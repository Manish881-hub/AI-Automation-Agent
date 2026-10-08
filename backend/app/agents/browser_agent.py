from ..config import settings
from ..schemas import TestStep, StepResult
from ..tools.browser import BrowserTool
from .validator import ValidatorAgent


# Demo-only seeded credentials (browser-qa: never real production logins).
PLACEHOLDERS = {
    "{{TEST_EMAIL}}": lambda: settings.test_email,
    "{{TEST_PASSWORD}}": lambda: settings.test_password,
}


def resolve_placeholders(value: str) -> str:
    for token, lookup in PLACEHOLDERS.items():
        if token in value:
            value = value.replace(token, lookup())
    return value


class BrowserAgent:
    """Execute explicit browser actions; delegate verdicts to ValidatorAgent."""

    name = "browser"

    def __init__(self) -> None:
        self.validator = ValidatorAgent()

    async def execute(self, browser: BrowserTool, step: TestStep, screenshot_path: str) -> StepResult:
        try:
            visible_check: bool | None = None
            if step.action == "navigate":
                await browser.navigate(step.target)
            elif step.action == "click":
                await browser.click(step.target)
            elif step.action == "fill":
                await browser.fill(step.target, resolve_placeholders(step.value))
            elif step.action == "press":
                await browser.press(step.target or "body", step.value)
            elif step.action == "assert":
                # Visibility probes need the live page; every other
                # assertion type is judged purely from the observation.
                if (step.assertion_type or "text") in ("visible", "element"):
                    visible_check = await browser.ensure_visible(step.target)

            observation = await browser.observe(screenshot_path)
            return self.validator.validate(step, observation, visible_check=visible_check)
        except Exception as exc:
            # Guard observe(): a dead page must yield a step-level error,
            # never an orchestrator-level crash (ECC error-handling).
            try:
                observation = await browser.observe(screenshot_path)
            except Exception:
                observation = None
            return self.validator.validate(step, observation, step_error=str(exc))
