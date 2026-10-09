"""Resilient LLM calls: bounded retries, OpenRouter fallbacks, terminal failure.

Covers: transient 429 then success, Retry-After honored, fallback models
passed via the SDK's extra_body mechanism, auth errors never retried,
json_object capability fallback preserved, and exhausted retries producing
a terminal infrastructure failure (not a website failure).
"""

import asyncio
import json
from types import SimpleNamespace

import httpx
import openai
import pytest
from pydantic import BaseModel

from app.config import settings
from app.services import llm as llm_module
from app.services.llm import LLM, candidate_models, describe_provider_error, is_transient
from app.services.orchestrator import Orchestrator
from app.services.session import session_store


class Ping(BaseModel):
    ok: bool = False


def _resp(status: int, headers: dict | None = None) -> httpx.Response:
    req = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    return httpx.Response(status, headers=headers or {}, request=req)


def _err(cls, status: int, message: str, headers: dict | None = None):
    return cls(message, response=_resp(status, headers), body={"error": message})


def _ok_reply(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
    )


class FakeCompletions:
    def __init__(self, effects):
        self.effects = list(effects)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        eff = self.effects.pop(0) if len(self.effects) > 1 else self.effects[0]
        if isinstance(eff, BaseException):
            raise eff
        return eff


def _llm_with(completions: FakeCompletions) -> LLM:
    llm = LLM()
    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return llm


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


def test_transient_429_then_success(no_sleep):
    llm = _llm_with(FakeCompletions([
        _err(openai.RateLimitError, 429, "rate limited", {"retry-after": "0"}),
        _ok_reply({"ok": True}),
    ]))
    result = asyncio.run(llm.structured("s", "u", Ping))
    assert result.ok is True
    assert len(llm.client.chat.completions.calls) == 2


def test_retry_after_header_honored(no_sleep):
    llm = _llm_with(FakeCompletions([
        _err(openai.RateLimitError, 429, "slow down", {"retry-after": "7"}),
        _ok_reply({"ok": True}),
    ]))
    asyncio.run(llm.structured("s", "u", Ping))
    assert no_sleep == [7.0]


def test_backoff_capped_when_no_retry_after(no_sleep, monkeypatch):
    monkeypatch.setattr(settings, "llm_retry_base_sec", 2.0)
    monkeypatch.setattr(settings, "llm_retry_max_sec", 5.0)
    monkeypatch.setattr(settings, "llm_max_attempts", 3)
    llm = _llm_with(FakeCompletions([
        _err(openai.InternalServerError, 500, "boom"),
        _err(openai.InternalServerError, 500, "boom"),
        _ok_reply({"ok": True}),
    ]))
    asyncio.run(llm.structured("s", "u", Ping))
    assert no_sleep == [2.0, 4.0]  # base * 2**attempt, capped at max


def test_fallback_models_sent_via_extra_body(monkeypatch):
    monkeypatch.setattr(settings, "openai_base_url", "https://openrouter.ai/api/v1")
    monkeypatch.setattr(settings, "openai_fallback_models", "fb-one, fb-two")
    assert candidate_models() == [settings.openai_model, "fb-one", "fb-two"]
    llm = _llm_with(FakeCompletions([_ok_reply({"ok": True})]))
    asyncio.run(llm.structured("s", "u", Ping))
    (call,) = llm.client.chat.completions.calls
    assert call["extra_body"] == {"models": [settings.openai_model, "fb-one", "fb-two"]}


def test_no_extra_body_without_fallbacks_or_openrouter(monkeypatch):
    monkeypatch.setattr(settings, "openai_base_url", "https://api.openai.com/v1")
    monkeypatch.setattr(settings, "openai_fallback_models", "fb-one")
    llm = _llm_with(FakeCompletions([_ok_reply({"ok": True})]))
    asyncio.run(llm.structured("s", "u", Ping))
    (call,) = llm.client.chat.completions.calls
    assert "extra_body" not in call


