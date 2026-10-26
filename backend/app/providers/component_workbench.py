"""Translate workbench evidence without treating mechanical mapping as design data."""

import os
from hashlib import sha256

from app.component_workbench_runtime import runtime
from app.contracts import ComponentProfile
from app.providers.common import ProviderError
from app.providers.component import ComponentProvider

VALUES = (
    "rated_power_w",
    "wind_load_pa", "snow_load_pa", "front_static_load_pa", "back_static_load_pa",
)


class ComponentWorkbenchProvider(ComponentProvider):
    def __init__(self, workbench=None, case_repository=None):
        self.last_result = {}
        self.workbench = workbench if workbench is not None else runtime().bench
        self.case_repository = case_repository
        from app.storage.verified_components import VerifiedComponentRepository
        self.verified_parameters = VerifiedComponentRepository(
            case_repository.database_path if case_repository is not None else None)

    @property
    def evidence_cache_token(self):
        return self.verified_parameters.cache_token()

    @property
    def name(self):
        return "component-model-workbench"

    def lookup_materials(self, project, materials):
        from recognition import compact_key, identify
        results, models, prior_profiles = [], {}, {}
        for item in materials:
            if item.material.category.value not in {"component_nameplate", "equipment_inventory"}:
                continue
            cached = None
            if self.case_repository is not None and item.material.category.value == "component_nameplate":
                digest = sha256(item.content).hexdigest()
                recorded_digest = getattr(item.material, "sha256", None)
                if not recorded_digest or recorded_digest.lower() == digest:
                    cached = self.case_repository.find_verified_component_for_file(digest)
            try:
                if cached is not None:
                    result = dict(cached["recognition"])
                    result["notes"] = [*result.get("notes", []),
                                       f"同一文件的型号识别复用案件 {cached['case_id']} 的已核验证据"]
                    prior_profiles[item.material.material_id] = cached
                elif item.material.file_name.lower().endswith(".xlsx"):
                    result = self._identify_inventory(item)
                else:
                    result = identify(item.content, item.material.file_name,
                                      self.workbench.rules_index, self.workbench.reader)
            except (ValueError, RuntimeError, OSError):
                result = {"recognition_status": "source_unavailable", "candidates": [],
                          "filename": item.material.file_name, "notes": ["组件材料识别失败，保留证据缺口"]}
            result["material_id"] = item.material.material_id
            results.append(result)
            for candidate in result.get("candidates", []):
                if candidate.get("auto_query_eligible"):
                    model = candidate.get("lookup_model") or candidate["raw_model"]
                    models[compact_key(model)] = model
        requested = project.component_model
        if results:
            if requested and compact_key(requested) not in models:
                state = "identity_conflict" if models else "identity_unconfirmed"
            elif not requested and len(models) == 1:
                requested = next(iter(models.values()))
                state = "exact"
            else:
                state = "exact" if requested else "multiple_models" if models else "identity_unconfirmed"
        else:
            state = "exact" if requested else "identity_unconfirmed"
        if state != "exact":
            self.last_result = {"status": state, "profile": None, "recognition": results,
                                "candidate_models": list(models.values())}
            return None
        from app.storage.verified_components import model_key as exact_model_key
        if self.verified_parameters.list(requested) and results:
            identified = [candidate.get("lookup_model") or candidate.get("raw_model", "")
                          for result in results for candidate in result.get("candidates", [])
                          if candidate.get("auto_query_eligible")]
            if not identified or any(exact_model_key(model) != exact_model_key(requested) for model in identified):
                self.last_result = {"status": "identity_conflict", "profile": None, "recognition": results,
                                    "notes": ["已核验参数仅适用于单一完整型号；本轮材料型号不同或存在多个型号，请人工核对。"]}
                return None
        # A managed model always goes through the current approval registry. Never
        # resurrect an expired/revoked approval from an old case's profile.
        if (len(models) == 1 and len(prior_profiles) == 1
                and not self.verified_parameters.list(requested)):
            cached = next(iter(prior_profiles.values()))
            prior_model = cached["profile"].get("component_model", "")
            verified_models = [candidate.get("lookup_model") or candidate.get("raw_model", "")
                               for candidate in cached["recognition"].get("candidates", [])
                               if candidate.get("auto_query_eligible")]
            if not cached["profile"].get("workbench_result", {}).get("verified_reuse") and compact_key(prior_model) == compact_key(requested) and all(
                compact_key(model) == compact_key(requested) for model in verified_models
            ):
                profile = ComponentProfile.model_validate(cached["profile"])
                self.last_result = {**profile.workbench_result,
                                    "recognition": results,
                                    "reused_from_case_id": cached["case_id"]}
                profile.workbench_result = self.last_result
                profile.lookup_notes = [*profile.lookup_notes,
                                        f"相同 SHA-256 文件复用案件 {cached['case_id']} 的原参数证据；本轮未重新查询"]
                return profile
        profile = self.lookup(requested)
        self.last_result["recognition"] = results
        if profile is not None:
            profile.workbench_result = self.last_result
        return profile

    def _identify_inventory(self, item):
        from recognition import candidate_kind, text_candidates

        from app.intake.equipment_inventory import parse_equipment_inventory

        rows = parse_equipment_inventory(
            material_id=item.material.material_id, content=item.content,
        )
        candidates = []
        for row in rows:
            # Read literal cells only; the legacy parser's inferred model/power
            # is intentionally not used. Workbench rules determine candidates.
            for candidate in text_candidates(row.specification or "", self.workbench.rules_index):
                kind, note, model = candidate_kind(candidate, self.workbench.rules_index)
                candidate.update(
                    lookup_model=model, candidate_kind=kind,
                    auto_query_eligible=kind == "complete", note=note,
                    worksheet_name=row.worksheet_name, row_number=row.row_number,
                    quote=row.specification,
                )
                candidates.append(candidate)
        return {
            "filename": item.material.file_name, "recognition_version": "xlsx-literal-v1",
            "recognition_status": "recognized" if candidates else "identity_unconfirmed",
            "candidates": candidates,
            "notes": ["设备清单按原文单元格读取，型号格式由组件工作台规则核验"],
        }

    def lookup(self, component_model):
        verified = self.verified_parameters.lookup(component_model) if component_model else None
        if verified is not None:
            self.last_result = verified.workbench_result
            return verified
        self.last_result = {"status": "source_unavailable", "profile": None}
        try:
            from pv_component_lookup.naming import model_key
            agent = os.getenv("PV_COMPONENT_WORKBENCH_AGENT_ENABLED", "true").lower()
            result = self.workbench.run(
                component_model, strategy="auto",
                use_agent=agent in {"1", "true", "yes", "on"},
            )
            self.last_result = result
            raw = result.get("profile")
            if not raw:
                return None
            exact = raw.get("model_match_status") == "exact" and (
                model_key(raw["component_model"])
                == model_key(component_model)
            )
            # The workbench's ui_fields may contain directional mechanical mappings.
            # Only original contract fields enter the underwriting calculation.
            values = {}
            evidence = {}
            for field in VALUES:
                records = raw.get("parameter_evidence", {}).get(field, [])
                conflict = any(r.get("status") == "conflict" for r in records)
                values[field] = raw.get(field) if exact and not conflict else None
                if records:
                    evidence[field] = {
                        "status": "conflict" if conflict else "found" if values[field] else "unknown",
                        "value": values[field], "records": records,
                    }
            payload = {key: raw[key] for key in (
                "manufacturer", "market_version", "source_document_type", "source_url",
                "source_name", "retrieved_at", "match_confidence", "parameter_sources",
                "lookup_notes",
            ) if key in raw}
            return ComponentProfile(
                component_model=component_model,
                model_match_status="exact" if exact else "unconfirmed",
                workbench_run_id=result.get("id"),
                workbench_result=result,
                parameter_evidence=evidence,
                **payload, **values,
            )
        except (ValueError, RuntimeError, OSError) as exc:
            raise ProviderError("Component workbench lookup failed") from exc
