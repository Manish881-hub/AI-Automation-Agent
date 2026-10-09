"""Plan validator: the final-assertion signal scan must see url-assertion values.

A `url` assertion carries its objective signal in `value` (the URL itself)
while `target` may be a generic label like "URL Bar". Scanning only
target+expected rejects genuinely verifying plans; scanning all text
fields keeps the bar (signals required, error states rejected) intact.
"""

from app.agents.plan_validator import PlanValidatorAgent
from app.schemas import TestPlan

OBJECTIVE = "verify that a user can sign in and reach the dashboard"


def _plan(last: dict) -> TestPlan:
    return TestPlan.model_validate({
        "steps": [
            {"id": 1, "action": "navigate", "target": "http://localhost:8921/"},
            {"id": 2, "action": "fill", "target": "Email", "value": "{{TEST_EMAIL}}"},
            {"id": 3, "action": "fill", "target": "Password", "value": "{{TEST_PASSWORD}}"},
            {"id": 4, "action": "click", "target": "Sign in"},
            {"id": 5, **last},
        ]
    })


def test_url_assert_with_dashboard_url_in_value_approved():
    plan = _plan({
        "action": "assert", "assertion_type": "url",
        "target": "URL Bar", "value": "http://localhost:8921/dashboard", "expected": "",
        "reason": "Verify user reached the dashboard",
    })
    validation = PlanValidatorAgent().validate(plan, OBJECTIVE, 12)
    assert validation.approved
    assert 5 in validation.success_step_ids


def test_final_assert_without_any_signal_still_rejected():
    plan = _plan({
        "action": "assert", "assertion_type": "title",
        "target": "Title", "value": "", "expected": "Welcome",
        "reason": "page loaded",
    })
    validation = PlanValidatorAgent().validate(plan, OBJECTIVE, 12)
    assert not validation.approved
    assert any("objective signals" in r for r in validation.reasons)


def test_final_assert_on_error_state_still_rejected():
    plan = _plan({
        "action": "assert", "assertion_type": "text",
        "target": "Error", "value": "", "expected": "Sign in failed — dashboard",
        "reason": "error shown",
    })
    validation = PlanValidatorAgent().validate(plan, OBJECTIVE, 12)
    assert not validation.approved
