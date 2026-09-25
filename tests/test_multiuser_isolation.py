"""What one user can reach of another's: uploads, jobs, and the pdfmd folder.

The Hermes-side isolation (memory, sessions) is covered by
test_session_isolation / test_hermes_home_isolation. These cover the parts
specific to this service: the upload folder the `pdfmd` tools read, and the
job registry `/v1/chat` runs turns through.
"""

from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

import app.api as api
from agents.user_profiles import reset_seed_cache
from plugins.pdfmd import _pdf

TOKEN = "test-gateway-token"
PDF = b"%PDF-1.4\n% test\n"


def _headers(user: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}", "X-User-Id": user}


class _StubHost:
    ready = True

    def __init__(self) -> None:
        self.release = threading.Event()
        self.release.set()

    def readiness(self) -> dict:
        return {"ready": True, "backend": "stub"}

    def initialize(self) -> dict:
        return self.readiness()

    def chat(self, message, *, session_id=None, reset_session=False, profile=None):
        self.release.wait(5)
        return {
            "success": True,
            "response": f"{profile.slug} uchun maxfiy javob",
            "session_id": session_id or "s",
        }


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_USERS_HOME", str(tmp_path / "users"))
    monkeypatch.setenv("PDF_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GATEWAY_TOKEN", TOKEN)
    monkeypatch.delenv("HERMES_REQUIRE_USER_ID", raising=False)
    monkeypatch.delenv("CHAT_WAIT_SECONDS", raising=False)
    reset_seed_cache()
    api._limiter.reset()
    yield
    reset_seed_cache()


@pytest.fixture
def host(monkeypatch):
    stub = _StubHost()
    monkeypatch.setattr("agents.hermes_host.get_hermes_host", lambda: stub)
    return stub


@pytest.fixture
def client(host):
    return TestClient(api.create_app())


# --- uploads ---------------------------------------------------------------

def test_uploads_land_in_the_callers_own_folder(client, tmp_path):
    r = client.post("/v1/files?name=reliz.pdf", content=PDF, headers=_headers("shohruz"))
    assert r.status_code == 200
    assert (tmp_path / "data" / "users" / "shohruz" / "pdf" / "reliz.pdf").is_file()
    assert not (tmp_path / "data" / "pdf").exists()


def test_file_listing_shows_only_the_callers_files(client):
    client.post("/v1/files?name=shohruz.pdf", content=PDF, headers=_headers("shohruz"))
    client.post("/v1/files?name=aziza.pdf", content=PDF, headers=_headers("aziza"))

    mine = client.get("/v1/files", headers=_headers("aziza")).json()
    assert mine["pdf"] == ["aziza.pdf"]


def test_same_file_name_does_not_overwrite_another_user(client, tmp_path):
    client.post("/v1/files?name=reliz.pdf", content=PDF + b"A", headers=_headers("shohruz"))
    client.post("/v1/files?name=reliz.pdf", content=PDF + b"B", headers=_headers("aziza"))

    users = tmp_path / "data" / "users"
    assert (users / "shohruz" / "pdf" / "reliz.pdf").read_bytes().endswith(b"A")
    assert (users / "aziza" / "pdf" / "reliz.pdf").read_bytes().endswith(b"B")


def test_upload_requires_the_user_header(client):
    r = client.post(
        "/v1/files?name=x.pdf",
        content=PDF,
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert r.status_code == 400


def test_multipart_chat_stores_the_attachment_per_user(client, tmp_path):
    r = client.post(
        "/v1/chat",
        data={"message": "tekshir"},
        files={"file": ("reliz.pdf", PDF, "application/pdf")},
        headers=_headers("shohruz"),
    )
    assert r.status_code == 200
    assert r.json()["files"] == ["reliz.pdf"]
    assert (tmp_path / "data" / "users" / "shohruz" / "pdf" / "reliz.pdf").is_file()


# --- jobs ------------------------------------------------------------------

def test_another_user_cannot_collect_a_job_by_id(client):
    r = client.post(
        "/v1/chat",
        json={"message": "savol", "session_id": "chat-1"},
        headers=_headers("shohruz"),
    )
    job_id = r.json()["job_id"]
    assert client.get(f"/v1/jobs/{job_id}", headers=_headers("shohruz")).status_code == 200
    assert client.get(f"/v1/jobs/{job_id}", headers=_headers("aziza")).status_code == 404


def test_another_user_cannot_find_a_job_by_session(client):
    client.post(
        "/v1/chat",
        json={"message": "savol", "session_id": "chat-1"},
        headers=_headers("shohruz"),
    )
    r = client.get("/v1/jobs?session_id=chat-1", headers=_headers("aziza"))
    assert r.json()["job"] is None


def test_replaying_a_running_request_does_not_join_another_users_turn(
    client, host, monkeypatch
):
    """The retry-attach path must not become a way to read someone's answer."""
    monkeypatch.setenv("CHAT_WAIT_SECONDS", "0.2")
    host.release.clear()
    body = {"message": "maxfiy savol", "session_id": "chat-1"}
    first = client.post("/v1/chat", json=body, headers=_headers("shohruz")).json()
    second = client.post("/v1/chat", json=body, headers=_headers("aziza")).json()
    host.release.set()

    assert first["status"] == "running" and second["status"] == "running"
    assert first["job_id"] != second["job_id"]

    monkeypatch.setenv("CHAT_WAIT_SECONDS", "0")
    done = client.post("/v1/chat", json=body, headers=_headers("aziza")).json()
    assert "aziza" in (done["response"] or "")
    assert "shohruz" not in (done["response"] or "")


# --- pdfmd folder resolution ------------------------------------------------

def test_pdfmd_reads_the_folder_of_the_active_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(
        _pdf, "_active_hermes_home", lambda: tmp_path / "users" / "shohruz"
    )
    assert _pdf.data_root() == (tmp_path / "data" / "users" / "shohruz").resolve()


def test_pdfmd_fails_closed_without_a_per_user_home(tmp_path, monkeypatch):
    """No override reached the tool: refuse, never fall back to a shared folder."""
    monkeypatch.setattr(_pdf, "_active_hermes_home", lambda: tmp_path / ".hermes")
    with pytest.raises(_pdf.PdfError):
        _pdf.data_root()

    monkeypatch.setattr(_pdf, "_active_hermes_home", lambda: None)
    with pytest.raises(_pdf.PdfError):
        _pdf.data_root()


def test_pdfmd_cannot_open_another_users_file(tmp_path, monkeypatch):
    other = tmp_path / "data" / "users" / "aziza" / "pdf"
    other.mkdir(parents=True)
    (other / "reliz.pdf").write_bytes(PDF)
    monkeypatch.setattr(
        _pdf, "_active_hermes_home", lambda: tmp_path / "users" / "shohruz"
    )

    for name in ("reliz.pdf", "../aziza/pdf/reliz.pdf", str(other / "reliz.pdf")):
        with pytest.raises(_pdf.PdfError):
            _pdf.resolve_pdf(name)
