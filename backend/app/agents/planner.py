from ..schemas import TestPlan, WebsiteSnapshot
from ..services.llm import LLM


class PlannerAgent:
    name = "planner"

    async def run(
        self,
        llm: LLM,
        url: str,
        objective: str,
        website_context: WebsiteSnapshot | None = None,
    ) -> TestPlan:
        system = """You are a senior QA automation planner. Create a small, executable web test plan.
Return JSON matching the requested schema. Keep the plan to at most 8 steps.
Rules:
- Only use actions: navigate, click, fill, press, assert.
- Targets must be SEMANTIC (visible button text, link text, input label or
  placeholder) taken from the website context below — never invent CSS
  selectors. The browser adapter resolves semantic targets deterministically.
- Prefer targets that literally appear in the website context.
- For fill steps on login credentials use placeholders {{TEST_EMAIL}} and
  {{TEST_PASSWORD}} as the value; use real visible text for anything else.
- For assert, set assertion_type explicitly: text (default), visible,
  url, title, element, no_console_error, or http_success. Use url/title
  assertions for navigation outcomes, visible for element presence."""
        if website_context is not None:
            context = self._format_context(website_context)
            user = (
                f"Website: {url}\nObjective: {objective}\n\n"
                f"Website context (observed live — only target what is listed):\n{context}"
            )
        else:
            user = f"Website: {url}\nObjective: {objective}"
        return await llm.structured(system, user, TestPlan)

    def _format_context(self, snapshot: WebsiteSnapshot) -> str:
        lines = [f"Title: {snapshot.title}"]
        if snapshot.headings:
            lines.append("Headings: " + "; ".join(snapshot.headings[:10]))
        if snapshot.buttons:
            lines.append("Buttons: " + "; ".join(snapshot.buttons[:20]))
        if snapshot.inputs:
            parts = [
                i.label or i.placeholder or i.name or i.type
                for i in snapshot.inputs[:15]
            ]
            lines.append("Inputs: " + "; ".join(parts))
        if snapshot.links:
            lines.append("Links: " + "; ".join(l.text for l in snapshot.links[:20]))
        if snapshot.forms:
            lines.append(f"Forms: {len(snapshot.forms)} present")
        return "\n".join(lines)
