"""The web API over real HTTP requests (FastAPI's test client)."""

import pytest
from fastapi.testclient import TestClient

from intake.readers import MAX_BYTES
from intake.web import create_app

from .conftest import SAMPLES, TODAY, make_docx


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app(today=lambda: TODAY)) as c:
        yield c


def test_page_is_served_with_strict_security_headers(client):
    response = client.get("/")
    assert response.status_code == 200 and "Drop a CV here" in response.text
    csp = response.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["cache-control"] == "no-store"


def test_info_lists_the_samples_and_the_extractor(client):
    info = client.get("/api/info").json()
    assert info["extractor"] == "rules"
    assert [s["name"] for s in info["samples"]] == ["amara-osei.pdf", "tom-brennan.docx", "sofia-marlowe.txt",
                                                     "rowan-hale.txt"]


def test_a_bundled_sample_can_be_extracted(client):
    data = client.post("/api/samples/rowan-hale.txt").json()
    assert data["candidate"]["name"]["value"] == "Rowan Hale"
    assert data["needs_review"] is True and len(data["review"]) == 5


def test_sample_names_are_a_fixed_list(client):
    assert client.post("/api/samples/..%2Fintake%2Fweb.py").status_code == 404
    assert client.post("/api/samples/passwd").status_code == 404


@pytest.mark.parametrize("name", ["amara-osei.pdf", "tom-brennan.docx", "sofia-marlowe.txt"])
def test_uploads_of_each_file_type(client, name):
    response = client.post("/api/extract", files={"file": (name, (SAMPLES / name).read_bytes())})
    assert response.status_code == 200
    body = response.json()
    assert body["source"]["filename"] == name and body["candidate"]["name"]["status"] == "found"


def test_upload_filename_is_reduced_to_its_base_name(client):
    data = make_docx(["Jordan Example", "jordan@example.com", "Support Analyst at Example Ltd 2020 - 2022"])
    body = client.post("/api/extract", files={"file": ("../../etc/cv.docx", data)}).json()
    assert body["source"]["filename"] == "cv.docx"


def test_unreadable_upload_gets_a_422_with_a_reason(client):
    response = client.post("/api/extract", files={"file": ("cv.doc", b"\xd0\xcf\x11\xe0" + b"\0" * 64)})
    assert response.status_code == 422 and "Old Word .doc" in response.json()["detail"]


def test_oversized_upload_is_refused(client):
    response = client.post("/api/extract", files={"file": ("big.txt", b"a" * (MAX_BYTES + 1))})
    assert response.status_code == 413


def test_missing_file_is_a_validation_error(client):
    assert client.post("/api/extract").status_code == 422
