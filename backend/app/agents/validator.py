from ..schemas import TestStep, StepResult, TestEvidence


class ValidatorAgent:
    """Determine whether an observable expected condition was satisfied.

    Pure verdict function: takes a browser observation (or None when the
    page is dead), an optional step-level error, and an optional
    visibility probe result for visible/element assertions. No Playwright
    calls in here — Browser executes, Validator judges.
    """

    name = "validator"

    def validate(
        self,
        step: TestStep,
        observation,
        step_error: str | None = None,
        visible_check: bool | None = None,
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

        # Duck-typed: accept BrowserObservation (screenshot_path) or
        # TestEvidence (screenshot) so the verdict never crashes on shape.
        evidence = TestEvidence(
            url=getattr(observation, "url", "") or "",
            title=getattr(observation, "title", "") or "",
            text=getattr(observation, "text", "") or "",
            console_errors=list(getattr(observation, "console_errors", []) or []),
            network_errors=list(getattr(observation, "network_errors", []) or []),
            screenshot=getattr(observation, "screenshot_path", None)
            or getattr(observation, "screenshot", None),
        )
        if step_error:
            return StepResult(
                step_id=step.id,
                status="error",
                message=step_error,
                evidence=evidence,
            )

        if step.action == "assert":
            return self._check_assertion(step, evidence, visible_check)

        return StepResult(
            step_id=step.id,
            status="passed",
            message="Step completed successfully.",
            evidence=evidence,
        )

    def _check_assertion(
        self, step: TestStep, evidence: TestEvidence, visible_check: bool | None
    ) -> StepResult:
        atype = step.assertion_type or "text"
        expected = step.expected or ""

        if atype == "visible" or atype == "element":
            # visible_check is probed by the browser agent (see
            # BrowserTool.ensure_visible) before the observation is taken.
            if visible_check is False:
                return self._fail(step, f"{step.target!r} is not visible.", evidence)
            return self._passed(step, evidence)

        if atype == "url":
            if expected.lower() not in (evidence.url or "").lower():
                return self._fail(
                    step, f"Expected URL to contain {expected!r}, got {evidence.url!r}.", evidence,
                )
            return self._passed(step, evidence)

        if atype == "title":
            if expected.lower() not in (evidence.title or "").lower():
                return self._fail(
                    step, f"Expected title to contain {expected!r}, got {evidence.title!r}.",
                    evidence,
                )
            return self._passed(step, evidence)

        if atype == "no_console_error":
            if evidence.console_errors:
                shown = "; ".join(evidence.console_errors[:3])
                return self._fail(step, f"Console errors present: {shown}", evidence)
            return self._passed(step, evidence)

        if atype == "http_success":
            if evidence.network_errors:
                shown = "; ".join(evidence.network_errors[:3])
                return self._fail(step, f"Failed HTTP requests: {shown}", evidence)
            return self._passed(step, evidence)

        # default "text": expected substring in page text or title
        haystack = f"{evidence.text} {evidence.title}".lower()
        if expected and expected.lower() not in haystack:
            return self._fail(step, f"Expected {expected!r} not observed.", evidence)
        return self._passed(step, evidence)

    @staticmethod
    def _fail(step: TestStep, message: str, evidence: TestEvidence) -> StepResult:
        return StepResult(step_id=step.id, status="failed", message=message, evidence=evidence)

    @staticmethod
    def _passed(step: TestStep, evidence: TestEvidence) -> StepResult:
        return StepResult(
            step_id=step.id, status="passed",
            message="Step completed successfully.", evidence=evidence,
        )
