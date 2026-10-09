"""failure_terms must mine error evidence from every step.

A weak plan can sail through a backend 500 (step "passed") while the
console traceback names the defect. Only failed-step messages plus
all-steps error channels become search terms; passed-step chatter stays
out so the search is not drowned in noise.
"""

from app.agents.codebase import failure_terms
from app.schemas import StepResult, TestEvidence


def _result(step_id, status, message, console=(), network=()):
    return StepResult(
        step_id=step_id, status=status, message=message,
        evidence=TestEvidence(
            console_errors=list(console), network_errors=list(network),
        ),
    )


def test_passed_step_console_traceback_yields_code_terms():
    results = [
        _result(1, "passed", "Step completed successfully."),
        _result(2, "failed", "Expected 'Acme Portal — Sign in' not observed."),
        _result(
            5, "passed", "Step completed successfully.",
            console=[
                "Failed to load resource: the server responded with a status "
                "of 500 (Internal Server Error)",
                "login failed: KeyError: 'token'",
            ],
            network=["500 POST http://localhost:8921/api/login"],
        ),
    ]
    terms = failure_terms(results, None)
    assert "token" in terms  # quoted in the console traceback -> auth.py
    assert len(terms) <= 12  # search stays capped and cheap


def test_passed_step_chatter_excluded():
    results = [_result(1, "passed", "Step completed successfully.")]
    assert "successfully" not in failure_terms(results, None)


def test_failed_step_message_still_mined():
    results = [_result(2, "failed", "Expected 'Acme Portal — Sign in' not observed.")]
    terms = failure_terms(results, None)
    assert "sign" in terms
