import json
import sqlite3
from datetime import UTC, datetime
from hashlib import sha256

from app.contracts import ComponentProfile, Material, ProjectInfo
from app.providers.common import MaterialInput
from app.providers.component_workbench import ComponentWorkbenchProvider
from app.storage.cases import CaseRepository


def test_identical_nameplate_reuses_verified_identity_and_original_parameter_source(tmp_path):
    content = b"same photographed nameplate"
    digest = sha256(content).hexdigest()
    repository = CaseRepository(tmp_path / "cases.sqlite3")
    prior_profile = ComponentProfile(
        component_model="JAM72S20-450/MR", model_match_status="exact",
        rated_power_w=450, source_name="original datasheet",
        source_url=None, retrieved_at=datetime.now(UTC), match_confidence=1.0,
        lookup_notes=["original lookup"],
        workbench_result={"status": "parameters_mapped", "id": "old-run"},
    )
    saved = {
        "materials": [{"material_id": "old-photo", "category": "component_nameplate", "sha256": digest}],
        "component_profile": prior_profile.model_dump(mode="json"),
        "component_module_result": {"recognition": [{
            "material_id": "old-photo", "recognition_status": "models_verified",
            "candidates": [{"lookup_model": "JAM72S20-450/MR", "auto_query_eligible": True}],
        }]},
    }
    with sqlite3.connect(repository.database_path) as connection:
        repository._initialize(connection)
        connection.execute(
            "INSERT INTO cases(case_id,stored_at,review_status,case_json) VALUES(?,?,?,?)",
            ("old-case", "2026-10-16T00:00:00+00:00", "pending", json.dumps(saved)),
        )

    class NoNewLookup:
        rules_index = None
        reader = None

        def run(self, *args, **kwargs):
            raise AssertionError("Identical verified file should retain prior parameter evidence")

    material = Material(
        material_id="new-photo", category="component_nameplate", file_name="nameplate.jpg",
        media_type="image/jpeg", sha256=digest, quality_status="usable",
        quality_issues=[], parse_status="success",
    )
    project = ProjectInfo(
        project_name="test", insured_name="test", project_type="rooftop",
        installation_type="carport_roof", site_address=None,
        longitude=None, latitude=None, proposed_start_date=None, component_model=None,
    )
    provider = ComponentWorkbenchProvider(workbench=NoNewLookup(), case_repository=repository)
    profile = provider.lookup_materials(project, [MaterialInput(material, content)])
    assert profile.component_model == "JAM72S20-450/MR"
    assert profile.rated_power_w == 450
    assert profile.source_name == "original datasheet"
    assert profile.workbench_result["reused_from_case_id"] == "old-case"
    assert profile.workbench_result["recognition"][0]["material_id"] == "new-photo"
    assert repository.find_verified_component_for_file("0" * 64) is None

    conflicting_project = project.model_copy(update={"component_model": "OTHER-500"})
    assert provider.lookup_materials(conflicting_project, [MaterialInput(material, content)]) is None
    assert provider.last_result["status"] == "identity_conflict"
