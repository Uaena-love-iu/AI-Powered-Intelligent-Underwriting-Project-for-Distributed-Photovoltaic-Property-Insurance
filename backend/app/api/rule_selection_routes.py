from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict
from app.contracts import ProjectInfo
from app.rules.selection import CATALOG, CATALOG_VERSION, SaveScheme, Selection, list_schemes, save_scheme, resolve

router = APIRouter(tags=["rule-selection"])


@router.get("/api/v1/workbench/version")
def version():
    return {"name": "光伏核保工作台-v2", "version": "2.0.0", "rules_catalog": CATALOG_VERSION}


class Preview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project: ProjectInfo
    selection: Selection


@router.get("/api/v1/rule-selection")
def catalog():
    return dict(catalog=CATALOG, catalog_version=CATALOG_VERSION, schemes=list_schemes())


@router.post("/api/v1/rule-selection/preview")
def preview(request: Preview):
    try:
        snapshot = resolve(request.selection, request.project)
        return {k: snapshot[k] for k in ("rules", "scheme_name", "parameters", "snapshot_id")}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/api/v1/rule-selection/schemes", status_code=201)
def save(request: SaveScheme):
    try:
        return save_scheme(request)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
