from ..schemas import TestStep, StepResult
from ..tools.browser import BrowserTool
from .validator import ValidatorAgent


class BrowserAgent:
    """Execute explicit browser actions; delegate verdicts to ValidatorAgent."""

    name = "browser"

    def __init__(self) -> None:
        self.validator = ValidatorAgent()

    async def execute(self, browser: BrowserTool, step: TestStep, screenshot_path: str) -> StepResult:
        try:
            if step.action == "navigate":
                await browser.navigate(step.target)
            elif step.action == "click":
                await browser.click(step.target)
            elif step.action == "fill":
                await browser.fill(step.target, step.value)
            elif step.action == "press":
                await browser.press(step.target or "body", step.value)
            elif step.action == "assert":
                pass  # no browser mutation; verdict comes from observation below

            observation = await browser.observe(screenshot_path)
            return self.validator.validate(step, observation)
        except Exception as exc:
            # Guard observe(): a dead page must yield a step-level error,
            # never an orchestrator-level crash (ECC error-handling).
            try:
                observation = await browser.observe(screenshot_path)
            except Exception:
                observation = None
            return self.validator.validate(step, observation, step_error=str(exc))
