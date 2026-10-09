"""Fix-proposal loop: one bounded retry on an empty model reply.

A cheap model sometimes returns an explanation with no edit intents.
The loop must ask once more (same bound as a rejected edit), then park
as proposal_failed — never spin, never silently accept nothing.
"""

import asyncio
from pathlib import Path

import pytest

from app.agents.codebase import CodebaseAgent
from app.schemas import FailureAnalysis, FixProposal, PatchEdit
from app.services.orchestrator import Orchestrator
from app.services.session import session_store

WORKSPACE = Path("/home/manishbhaktisagar/Downloads/ai-test-automation-agent/demo/autofix-app")

VALID_EDITS = [
    PatchEdit(
        file="backend/auth.py",
        old_text='    token = provider_response["token"]',
        new_text='    token = provider_response.get("token")',
    ),
    PatchEdit(
        file="backend/auth.py",
        old_text='    return {"ok": True, "token": token, "limited": False}',
        new_text='    return {"ok": True, "token": token, "limited": True}',
    ),
]


class StubLLM:
    def __init__(self, proposals):
        self.proposals = list(proposals)
        self.calls = 0

    def scoped(self, budget):
        return self

    async def structured(self, system, user, schema):
        self.calls += 1
        return self.proposals.pop(0)


@pytest.fixture()
def orch_setup(monkeypatch):
    files = {
        "backend/auth.py": (WORKSPACE / "backend" / "auth.py").read_text(),
        "backend/tests/test_auth.py": (WORKSPACE / "backend" / "tests" / "test_auth.py").read_text(),
    }
    monkeypatch.setattr(
        CodebaseAgent, "investigate",
        lambda self, tool, results, analysis: {"files": files},
    )
    monkeypatch.setattr("app.services.orchestrator.save_text", lambda *a, **k: "")
    orch = Orchestrator()
    run = orch.create_run("http://localhost:8921", "verify sign in works please")
    run.analysis = FailureAnalysis(
        summary="login fails", probable_root_cause="KeyError on token", failed=True,
    )
    session = session_store.create(run.run_id)
    yield orch, run, session
    session_store.sessions.pop(run.run_id, None)


def test_empty_edits_retried_once_then_proposed(orch_setup):
    orch, run, session = orch_setup
    orch.llm = StubLLM([
        FixProposal(edits=[], explanation="looks fine"),
        FixProposal(edits=VALID_EDITS, explanation="use .get()", test_targets=[]),
    ])
    asyncio.run(orch._propose_fix(run, session))
    assert orch.llm.calls == 2
    assert run.fix_status == "awaiting_approval"
    assert "diff --git" in run.fix_diff


def test_two_empty_replies_park_as_proposal_failed(orch_setup):
    orch, run, session = orch_setup
    orch.llm = StubLLM([
        FixProposal(edits=[], explanation="one"),
        FixProposal(edits=[], explanation="two"),
    ])
    asyncio.run(orch._propose_fix(run, session))
    assert orch.llm.calls == 2  # bounded: no third attempt
    assert run.fix_status == "proposal_failed"
