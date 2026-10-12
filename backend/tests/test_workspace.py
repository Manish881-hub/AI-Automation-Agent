"""Per-run workspace selection and cross-application guard.

The codebase agent must search the run's own workspace — never another
app's repo — and a run targeting app B must never yield a patch for app A.
Clients select by server-side registry name; arbitrary paths are rejected.
"""

import pytest
from fastapi.testclient import TestClient

from app.config import resolve_workspace, settings
from app.schemas import FailureAnalysis, StepResult
from app.services.orchestrator import Orchestrator
from app.services.session import session_store


@pytest.fixture()
def registry(tmp_path, monkeypatch):
    repo_a = tmp_path / "repoA"
    repo_b = tmp_path / "repoB"
    (repo_a / "sub").mkdir(parents=True)
    (repo_b / "sub").mkdir(parents=True)
    (repo_a / "marker_a.py").write_text("alpha_marker = 1\n")
    (repo_b / "marker_b.py").write_text("beta_marker = 2\n")
    monkeypatch.setattr(settings, "workspace_allowlist", {
        "repoA": {"root": str(repo_a), "origin": "http://a.test"},
        "repoB": {"root": str(repo_b), "origin": "http://b.test"},
        "noorigin": {"root": str(repo_a)},
    })
    return repo_a, repo_b


def test_resolve_default_and_unknown(monkeypatch):
    monkeypatch.setattr(settings, "workspace_root", "/ws")
    monkeypatch.setattr(settings, "workspace_origin", "http://localhost:8921")
    assert resolve_workspace(None) == ("default", "/ws", "http://localhost:8921", {})
    assert resolve_workspace("default")[0] == "default"


def test_resolve_registry_and_failures(registry):
    name, root, origin, creds = resolve_workspace("repoA")
    assert (name, origin) == ("repoA", "http://a.test")
    assert root.endswith("repoA")
    assert creds == {}
    with pytest.raises(ValueError):
        resolve_workspace("no-such-app")
    with pytest.raises(ValueError):
        resolve_workspace("noorigin")  # origin required: fail closed
    settings.workspace_allowlist["ghost"] = {
        "root": "/nonexistent-dir-xyz", "origin": "http://g.test"}
    with pytest.raises(ValueError):
        resolve_workspace("ghost")


def test_origin_match_matrix():
    assert Orchestrator._workspace_matches_run(
        _fake_run("http://localhost:8921/", "http://localhost:8921")) is True
    assert Orchestrator._workspace_matches_run(
        _fake_run("http://localhost:3000/", "http://localhost:8921")) is False
    assert Orchestrator._workspace_matches_run(
        _fake_run("http://localhost:3000/", "")) is False
    assert Orchestrator._workspace_matches_run(
        _fake_run("not-a-url", "http://localhost:8921")) is False


def _fake_run(url, origin):
    from app.services.orchestrator import RunState

    return RunState(run_id="r", url=url, objective="o",
                    workspace_root="/ws", workspace_origin=origin)


def test_mismatch_refuses_before_any_llm_call(registry, monkeypatch):
    import asyncio

    monkeypatch.setattr("app.services.orchestrator.save_text", lambda *a, **k: "")
    orch = Orchestrator()

    class NoLLM:
        def scoped(self, budget):
            return self

        async def structured(self, *a, **k):
            raise AssertionError("fixer must not be called on mismatch")

    orch.llm = NoLLM()
    run = orch.create_run("http://localhost:3000/", "verify the dashboard please",
                          workspace="repoA")
    run.analysis = FailureAnalysis(failed=True, summary="s",
                                   probable_root_cause="401s on refresh")
    run.results = [StepResult(step_id=1, status="failed",
                              message="Expected 'alpha_marker' missing.")]
    session = session_store.create(run.run_id)
    try:
        asyncio.run(orch._propose_fix(run, session))
    finally:
        session_store.sessions.pop(run.run_id, None)
    assert run.fix_status == "workspace_mismatch"
    assert "localhost:3000" in run.fix_explanation
    assert run.fix_diff == ""
    assert run.fix_worktree == ""


def test_workspace_credentials_precedence(registry, monkeypatch):
    from app.agents.browser_agent import resolve_placeholders

    monkeypatch.setattr(settings, "test_email", "global@example.com")
    monkeypatch.setattr(settings, "test_password", "Global123!")
    settings.workspace_allowlist["creds"] = {
        "root": str(registry[0]), "origin": "http://c.test",
        "test_email": "taskflow-qa@example.com", "test_password": "TfQa123!",
    }
    orch = Orchestrator()
    run = orch.create_run("http://c.test/", "verify widgets load please",
                          workspace="creds")
    assert run.workspace_credentials == {
        "{{TEST_EMAIL}}": "taskflow-qa@example.com",
        "{{TEST_PASSWORD}}": "TfQa123!",
    }
    creds = Orchestrator.run_credentials(run)
    assert resolve_placeholders("{{TEST_EMAIL}} / {{TEST_PASSWORD}}", creds) == (
        "taskflow-qa@example.com / TfQa123!")
    # Without entry credentials the global demo account applies.
    run2 = orch.create_run("http://a.test/", "verify widgets load please",
                           workspace="repoA")
    creds2 = Orchestrator.run_credentials(run2)
    assert resolve_placeholders("{{TEST_EMAIL}}", creds2) == "global@example.com"
    for r in (run, run2):
        session_store.sessions.pop(r.run_id, None)


