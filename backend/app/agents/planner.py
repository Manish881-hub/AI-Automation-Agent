from ..schemas import TestPlan
from ..services.llm import LLM


class PlannerAgent:
    name = "planner"

    async def run(self, llm: LLM, url: str, objective: str) -> TestPlan:
        system = """You are a senior QA automation planner. Create a small, executable web test plan.
Return JSON matching the requested schema. Prefer robust natural-language targets because the browser agent will resolve them from the page.
Only use actions: navigate, click, fill, press, assert. Keep the plan to at most 8 steps.
For click/fill, target should describe visible text, accessible label, placeholder, or CSS selector.
For assert, expected should describe an observable condition."""
        user = f"Website: {url}\nObjective: {objective}"
        return await llm.structured(system, user, TestPlan)
