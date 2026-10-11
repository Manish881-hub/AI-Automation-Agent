"""Run cancellation: endpoint mapping, cooperative boundaries, safe stops.

Lifecycle tests use deterministic fakes (no LLM quota, no real browser):
a fake browser tool, a stub LLM serving a fixed plan, and the real
orchestrator pipeline. Subprocess and API-mapping tests use the real
TestRunner and TestClient respectively.
"""

import asyncio
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.schemas import (
    FailureAnalysis, RecoveryDecision, TestPlan, TestStep,
)
from app.services.orchestrator import Orchestrator, RunCancelled, throw_if_cancelled
from app.services.history import read_history
from app.services.session import session_store
from app.schemas import WebsiteSnapshot
from app.tools.browser import BrowserObservation
from app.tools.testrunner import TestRunnerTool

OBJECTIVE = "verify the demo store shows widgets"


def _plan() -> TestPlan:
    return TestPlan.model_validate({"steps": [
        {"id": 1, "action": "navigate", "target": "http://x/"},
        {"id": 2, "action": "assert", "assertion_type": "text",
         "target": "Welcome", "expected": "widgets here"},
        {"id": 3, "action": "assert", "assertion_type": "text",
         "target": "Welcome", "expected": "widgets here"},
        {"id": 4, "action": "assert", "assertion_type": "text",
         "target": "Welcome", "expected": "widgets here"},
    ]})


class FakeBrowser:
    def __init__(self, run=None, cancel_after=None):
        self.started = False
        self.closed = False
        self.executes = 0
        self._run = run
        self._cancel_after = cancel_after

    async def start(self):
        self.started = True

    async def close(self):
        self.closed = True

    async def navigate(self, url):
        pass

    async def snapshot(self, shot):
        return WebsiteSnapshot(url="http://x/", title="Widget Store",
                               buttons=["Go"], inputs=[], links=[])

    async def click(self, target):
        pass

    async def fill(self, target, value):
        pass

    async def press(self, target, value):
        pass

    async def ensure_visible(self, target):
        return True

    async def observe(self, shot):
        self.executes += 1
        if (self._run is not None and self._cancel_after is not None
                and self.executes >= self._cancel_after):
            self._run.cancel_requested = True
            self._run.cancel_event.set()
        return BrowserObservation(url="http://x/", title="Widget Store",
                                  text="Welcome widgets here demo store")

    async def available_targets(self):
        return []


class FakeLLM:
    def __init__(self, plan=None, fail_planner_with=None):
        self.plan = plan or _plan()
        self.fail_planner_with = fail_planner_with

    def scoped(self, budget):
        return self

    async def structured(self, system, user, schema):
        if schema is TestPlan:
            if self.fail_planner_with is not None:
                raise self.fail_planner_with
            return self.plan
        if schema is FailureAnalysis:
            return FailureAnalysis(failed=False, summary="all green")
        if schema is RecoveryDecision:
            return RecoveryDecision(action="abort", reason="nope")
        raise AssertionError(f"unexpected schema {schema}")

    async def text(self, system, user):
        return ""


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "artifact_dir", str(tmp_path))
    monkeypatch.setattr("app.services.orchestrator.save_text", lambda *a, **k: "")
    return tmp_path


def _orch_with_fake_llm(plan=None, fail_planner_with=None):
    orch = Orchestrator()
    orch.llm = FakeLLM(plan=plan, fail_planner_with=fail_planner_with)
    return orch


def test_boundary_check_raises_with_stage():
    orch = Orchestrator()
    run = orch.create_run("http://x/", "verify widgets load please")
    run.cancel_requested = True
    try:
        with pytest.raises(RunCancelled) as exc:
            throw_if_cancelled(run, "execution")
        assert exc.value.stage == "execution"
    finally:
        session_store.sessions.pop(run.run_id, None)


def test_cancel_during_execution_closes_browser_and_stops(isolated, monkeypatch):
    fake = FakeBrowser()
    monkeypatch.setattr("app.services.orchestrator.BrowserTool", lambda: fake)
    orch = _orch_with_fake_llm()
    run = orch.create_run("http://x/", OBJECTIVE)
    fake._run = run
    fake._cancel_after = 2
    asyncio.run(orch.execute(run.run_id))
    assert run.status == "cancelled"
    assert fake.started and fake.closed  # resources released
    assert len(run.results) == 2  # no new work after the flag
    assert run.error_stage == "execution"
    assert run.error_kind == "cancelled"
    assert run.objective_status == "unknown"  # never judged, never minted
    assert run.events[-1].startswith("orchestrator: run_cancelled")
    rows = read_history()
    assert any(r["run_id"] == run.run_id and r["transition"] == "run_cancelled"
               for r in rows)
    session_store.sessions.pop(run.run_id, None)


def test_late_timeout_becomes_cancelled_not_failed(isolated, monkeypatch):
    fake = FakeBrowser()
    monkeypatch.setattr("app.services.orchestrator.BrowserTool", lambda: fake)

    class HangingLLM(FakeLLM):
        async def structured(self, system, user, schema):
            if schema is TestPlan:
                self.run_ref.cancel_requested = True  # operator asked mid-call
                self.run_ref.cancel_event.set()
                raise asyncio.TimeoutError()
            return await super().structured(system, user, schema)

    orch = Orchestrator()
    llm = HangingLLM()
    orch.llm = llm
    run = orch.create_run("http://x/", OBJECTIVE)
    llm.run_ref = run
    asyncio.run(orch.execute(run.run_id))
    assert run.status == "cancelled"
    assert run.error_stage == "planning"
    assert run.objective_status == "unknown"
    session_store.sessions.pop(run.run_id, None)


