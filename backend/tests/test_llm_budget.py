"""Per-run LLM budget: counting, cap enforcement, terminal mapping.

One ScopedLLM per run over the shared client: logical calls are counted,
the cap stops loops loudly as kind="budget", and retries inside a call
do not each consume budget (they stay bounded by llm_max_attempts).
"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app.config import settings
from app.services.llm import (
    BudgetExhausted, CallBudget, LLM, ScopedLLM,
    describe_provider_error,
)
from app.services.orchestrator import Orchestrator
from app.services.session import session_store
from test_llm_resilience import FakeCompletions, _err, _ok_reply

import openai


@pytest.fixture(autouse=True)
def _isolated_artifacts(tmp_path, monkeypatch):
    # _fail_run appends to the history index: keep suite writes out of the
    # real backend/artifacts directory.
    monkeypatch.setattr(settings, "artifact_dir", str(tmp_path))


@pytest.fixture()
def no_sleep(monkeypatch):
    recorded: list[float] = []

    async def fake_sleep(delay: float) -> None:
        recorded.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return recorded


class Ping(BaseModel):
    ok: bool = False


def _scoped(completions: FakeCompletions, cap: int) -> ScopedLLM:
    llm = LLM()
    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return llm.scoped(CallBudget(cap=cap))


def test_scoped_counts_logical_calls_and_delegates():
    completions = FakeCompletions([_ok_reply({"ok": True})])
    scoped = _scoped(completions, cap=30)
    result = asyncio.run(scoped.structured("s", "u", Ping))
    assert result.ok is True
    assert scoped.budget.calls == 1
    assert len(completions.calls) == 1


def test_cap_stops_before_extra_sdk_call():
    completions = FakeCompletions([_ok_reply({"ok": True})])
    scoped = _scoped(completions, cap=2)
    asyncio.run(scoped.structured("s", "u", Ping))
    asyncio.run(scoped.text("s", "u"))
    with pytest.raises(BudgetExhausted):
        asyncio.run(scoped.structured("s", "u", Ping))
    assert scoped.budget.calls == 2
    assert len(completions.calls) == 2


def test_retries_inside_a_call_spend_once(no_sleep, monkeypatch):
    monkeypatch.setattr(settings, "llm_max_attempts", 4)
    completions = FakeCompletions([
        _err(openai.RateLimitError, 429, "limited"),
        _err(openai.RateLimitError, 429, "limited"),
        _ok_reply({"ok": True}),
    ])
    scoped = _scoped(completions, cap=30)
    assert asyncio.run(scoped.structured("s", "u", Ping)).ok is True
    assert scoped.budget.calls == 1
    assert len(completions.calls) == 3


def test_budget_trip_is_terminal_kind_budget():
    orch = Orchestrator()
    run = orch.create_run("http://localhost:8921", "verify sign in works please")
    session = session_store.create(run.run_id)
    try:
        orch._fail_run(run, session, "planning", BudgetExhausted("spent 30/30"))
    finally:
        session_store.sessions.pop(run.run_id, None)
    assert run.status == "failed"
    assert run.error_kind == "budget"
    assert run.objective_status == "unknown"
    assert "30/30" in run.error_message
    assert run.llm_calls == run.llm_budget.calls


def test_default_cap_leaves_golden_headroom():
    # Structural worst case per run: plan + 2 revisions + 12 recovery +
    # debugger + 2 proposals = ~18 logical calls.
    assert settings.llm_max_calls_per_run >= 25


def test_budget_message_actionable_not_provider_blame():
    completions = FakeCompletions([_ok_reply({"ok": True})])
    scoped = _scoped(completions, cap=0)  # first call trips immediately
    with pytest.raises(BudgetExhausted) as exc_info:
        asyncio.run(scoped.text("s", "u"))
    assert len(completions.calls) == 0  # stopped before any SDK call
    msg = describe_provider_error(exc_info.value)
    assert "LLM_MAX_CALLS_PER_RUN" in msg
    for blame in ("rate limit", "unavailable", "timed out", "HTTP 429"):
        assert blame not in msg  # policy stop, not provider blame