def test_resolved_secrets_never_persisted(registry, monkeypatch):
    """Plans and run records keep placeholders; resolved values exist only
    in memory at fill time."""
    import json

    monkeypatch.setattr(settings, "test_password", "Global123!")
    settings.workspace_allowlist["creds"] = {
        "root": str(registry[0]), "origin": "http://c.test",
        "test_password": "TfQa123!",
    }
    orch = Orchestrator()
    run = orch.create_run("http://c.test/", "verify widgets load please",
                          workspace="creds")
    try:
        blob = json.dumps({
            "plan": {"steps": [{"value": "{{TEST_PASSWORD}}"}]},
            "objective": run.objective,
            "workspace": run.workspace_name,
        })
        assert "TfQa123!" not in blob
        assert "{{TEST_PASSWORD}}" in blob
    finally:
        session_store.sessions.pop(run.run_id, None)


def test_default_workspace_refuses_foreign_app(monkeypatch):
    """Exact shape of the TaskFlow incident: a run targeting another app
    while the default (Acme demo) workspace is configured must park as
    workspace_mismatch — the Acme patch can never be proposed for it."""
    import asyncio

    monkeypatch.setattr(
        settings, "workspace_root",
        "/home/manishbhaktisagar/Downloads/ai-test-automation-agent/demo/autofix-app")
    monkeypatch.setattr(settings, "workspace_origin", "http://localhost:8921")
    monkeypatch.setattr("app.services.orchestrator.save_text", lambda *a, **k: "")
    orch = Orchestrator()

    class NoLLM:
        def scoped(self, budget):
            return self

        async def structured(self, *a, **k):
            raise AssertionError("fixer must not be called on mismatch")

    orch.llm = NoLLM()
    run = orch.create_run("http://localhost:3000/",
                          "verify the dashboard please")
    assert run.workspace_name == "default"
    run.analysis = FailureAnalysis(failed=True, summary="s",
                                   probable_root_cause="401s on refresh")
    from app.schemas import StepResult
    run.results = [StepResult(step_id=1, status="failed",
                              message="login failed: KeyError: 'token'.")]
    session = session_store.create(run.run_id)
    try:
        asyncio.run(orch._propose_fix(run, session))
    finally:
        session_store.sessions.pop(run.run_id, None)
    assert run.fix_status == "workspace_mismatch"
    assert run.fix_diff == ""


def test_per_run_isolation_no_cross_repo_leak(registry):
    import asyncio

    orch = Orchestrator()
    run_a = orch.create_run("http://a.test/", "verify widgets load please",
                            workspace="repoA")
    run_a.analysis = FailureAnalysis(failed=True, summary="s")
    run_a.results = [StepResult(step_id=1, status="failed",
                                message="Expected 'alpha_marker' missing.")]
    run_b = orch.create_run("http://b.test/", "verify widgets load please",
                            workspace="repoB")
    run_b.analysis = FailureAnalysis(failed=True, summary="s")
    run_b.results = [StepResult(step_id=1, status="failed",
                                message="Expected 'beta_marker' missing.")]
    try:
        ctx_a = asyncio.run(orch._investigate_codebase(run_a))
        ctx_b = asyncio.run(orch._investigate_codebase(run_b))
    finally:
        for run in (run_a, run_b):
            session_store.sessions.pop(run.run_id, None)
    assert any("marker_a" in f for f in ctx_a["files"])
    assert not any("marker_b" in f for f in ctx_a["files"])
    assert any("marker_b" in f for f in ctx_b["files"])
    assert not any("marker_a" in f for f in ctx_b["files"])


def test_unknown_workspace_rejected_at_api(registry, monkeypatch):
    from app.main import app
    from app.api import runs as runs_module

    async def _no_execute(run_id):
        return None

    monkeypatch.setattr(settings, "approval_token", "op-token")
    monkeypatch.setattr(runs_module.orchestrator, "execute", _no_execute)
    resp = TestClient(app).post(
        "/api/runs",
        json={"url": "http://localhost:8921/",
              "objective": "verify that a user can sign in and reach the dashboard"},
        headers={"X-Approval-Token": "op-token"})
    assert resp.status_code == 202
    assert resp.json()["workspace_name"] == "default"
    runs_module.orchestrator.runs.pop(resp.json()["run_id"], None)
    resp2 = TestClient(app).post(
        "/api/runs",
        json={"url": "http://localhost:8921/",
              "objective": "verify that a user can sign in and reach the dashboard",
              "workspace": "no-such-app"},
        headers={"X-Approval-Token": "op-token"})
    assert resp2.status_code == 422


def test_named_workspace_accepted_at_api(registry, monkeypatch):
    from app.main import app
    from app.api import runs as runs_module

    async def _no_execute(run_id):
        return None

    monkeypatch.setattr(settings, "approval_token", "op-token")
    monkeypatch.setattr(runs_module.orchestrator, "execute", _no_execute)
    resp = TestClient(app).post(
        "/api/runs",
        json={"url": "http://a.test/",
              "objective": "verify widgets load please",
              "workspace": "repoA"},
        headers={"X-Approval-Token": "op-token"})
    assert resp.status_code == 202
    body = resp.json()
    assert body["workspace_name"] == "repoA"
    runs_module.orchestrator.runs.pop(body["run_id"], None)
