from pathlib import Path

from scripts.audit_competition_packages import audit_packages


def test_audit_checks_a_b_c_images_certificate_and_official_rules(tmp_path: Path) -> None:
    outbound = tmp_path / "外发"
    for suite, count in (("A", 5), ("B", 5), ("C", 5)):
        directory = outbound / suite
        directory.mkdir(parents=True)
        for index in range(count):
            (directory / f"photo-{index}.jpg").write_bytes(b"image")
        (directory / "电站备案证.pdf").write_bytes(b"pdf")
    (tmp_path / "官方业务规则.xlsx").write_bytes(b"xlsx")
    (outbound / "A" / ".DS_Store").write_bytes(b"metadata")

    report = audit_packages(tmp_path)

    assert report["all_automated_file_gates_passed"] is True
    assert report["official_rules_found_by_filename"] is True
    assert report["rule_content_verified"] is False
    assert report["packages"]["A"]["image_count"] == 5
    assert report["packages"]["A"]["automated_file_gate_passed"] is True
    assert "人工标注" in report["packages"]["A"]["panorama_views"]


def test_audit_flags_missing_package_and_short_image_set(tmp_path: Path) -> None:
    directory = tmp_path / "外发" / "A"
    directory.mkdir(parents=True)
    for index in range(3):
        (directory / f"{index}.jpg").write_bytes(b"image")

    report = audit_packages(tmp_path)

    assert report["all_automated_file_gates_passed"] is False
    assert report["packages"]["A"]["image_count"] == 3
    assert report["packages"]["A"]["automated_file_gate_passed"] is False
    assert report["packages"]["C"]["directory_exists"] is False


def test_audit_never_treats_image_names_as_view_annotations(tmp_path: Path) -> None:
    directory = tmp_path / "A"
    directory.mkdir()
    for index in range(5):
        (directory / f"panorama-{index}.jpg").write_bytes(b"image")
    (directory / "备案证.pdf").write_bytes(b"pdf")

    report = audit_packages(tmp_path)

    assert report["packages"]["A"]["panorama_views"].startswith("需人工标注")
