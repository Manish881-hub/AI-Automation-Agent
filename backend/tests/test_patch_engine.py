"""Patch engine contract: intent in, validated git diff out.

Covers: valid edit, missing anchor, ambiguous anchor, denied path,
empty edit list, malformed model response, and the real fixture edit.
"""

import pytest

from app.schemas import FixProposal, PatchEdit
from app.tools.codebase import CodebaseTool
from app.tools.patch import PatchEngineError, build_diff

WORKSPACE = "/home/manishbhaktisagar/Downloads/ai-test-automation-agent/demo/autofix-app"


@pytest.fixture()
def tool():
    return CodebaseTool(WORKSPACE)


def test_valid_edit_generates_appliable_diff(tool):
    edits = [PatchEdit(
        file="backend/auth.py",
        old_text='    token = provider_response["token"]',
        new_text='    token = provider_response.get("token")',
    )]
    diff = build_diff(tool, edits)
    assert "diff --git" in diff
    ok, _ = tool.apply_check(diff)
    assert ok


def test_missing_old_text_rejected(tool):
    with pytest.raises(PatchEngineError, match="not present"):
        build_diff(tool, [PatchEdit(
            file="backend/auth.py",
            old_text="token = does_not_exist_anywhere_xyz()",
            new_text="token = None",
        )])


def test_ambiguous_old_text_rejected(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\nx = 1\n")
    tool = CodebaseTool(str(tmp_path))
    with pytest.raises(PatchEngineError, match="ambiguous"):
        build_diff(tool, [PatchEdit(
            file="a.py",
            old_text="x = 1",
            new_text="x = 2",
        )])


def test_denied_path_rejected(tool):
    with pytest.raises(PatchEngineError):
        build_diff(tool, [PatchEdit(
            file=".env",
            old_text="x",
            new_text="y",
        )])


def test_empty_edits_rejected(tool):
    with pytest.raises(PatchEngineError, match="empty"):
        build_diff(tool, [])


def test_malformed_model_response_rejected(tool):
    with pytest.raises(Exception):
        FixProposal.model_validate({"edits": "just do it"})
    with pytest.raises(PatchEngineError, match="empty old_text"):
        build_diff(tool, [PatchEdit(file="backend/auth.py", old_text="  ", new_text="x")])


def test_fixture_auth_edit_applies_cleanly(tool):
    # The exact intent the fixer must express for the eval to pass.
    edits = [
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
    diff = build_diff(tool, edits)
    ok, out = tool.apply_check(diff)
    assert ok, out
