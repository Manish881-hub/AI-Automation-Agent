"""Plan validator: the final-assertion signal scan must see url-assertion values.

A `url` assertion carries its objective signal in `value` (the URL itself)
while `target` may be a generic label like "URL Bar". Scanning only
target+expected rejects genuinely verifying plans; scanning all text
fields keeps the bar (signals required, error states rejected) intact.
"""

from app.agents.plan_validator import PlanValidatorAgent
from app.agents.planner import PlannerAgent
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


def test_bare_step_dict_coerced_to_plan():
    plan = TestPlan.model_validate(
        {"action": "navigate", "target": "http://localhost:8921/"})
    assert [s.id for s in plan.steps] == [1]
    assert plan.steps[0].action == "navigate"


def test_bare_steps_list_coerced_to_plan():
    plan = TestPlan.model_validate([
        {"action": "navigate", "target": "http://localhost:8921/"},
        {"action": "assert", "assertion_type": "visible", "target": "Welcome"},
    ])
    assert [s.id for s in plan.steps] == [1, 2]


def test_final_assert_on_error_state_still_rejected():
    plan = _plan({
        "action": "assert", "assertion_type": "text",
        "target": "Error", "value": "", "expected": "Sign in failed — dashboard",
        "reason": "error shown",
    })
    validation = PlanValidatorAgent().validate(plan, OBJECTIVE, 12)
    assert not validation.approved


def test_plan_cannot_navigate_away_from_client_origin():
    plan = _plan({
        "action": "assert", "assertion_type": "visible",
        "target": "Dashboard", "reason": "dashboard is visible",
    })
    plan.steps[0].target = "https://credential-collector.example/login"

    validation = PlanValidatorAgent().validate(
        plan, OBJECTIVE, allowed_url="http://localhost:8921"
    )

    assert not validation.approved
    assert any("outside the client origin" in reason for reason in validation.reasons)


def test_plan_step_limit_defaults_to_eight():
    steps = [
        {"id": 1, "action": "navigate", "target": "http://localhost:8921/"},
        *[
            {"id": i, "action": "click", "target": "Continue"}
            for i in range(2, 9)
        ],
        {"id": 9, "action": "assert", "assertion_type": "visible", "target": "Dashboard"},
    ]
    validation = PlanValidatorAgent().validate(
        TestPlan.model_validate({"steps": steps}), OBJECTIVE
    )

    assert not validation.approved
    assert any("limit is 8" in reason for reason in validation.reasons)


def test_exact_rejected_navigate_shape_is_not_a_url():
    """Regression: the local run produced target='navigate' for step one."""
    plan = _plan({
        "action": "assert", "assertion_type": "visible",
        "target": "Dashboard", "reason": "dashboard is visible",
    })
    plan.steps[0].target = "navigate"

    validation = PlanValidatorAgent().validate(
        plan, OBJECTIVE, allowed_url="http://localhost:8921/"
    )

    assert not validation.approved
    assert "step 1 has an invalid navigation URL" in validation.reasons


def test_planner_prompt_makes_navigate_target_a_url_not_a_semantic_label():
    """The model must not apply the semantic-target rule to navigate steps."""
    planner = PlannerAgent()

    assert "For `navigate`, set `target` to the exact Website URL" in planner.system_prompt
    assert "All other targets must be SEMANTIC" in planner.system_prompt
    assert "For `navigate`, use the exact Website URL" in planner.revision_system_prompt
