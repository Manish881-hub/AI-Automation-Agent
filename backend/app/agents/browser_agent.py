from pathlib import Path
from ..schemas import TestStep, StepResult, TestEvidence
from ..tools.browser import BrowserTool


class BrowserAgent:
    name = "browser"

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
                observation = await browser.observe()
                expected = step.expected.lower()
                if expected not in observation.text.lower() and expected not in observation.title.lower():
                    return StepResult(step_id=step.id, status="failed", message=f"Expected '{step.expected}' was not observed.", evidence=TestEvidence(url=observation.url, title=observation.title, text=observation.text, console_errors=observation.console_errors, network_errors=observation.network_errors))

            observation = await browser.observe(screenshot_path)
            return StepResult(
                step_id=step.id,
                status="passed",
                message="Step completed successfully.",
                evidence=TestEvidence(
                    url=observation.url,
                    title=observation.title,
                    text=observation.text,
                    console_errors=observation.console_errors,
                    network_errors=observation.network_errors,
                    screenshot=observation.screenshot_path,
                ),
            )
        except Exception as exc:
            observation = await browser.observe(screenshot_path)
            return StepResult(
                step_id=step.id,
                status="error",
                message=str(exc),
                evidence=TestEvidence(
                    url=observation.url,
                    title=observation.title,
                    text=observation.text,
                    console_errors=observation.console_errors,
                    network_errors=observation.network_errors,
                    screenshot=observation.screenshot_path,
                ),
            )
