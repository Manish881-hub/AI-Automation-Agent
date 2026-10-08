from ..schemas import FixProposal
from ..services.llm import LLM


class FixerAgent:
    """Propose a source fix as structured edit intents — never patch syntax.

    The fixer states WHAT to change (file + old/new text) and WHY. The
    deterministic patch engine builds and validates the git diff; the
    orchestrator applies it only after explicit human approval.
    """

    name = "fixer"

    async def propose(
        self,
        llm: LLM,
        failure_summary: str,
        code_context: dict,
        test_command: str = "python -m pytest -q",
        feedback: str = "",
    ) -> FixProposal:
        files_text = "\n\n".join(
            f"--- {path} ---\n{content[:6000]}"
            for path, content in code_context.get("files", {}).items()
        )
        system = """You are a careful bug-fix engineer. Propose the SMALLEST set of source edits that fixes the diagnosed failure.
Return JSON matching the requested schema: an edits array of {file, old_text, new_text} plus explanation and test_targets.
Rules:
- old_text must be an EXACT excerpt of the shown code (indentation included), unique in its file, a few lines only.
- new_text is the replacement for exactly that excerpt.
- Infer the intended behavior from the FAILING TEST and surrounding code. Make the smallest change that satisfies the existing test contract. Do not invent new behavior (no invented tokens, flags, or fallback values the tests do not require).
- No unrelated refactors, no new dependencies, no credential or secret changes, never delete tests.
Example edit: {"file": "demo/app.py", "old_text": "    token = response[\\"token\\"]", "new_text": "    token = response.get(\\"token\\")"}
Reply with JSON only, no prose outside the JSON."""
        user = (
            f"Failure:\n{failure_summary}\n\n"
            f"Relevant code and tests:\n{files_text or '(no files located)'}\n\n"
            f"Verify with: {test_command}"
        )
        if feedback:
            user += f"\n\nPrevious attempt was rejected, fix it:\n{feedback}"
        return await llm.structured(system, user, FixProposal)
