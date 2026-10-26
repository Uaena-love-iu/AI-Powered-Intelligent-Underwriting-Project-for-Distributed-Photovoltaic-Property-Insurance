from io import BytesIO

from app.intake import MAX_FILES_PER_REQUEST
from app.main import app
from fastapi.testclient import TestClient
from PIL import Image

client = TestClient(app)


def png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (40, 30), color="white").save(buffer, format="PNG")
    return buffer.getvalue()


def test_inspects_multiple_files_and_marks_duplicate() -> None:
    content = png_bytes()

    response = client.post(
        "/api/v1/files/inspect",
        files=[
            ("files", ("first.png", content, "image/png")),
            ("files", ("second.png", content, "image/png")),
        ],
    )

    assert response.status_code == 200
    results = response.json()
    assert len(results) == 2
    assert results[0]["status"] == "accepted"
    assert results[0]["media_type"] == "image/png"
    assert results[0]["width"] == 40
    assert results[0]["height"] == 30
    assert results[1]["status"] == "duplicate"
    assert results[1]["issues"] == ["duplicate_file"]
    assert results[0]["sha256"] == results[1]["sha256"]


def test_rejects_corrupt_pdf() -> None:
    response = client.post(
        "/api/v1/files/inspect",
        files=[
            ("files", ("broken.pdf", b"%PDF-broken", "application/pdf")),
        ],
    )

    assert response.status_code == 200
    result = response.json()[0]
    assert result["status"] == "rejected"
    assert result["issues"] == ["corrupt_pdf"]


def test_rejects_requests_with_too_many_files() -> None:
    content = png_bytes()
    files = [
        ("files", (f"file-{index}.png", content, "image/png"))
        for index in range(MAX_FILES_PER_REQUEST + 1)
    ]

    response = client.post(
        "/api/v1/files/inspect",
        files=files,
    )

    assert response.status_code == 413
    assert response.json() == {
        "detail": f"A maximum of {MAX_FILES_PER_REQUEST} files is allowed"
    }


def test_openapi_marks_uploaded_files_as_binary() -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    openapi_schema = response.json()
    request_schema = openapi_schema["paths"]["/api/v1/files/inspect"]["post"][
        "requestBody"
    ]["content"]["multipart/form-data"]["schema"]
    component_name = request_schema["$ref"].rsplit("/", maxsplit=1)[-1]
    file_items = openapi_schema["components"]["schemas"][component_name]["properties"][
        "files"
    ]["items"]

    assert file_items["type"] == "string"
    assert file_items["format"] == "binary"