def test_completed_run_without_cancel_still_completes(isolated, monkeypatch):
    fake = FakeBrowser()
    monkeypatch.setattr("app.services.orchestrator.BrowserTool", lambda: fake)
    orch = _orch_with_fake_llm()
    run = orch.create_run("http://x/", OBJECTIVE)
    asyncio.run(orch.execute(run.run_id))
    assert run.status == "completed"
    assert run.objective_status == "passed"
    assert len(run.results) == 4
    assert fake.closed
    session_store.sessions.pop(run.run_id, None)


def test_pytest_cancel_terminates_subprocess(tmp_path):
    (tmp_path / "test_slow.py").write_text(
        "import time\ndef test_slow():\n    time.sleep(30)\n")
    tool = TestRunnerTool(tmp_path, timeout=120)
    event = threading.Event()
    result: dict = {}
    thread = threading.Thread(
        target=lambda: result.update(tool.run_pytest([], event)))
    thread.start()
    time.sleep(1.0)
    event.set()
    thread.join(timeout=30)
    assert not thread.is_alive()
    assert result["cancelled"] is True
    assert result["passed"] is False  # killed, not a suite failure
    assert "cancelled by operator" in result["output"]


def test_pytest_timeout_preserved_without_cancel(tmp_path):
    (tmp_path / "test_slow.py").write_text(
        "import time\ndef test_slow():\n    time.sleep(5)\n")
    result = TestRunnerTool(tmp_path, timeout=1).run_pytest([])
    assert result["cancelled"] is False
    assert result["passed"] is False
    assert "timed out" in result["output"]


def _api_client(monkeypatch, token="op-token"):
    from app.main import app

    monkeypatch.setattr(settings, "approval_token", token)
    return TestClient(app)


def _tracked_run(orch, **overrides):
    run = orch.create_run("http://x/", "verify widgets load please")
    for key, value in overrides.items():
        setattr(run, key, value)
    return run


def test_cancel_endpoint_unknown_and_unauthorized(monkeypatch):
    from app.main import app
    from app.api import runs as runs_module

    client = _api_client(monkeypatch)
    assert client.post("/api/runs/does-not-exist/cancel",
                       headers={"X-Approval-Token": "op-token"}).status_code == 404
    # No token header and no session: refused before lookup logic matters.
    run = runs_module.orchestrator.create_run("http://x/", "verify widgets load please")
    try:
        denied = TestClient(app).post(f"/api/runs/{run.run_id}/cancel")
        assert denied.status_code == 401
    finally:
        runs_module.orchestrator.runs.pop(run.run_id, None)


def test_cancel_active_idempotent_and_terminal_rejected(monkeypatch, isolated):
    from app.main import app
    from app.api import runs as runs_module

    client = _api_client(monkeypatch)
    orch = runs_module.orchestrator
    run = _tracked_run(orch, status="running")
    headers = {"X-Approval-Token": "op-token"}
    try:
        first = client.post(f"/api/runs/{run.run_id}/cancel", headers=headers)
        assert first.status_code == 200
        assert run.cancel_requested and run.cancel_event.is_set()
        repeat = client.post(f"/api/runs/{run.run_id}/cancel", headers=headers)
        assert repeat.status_code == 200  # idempotent while live

        run.status = "cancelled"
        already = client.post(f"/api/runs/{run.run_id}/cancel", headers=headers)
        assert already.status_code == 200
        assert already.json()["status"] == "cancelled"

        run.status = "completed"
        run.fix_status = "verified"
        done = client.post(f"/api/runs/{run.run_id}/cancel", headers=headers)
        assert done.status_code == 409
        assert run.status == "completed"  # terminal result untouched
        assert run.fix_status == "verified"
    finally:
        orch.runs.pop(run.run_id, None)


def test_cancel_before_approval_applies_nothing(monkeypatch, isolated, tmp_path):
    from app.main import app
    from app.api import runs as runs_module

    client = _api_client(monkeypatch)
    orch = runs_module.orchestrator
    target = tmp_path / "ws" / "data.txt"
    target.parent.mkdir(parents=True)
    target.write_text("buggy\n")
    run = _tracked_run(orch, status="completed", fix_status="awaiting_approval")
    run.fix_diff = "diff --git a/data.txt b/data.txt\n"
    headers = {"X-Approval-Token": "op-token"}
    try:
        resp = client.post(f"/api/runs/{run.run_id}/cancel", headers=headers)
        assert resp.status_code == 200
        assert run.fix_status == "cancelled"
        assert run.fix_worktree == ""
        assert target.read_text() == "buggy\n"  # patch never applied
        assert not (tmp_path / "worktrees").exists()
        rows = read_history()
        assert any(r["run_id"] == run.run_id and r["transition"] == "fix_cancelled"
                   for r in rows)
    finally:
        orch.runs.pop(run.run_id, None)
