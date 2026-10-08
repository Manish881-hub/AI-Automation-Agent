from ..schemas import TestStep, StepResult, TestEvidence


class ValidatorAgent:
    """Determine whether an observable expected condition was satisfied.

    Pure verdict function: takes a browser observation (or None when the
    page is dead) plus an optional step-level error string, returns a
    typed StepResult. No Playwright calls in here — keeps the
    architecture diagram honest (Browser executes, Validator judges).
    """

    name = "validator"

    def validate(
        self,
        step: TestStep,
        observation,
        step_error: str | None = None,
    ) -> StepResult:
        if observation is None:
            evidence = TestEvidence(
                console_errors=["observe failed: no observation captured"],
            )
            return StepResult(
                step_id=step.id,
                status="error",
                message=step_error or "browser observation unavailable",
                evidence=evidence,
            )

        evidence = TestEvidence(
            url=observation.url,
            title=observation.title,
            text=observation.text,
            console_errors=observation.console_errors,
            network_errors=observation.network_errors,
            screenshot=observation.screenshot_path,
        )
        if step_error:
            return StepResult(
                step_id=step.id,
                status="error",
                message=step_error,
                evidence=evidence,
            )

        if step.action == "assert":
            expected = (step.expected or "").lower()
            haystack = f"{observation.text} {observation.title}".lower()
            if expected and expected not in haystack:
                return StepResult(
                    step_id=step.id,
                    status="failed",
                    message=f"Expected {step.expected!r} not observed.",
                    evidence=evidence,
                )

        return StepResult(
            step_id=step.id,
            status="passed",
            message="Step completed successfully.",
            evidence=evidence,
        )
