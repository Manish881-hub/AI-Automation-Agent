from ..schemas import WebsiteSnapshot
from ..tools.browser import BrowserTool


class ReconnaissanceAgent:
    """Inspect the website BEFORE planning: open it, build a compact page model.

    The snapshot contains only interaction-relevant targets (headings for
    orientation, buttons/links/inputs/forms for actions) — never the raw
    DOM — so the planner stays grounded in what actually exists on the page.
    """

    name = "reconnaissance"

    async def run(self, browser: BrowserTool, url: str, screenshot_path: str | None = None) -> WebsiteSnapshot:
        await browser.navigate(url)
        return await browser.snapshot(screenshot_path)

    def describe(self, snapshot: WebsiteSnapshot) -> str:
        """Compact human/LLM-readable rendering of a snapshot for prompts."""
        lines = [
            f"URL: {snapshot.url}",
            f"Title: {snapshot.title}",
        ]
        if snapshot.headings:
            lines.append("Headings:")
            lines.extend(f"  - {h}" for h in snapshot.headings[:10])
        if snapshot.buttons:
            lines.append("Buttons:")
            lines.extend(f"  - {b}" for b in snapshot.buttons[:20])
        if snapshot.inputs:
            lines.append("Inputs:")
            for i in snapshot.inputs[:15]:
                label = i.label or i.placeholder or i.name or i.type
                lines.append(f"  - {label} ({i.type})")
        if snapshot.links:
            lines.append("Links:")
            lines.extend(f"  - {l.text}" for l in snapshot.links[:20])
        if snapshot.forms:
            lines.append(f"Forms: {len(snapshot.forms)} form(s) present")
        if snapshot.visible_text:
            lines.append(f"Visible text (excerpt): {snapshot.visible_text[:1500]}")
        return "\n".join(lines)
