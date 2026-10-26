"""Explicit approval of exact-model source parameters, with immutable versions."""
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from pydantic import AwareDatetime, Field, field_validator, model_validator

from app.contracts.common import ContractModel
from app.storage.verified_components import VerifiedComponentRepository, VerifiedComponentConflict

router = APIRouter(prefix="/api/v1/components/verified", tags=["verified-component-parameters"])


class Parameter(ContractModel):
    value: float = Field(gt=0, allow_inf_nan=False)
    source_url: str = Field(max_length=2000)
    quote: str = Field(min_length=2, max_length=2000)
    load_basis: Literal["test", "design", "unspecified"] = "unspecified"

    @field_validator("source_url")
    @classmethod
    def source_https(cls, value):
        url = urlparse(value)
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("请填写不含登录信息的 HTTPS 来源链接")
        return value


class Approval(ContractModel):
    component_model: str = Field(min_length=2, max_length=100)
    manufacturer: str = Field(min_length=2, max_length=120)
    document_title: str = Field(min_length=2, max_length=200)
    applicability: str = Field(min_length=2, max_length=2000)
    reason: str = Field(min_length=2, max_length=2000)
    reviewer_name: str = Field(min_length=1, max_length=100)
    valid_from: AwareDatetime
    expires_at: AwareDatetime
    exact_model_verified: Literal[True]
    parameters: dict[Literal["rated_power_w", "front_static_load_pa", "back_static_load_pa"], Parameter] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def validate_interval(self):
        if self.expires_at <= self.valid_from:
            raise ValueError("到期时间必须晚于生效时间")
        if any(c in self.component_model for c in "*?\n\r"):
            raise ValueError("只支持完整型号，不支持通配符或型号系列")
        return self


class Revocation(ContractModel):
    reviewer_name: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=2, max_length=2000)


def actor(request, fallback):
    user = getattr(request.state, "auth_user", None)
    return user.username if user else fallback


@router.get("")
def list_verified():
    return {"records": VerifiedComponentRepository().list()}


@router.post("", status_code=201)
def approve(body: Approval, request: Request):
    try:
        return VerifiedComponentRepository().create(body, actor(request, body.reviewer_name))
    except VerifiedComponentConflict as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/{record_id}/revoke")
def revoke(record_id: str, body: Revocation, request: Request):
    try:
        return VerifiedComponentRepository().revoke(record_id, actor(request, body.reviewer_name), body.reason)
    except KeyError as exc:
        raise HTTPException(404, "记录不存在") from exc
    except VerifiedComponentConflict as exc:
        raise HTTPException(409, str(exc)) from exc
