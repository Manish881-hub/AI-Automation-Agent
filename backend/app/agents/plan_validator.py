from ..schemas import PlanValidation, TestPlan


# Words too generic to ground a success criterion on their own.
_STOPWORDS = frozenset(
    "a an the and or to of for on in with that this verify check test "
    "user can be is are should must please ensure make sure it its as at "
    "by from into over after before reach reaches reached".split()
)

# A final assertion matching any of these proves an error state was
# asserted as success — the exact false-positive class v0.2.1 kills.
_ERROR_SIGNALS = (
    "failed", "failure", "error", "invalid", "incorrect", "wrong", "denied",
    "unauthorized", "forbidden", "not found", "unavailable", "try again",
    "went wrong", "404", "500", "501", "503", "timeout", "timed out",
)

# Never let a generated plan touch these, even if the model means well.
_DESTRUCTIVE_SIGNALS = ("drop table", "delete account", "rm -rf", "delete database")


def objective_keywords(objective: str) -> list[str]:
    words = [
        w.strip(".,!?;:\"'()[]").lower()
        for w in objective.replace("/", " ").split()
    ]
    return [w for w in words if len(w) > 2 and w not in _STOPWORDS]


def _has_verifiable_condition(step) -> bool:
    """An assertion with nothing to check against always passes vacuously."""
    atype = step.assertion_type or "text"
    if atype in ("visible", "element"):
        return bool((step.target or "").strip())
    if atype in ("no_console_error", "http_success"):
        return True
    return bool((step.expected or "").strip() or (step.target or "").strip())


class PlanValidatorAgent:
    """Deterministic policy over LLM-proposed plans.

    The LLM proposes; this agent disposes. No network, no model — pure
    rules, so approval is reproducible and explainable in review:
    """

    name = "plan_validator"

    def validate(
        self, plan: TestPlan | None, objective: str, max_steps: int = 12
    ) -> PlanValidation:
        reasons: list[str] = []
        if plan is None or not plan.steps:
            return PlanValidation(approved=False, reasons=["plan is empty"])
        steps = plan.steps
        if len(steps) > max_steps:
            reasons.append(f"plan has {len(steps)} steps, limit is {max_steps}")

        if steps[0].action != "navigate":
            reasons.append("plan must start with a navigate step")

        for s in steps:
            if s.action in ("click", "fill", "press") and not s.target.strip():
                reasons.append(f"step {s.id} ({s.action}) has an empty target")
            haystack = f"{s.target} {s.value} {s.expected}".lower()
            for bad in _DESTRUCTIVE_SIGNALS:
                if bad in haystack:
                    reasons.append(f"step {s.id} looks destructive ({bad!r})")

        last = steps[-1]
        keywords = objective_keywords(objective)
        success_ids: list[int] = []
        if last.action != "assert":
            reasons.append("plan must end with an assertion verifying the objective")
        elif not _has_verifiable_condition(last):
            reasons.append("final assertion has no verifiable condition (empty expected/target)")
        else:
            text = f"{last.target} {last.expected}".lower()
            if any(err in text for err in _ERROR_SIGNALS):
                reasons.append(
                    "final assertion verifies an error state, not objective success"
                )
            elif keywords and not any(k in text for k in keywords):
                reasons.append(
                    f"final assertion references none of the objective signals "
                    f"({', '.join(keywords[:6])})"
                )
            else:
                success_ids.append(last.id)
            for s in steps[:-1]:
                if s.action != "assert":
                    continue
                atext = f"{s.target} {s.expected}".lower()
                if keywords and any(k in atext for k in keywords) and not any(
                    err in atext for err in _ERROR_SIGNALS
                ):
                    success_ids.append(s.id)

        if reasons:
            return PlanValidation(approved=False, reasons=reasons)
        return PlanValidation(
            approved=True,
            reasons=[f"plan verifies objective via step(s) {sorted(set(success_ids))}"],
            success_step_ids=sorted(set(success_ids)) or [last.id],
        )
