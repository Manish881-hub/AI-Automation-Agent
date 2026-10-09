"""Durable run history: append-only index, tolerant reads, safe rewrites.

Covers: completed/provider/budget terminal records, list round-trip
through the real API route, malformed and torn-tail tolerance,
restart recovery (nothing cached in memory), and duplicate suppression.
"""

import json

import openai
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.services.history import history_path, read_history, record_terminal
from app.services.llm import BudgetExhausted
from app.services.orchestrator import Orchestrator
from app.services.session import session_store
from test_llm_resilience import _err


@pytest.fixture()
def tmp_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "artifact_dir", str(tmp_path))
    assert not history_path().exists()
    return tmp_path


def _run(orch, **overrides):
    run = orch.create_run("http://localhost:8921", "verify sign in works please")
    for key, value in overrides.items():
        setattr(run, key, value)
    return run


def _session_for(run):
    session = session_store.create(run.run_id)
    yield session
    session_store.sessions.pop(run.run_id, None)


def test_completed_run_recorded(tmp_artifacts):
    orch = Orchestrator()
    run = _run(orch, status="completed", objective_status="failed",
               objective_reason="dashboard never reached")
    assert record_terminal(run, "run_completed") is True
    (row,) = read_history()
    assert row["run_id"] == run.run_id
    assert row["transition"] == "run_completed"
    assert row["status"] == "completed"
    assert row["objective_status"] == "failed"
    assert row["ts"]


def test_provider_failure_recorded(tmp_artifacts):
    orch = Orchestrator()
    run = _run(orch)
    session = session_store.create(run.run_id)
    try:
        orch._fail_run(run, session, "planning",
                       _err(openai.RateLimitError, 429, "limited"))
    finally:
        session_store.sessions.pop(run.run_id, None)
    (row,) = read_history()
    assert row["transition"] == "run_failed"
    assert row["error_stage"] == "planning"
    assert row["error_kind"] == "infrastructure"
    assert "429" in row["error_message"]


def test_budget_failure_recorded(tmp_artifacts):
    orch = Orchestrator()
    run = _run(orch)
    session = session_store.create(run.run_id)
    try:
        orch._fail_run(run, session, "execution", BudgetExhausted("spent 30/30"))
    finally:
        session_store.sessions.pop(run.run_id, None)
    (row,) = read_history()
    assert row["transition"] == "run_failed"
    assert row["error_kind"] == "budget"


def test_list_round_trip_newest_first(tmp_artifacts):
    orch = Orchestrator()
    ids = []
    for i in range(3):
        run = _run(orch, status="completed")
        record_terminal(run, "run_completed")
        ids.append(run.run_id)
    rows = read_history(limit=2)
    assert [r["run_id"] for r in rows] == ids[::-1][:2]


def test_list_endpoint_serves_index(tmp_artifacts):
    from app.main import app

    orch = Orchestrator()
    run = _run(orch, status="completed")
    record_terminal(run, "run_completed")
    resp = TestClient(app).get("/api/runs?limit=10")
    assert resp.status_code == 200
    body = resp.json()
    assert [r["run_id"] for r in body["runs"]] == [run.run_id]


def test_malformed_and_torn_tail_tolerated(tmp_artifacts):
    path = history_path()
    good = {"run_id": "r-1", "transition": "run_completed", "status": "completed"}
    path.write_text(
        json.dumps(good) + "\n"
        + "\n"
        + "{not json\n"
        + "[1, 2]\n"  # valid JSON, not a record
        + '{"run_id": "r-2", "transition": "run_failed"',
        encoding="utf-8",
    )
    rows = read_history()
    assert [r["run_id"] for r in rows] == ["r-1"]


def test_restart_recovery_and_repeated_transitions(tmp_artifacts):
    orch = Orchestrator()
    run = _run(orch, status="failed", fix_status="awaiting_approval")
    assert record_terminal(run, "run_failed") is True
    # A repeated recording of the same transition is skipped …
    assert record_terminal(run, "run_failed") is False
    # … while a distinct later transition is kept and identifiable.
    run.status = "completed"
    run.fix_status = "verified"
    assert record_terminal(run, "fix_terminal") is True
    # Fresh read with no in-memory state: everything survived the "restart".
    rows = read_history()
    assert [(r["run_id"], r["transition"]) for r in rows] == [
        (run.run_id, "fix_terminal"),
        (run.run_id, "run_failed"),
    ]


def test_history_never_breaks_the_run(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "artifact_dir", "/proc/cannot-write-here")
    orch = Orchestrator()
    run = _run(orch)
    assert record_terminal(run, "run_completed") is False
    assert read_history() == []
