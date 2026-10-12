"""RecoveryDecision synonym coercion: model-shaped replies must parse.

A wrapped reply like {"decision": "retry_alternate_target", ...} previously
aborted recovery with a validation error. The retry action and target are
recovered; genuinely invalid actions are still rejected.
"""

import pytest
from pydantic import ValidationError

from app.schemas import RecoveryDecision


def test_wrapped_decision_envelope_parses():
    d = RecoveryDecision.model_validate({
        "decision": "retry_alternate_target",
        "label": "Log in",
        "reason": "label was not found.",
    })
    assert d.action == "retry_alternate_target"
    assert d.target == "Log in"
    assert d.reason == "label was not found."


def test_plain_decision_still_parses():
    d = RecoveryDecision.model_validate({"action": "skip", "reason": "gone"})
    assert (d.action, d.target, d.reason) == ("skip", "", "gone")


def test_invalid_action_still_rejected():
    with pytest.raises(ValidationError):
        RecoveryDecision.model_validate({"decision": "teleport"})