def test_auth_error_never_retried():
    completions = FakeCompletions([_err(openai.AuthenticationError, 401, "bad key")])
    llm = _llm_with(completions)
    with pytest.raises(openai.AuthenticationError):
        asyncio.run(llm.structured("s", "u", Ping))
    assert len(completions.calls) == 1


def test_json_object_capability_fallback_preserved():
    completions = FakeCompletions([
        _err(openai.BadRequestError, 400, "response_format json_object unsupported"),
        _ok_reply({"ok": True}),
    ])
    llm = _llm_with(completions)
    result = asyncio.run(llm.structured("s", "u", Ping))
    assert result.ok is True
    assert "response_format" in completions.calls[0]
    assert "response_format" not in completions.calls[1]


def test_is_transient_matrix():
    assert is_transient(_err(openai.RateLimitError, 429, "x"))
    assert is_transient(_err(openai.InternalServerError, 503, "x"))
    assert is_transient(openai.APITimeoutError(httpx.Request("POST", "https://x")))
    assert not is_transient(_err(openai.AuthenticationError, 401, "x"))
    assert not is_transient(_err(openai.BadRequestError, 400, "x"))
    assert not is_transient(RuntimeError("plain bug"))


def test_exhausted_retries_terminal_failure(no_sleep, monkeypatch):
    monkeypatch.setattr(settings, "llm_max_attempts", 2)
    completions = FakeCompletions([_err(openai.RateLimitError, 429, "limited")])
    llm = _llm_with(completions)
    with pytest.raises(openai.RateLimitError):
        asyncio.run(llm.structured("s", "u", Ping))
    assert len(completions.calls) == 2  # bounded: initial + 1 retry

    orch = Orchestrator()
    run = orch.create_run("http://localhost:8921", "verify sign in works please")
    session = session_store.create(run.run_id)
    try:
        orch._fail_run(run, session, "planning", _err(openai.RateLimitError, 429, "limited"))
    finally:
        session_store.sessions.pop(run.run_id, None)
    assert run.status == "failed"
    assert run.error_stage == "planning"
    assert run.error_kind == "infrastructure"
    assert "429" in run.error_message and "OPENAI_FALLBACK_MODELS" in run.error_message
    # Infrastructure death is not a website verdict.
    assert run.objective_status == "unknown"
    assert run.events[-1].startswith("orchestrator: run_failed")


def test_plan_rejection_is_agent_failure_not_infrastructure():
    orch = Orchestrator()
    run = orch.create_run("http://localhost:8921", "verify sign in works please")
    run.objective_status = "failed"  # set by the validation gate before raising
    session = session_store.create(run.run_id)
    try:
        orch._fail_run(run, session, "planning", RuntimeError("plan rejected: weak assert"))
    finally:
        session_store.sessions.pop(run.run_id, None)
    assert run.status == "failed"
    assert run.error_stage == "planning"
    assert run.error_kind == "agent"
    assert "plan rejected" in run.error_message
    assert run.objective_status == "failed"  # gate verdict preserved, not overwritten


def test_timeout_is_infrastructure_failure():
    orch = Orchestrator()
    run = orch.create_run("http://localhost:8921", "verify sign in works please")
    session = session_store.create(run.run_id)
    try:
        orch._fail_run(run, session, "planning", RuntimeError("planner timed out after 120s"))
    finally:
        session_store.sessions.pop(run.run_id, None)
    assert run.error_kind == "infrastructure"
    assert run.objective_status == "unknown"


def test_terminal_message_redacts_secrets():
    msg = describe_provider_error(RuntimeError("provider blew up sk-or-v1-abcdefg123456"))
    assert "sk-or-v1" not in msg
    assert "<redacted>" in msg


def test_describe_provider_error_actionable():
    assert "OPENAI_FALLBACK_MODELS" in describe_provider_error(
        _err(openai.RateLimitError, 429, "limited"))
    assert "did not fail" in describe_provider_error(
        _err(openai.InternalServerError, 503, "bad gateway"))
    assert llm_module.TRANSIENT_STATUSES >= {429, 502, 503, 504}
