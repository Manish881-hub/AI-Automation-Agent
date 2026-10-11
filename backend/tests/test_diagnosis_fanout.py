"""Post-debug multi-agent coordination stays bounded and read-only."""

import asyncio

import pytest

from app.config import settings
from app.schemas import FailureAnalysis
from app.services.orchestrator import Orchestrator
from app.services.session import session_store


class FakeReporter:
    def run(self, *_args, **_kwargs):
        return "# deterministic report"


@pytest.fixture()
def inline_worker(monkeypatch):
    """Keep unit tests deterministic; production still uses worker threads."""
    async def run_inline(fn, /, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr("app.services.orchestrator.asyncio.to_thread", run_inline)


def test_diagnosis_fanout_caches_readonly_codebase_context(monkeypatch, tmp_path, inline_worker):
    """Reporter and source scout join before the fixer can consume context."""
    monkeypatch.setattr(settings, "artifact_dir", str(tmp_path / "artifacts"))
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path))
    monkeypatch.setattr(settings, "fix_enabled", True)
    monkeypatch.setattr("app.services.orchestrator.save_text", lambda *args, **kwargs: "")

    def investigate(_self, _tool, _results, _analysis):
        return {"files": {"backend/auth.py": "redacted source"}}

    monkeypatch.setattr("app.services.orchestrator.CodebaseAgent.investigate", investigate)
    orch = Orchestrator()
    run = orch.create_run("http://localhost:8921", "verify sign in works please")
    run.analysis = FailureAnalysis(failed=True, summary="login failed")
    session = session_store.create(run.run_id)
    try:
        asyncio.run(orch._fan_out_diagnosis(run, session, FakeReporter()))
    finally:
        session_store.sessions.pop(run.run_id, None)

    assert run.report == "# deterministic report"
    assert run.codebase_context == {"files": {"backend/auth.py": "redacted source"}}
    assert any("diagnosis_fanout_started reporter,codebase" in event for event in run.events)
    assert any("code_located backend/auth.py" in event for event in run.events)
    assert any("diagnosis_fanout_completed reporter,codebase" in event for event in run.events)


def test_diagnosis_fanout_keeps_report_when_codebase_scout_fails(monkeypatch, tmp_path, inline_worker):
    monkeypatch.setattr(settings, "artifact_dir", str(tmp_path / "artifacts"))
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path))
    monkeypatch.setattr(settings, "fix_enabled", True)
    monkeypatch.setattr("app.services.orchestrator.save_text", lambda *args, **kwargs: "")

    def investigate(*_args, **_kwargs):
        raise RuntimeError("source unavailable")

    monkeypatch.setattr("app.services.orchestrator.CodebaseAgent.investigate", investigate)
    orch = Orchestrator()
    run = orch.create_run("http://localhost:8921", "verify sign in works please")
    run.analysis = FailureAnalysis(failed=True, summary="login failed")
    session = session_store.create(run.run_id)
    try:
        asyncio.run(orch._fan_out_diagnosis(run, session, FakeReporter()))
    finally:
        session_store.sessions.pop(run.run_id, None)

    assert run.report == "# deterministic report"
    assert run.codebase_context is None
    assert any("investigate_failed source unavailable" in event for event in run.events)
