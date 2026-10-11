"""Approval authorization: fail-closed gate with header or session proof.

Unset APPROVAL_TOKEN refuses every decision (an unconfigured server can
never apply code). With a token set, approve/reject accept the header
token or an operator session cookie from POST /api/auth/login — the
browser flow the UI uses, so the token never enters the frontend bundle.
Rejection through the full stack still leaves the patch unapplied.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.api.runs import require_approval
from app.config import settings


def _request(token: str | None = None, cookie: str | None = None) -> Request:
    headers = []
    if token is not None:
        headers.append((b"x-approval-token", token.encode()))
    if cookie is not None:
        headers.append((b"cookie", f"agent_session={cookie}".encode()))
    return Request(scope={"type": "http", "method": "POST", "headers": headers})


def test_unset_token_fails_closed(monkeypatch):
    monkeypatch.setattr(settings, "approval_token", "")
    for req in (_request(None), _request("anything")):
        with pytest.raises(HTTPException) as exc:
            require_approval(req)
        assert exc.value.status_code == 401


def test_token_mode_rejects_missing_and_wrong(monkeypatch):
    monkeypatch.setattr(settings, "approval_token", "s3cret-token")
    for req in (_request(None), _request("nope")):
        with pytest.raises(HTTPException) as exc:
            require_approval(req)
        assert exc.value.status_code == 401
        assert "s3cret" not in str(exc.value.detail)


def test_token_mode_accepts_correct_header(monkeypatch):
    monkeypatch.setattr(settings, "approval_token", "s3cret-token")
    require_approval(_request("s3cret-token"))  # no raise


def _authed_client(monkeypatch, token="s3cret-token") -> TestClient:
    from app.main import app

    monkeypatch.setattr(settings, "approval_token", token)
    client = TestClient(app)
    resp = client.post("/api/auth/login", json={"token": token})
    assert resp.status_code == 200
    assert resp.json() == {"authenticated": True}
    set_cookie = resp.headers.get("set-cookie", "")
    assert "agent_session=" in set_cookie and "httponly" in set_cookie.lower()
    return client


def test_login_rejects_wrong_token_without_cookie_or_oracle(monkeypatch):
    from app.main import app

    monkeypatch.setattr(settings, "approval_token", "s3cret-token")
    client = TestClient(app)
    for body in ({"token": "wrong"}, {"token": ""}, {}):
        resp = client.post("/api/auth/login", json=body)
        assert resp.status_code == 401
        assert "set-cookie" not in resp.headers
        assert "s3cret" not in resp.text


def test_login_fails_closed_when_unset(monkeypatch):
    from app.main import app

    monkeypatch.setattr(settings, "approval_token", "")
    resp = TestClient(app).post("/api/auth/login", json={"token": "anything"})
    assert resp.status_code == 401


def test_status_and_logout_flow(monkeypatch):
    from app.main import app

    monkeypatch.setattr(settings, "approval_token", "s3cret-token")
    client = TestClient(app)
    assert client.get("/api/auth/status").json() == {
        "authenticated": False, "token_required": True}
    authed = _authed_client(monkeypatch)
    assert authed.get("/api/auth/status").json()["authenticated"] is True
    assert authed.post("/api/auth/logout").status_code == 200
    assert authed.get("/api/auth/status").json()["authenticated"] is False


def test_session_cookie_authorizes_approve_endpoint(monkeypatch):
    monkeypatch.setattr(settings, "approval_token", "s3cret-token")
    calls: list[str] = []

    class FakeOrchestrator:
        async def apply_fix(self, run_id: str):
            calls.append(run_id)
            return SimpleNamespace(run_id=run_id, fix_status="applying",
                                   fix_branch="", fix_explanation="")

    monkeypatch.setattr("app.api.runs.orchestrator", FakeOrchestrator())
    from app.main import app

    anonymous = TestClient(app)
    assert anonymous.post("/api/runs/abc/fix/approve").status_code == 401
    assert anonymous.post("/api/runs/abc/fix/reject").status_code == 401
    assert calls == []  # refused before any fix logic ran
    authed = _authed_client(monkeypatch)
    assert authed.post("/api/runs/abc/fix/approve").status_code == 200
    assert calls == ["abc"]


@pytest.fixture()
def watched_workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    target = ws / "data.txt"
    target.write_text("buggy\n")
    return {"file": target, "artifacts": tmp_path / "artifacts"}


def test_reject_through_session_leaves_patch_unapplied(watched_workspace, monkeypatch):
    """Full-stack reject with a session cookie: rejected, worktree never
    created, source untouched."""
    from app.services.orchestrator import Orchestrator
    from app.services.session import session_store

    monkeypatch.setattr(settings, "approval_token", "s3cret-token")
    monkeypatch.setattr(settings, "artifact_dir", str(watched_workspace["artifacts"]))
    monkeypatch.setattr("app.services.orchestrator.save_text", lambda *a, **k: "")
    orch = Orchestrator()
    run = orch.create_run("http://localhost:8921", "verify sign in works please")
    run.fix_status = "awaiting_approval"
    run.fix_diff = "diff --git a/data.txt b/data.txt\n"
    session = session_store.create(run.run_id)
    try:
        from app.main import app

        client = TestClient(app)
        # Point the API at this orchestrator holding the awaiting run.
        monkeypatch.setattr("app.api.runs.orchestrator", orch)
        login = client.post("/api/auth/login", json={"token": "s3cret-token"})
        assert login.status_code == 200
        resp = client.post(f"/api/runs/{run.run_id}/fix/reject")
        assert resp.status_code == 200
        assert resp.json()["fix_status"] == "rejected"
    finally:
        session_store.sessions.pop(run.run_id, None)
    assert run.fix_worktree == ""
    assert (watched_workspace["file"]).read_text() == "buggy\n"
    assert list((watched_workspace["artifacts"] / "worktrees").glob("*")) == []
