from ..schemas import FixProposal
from ..services.llm import LLM


class FixerAgent:
    """Propose a source patch for a diagnosed failure — text only.

    The fixer never touches the filesystem. It returns a unified diff;
    the orchestrator stores it as fix.patch and applies it only after
    explicit human approval (bounded autonomous fixing).
    """

    name = "fixer"

    async def propose(
        self,
        llm: LLM,
        failure_summary: str,
        code_context: dict,
        test_command: str = "python -m pytest -q",
    ) -> FixProposal:
        files_text = "\n\n".join(
            f"--- {path} ---\n{content[:6000]}"
            for path, content in code_context.get("files", {}).items()
        )
        system = """You are a careful bug-fix engineer. Propose the SMALLEST patch that fixes the diagnosed failure.
Return JSON matching the requested schema with:
- diff: a unified diff (git apply compatible, paths relative to repo root) changing only what the fix needs.
- explanation: what was wrong and why this fixes it, in 2-4 sentences grounded in the shown code.
- test_targets: pytest node/paths that verify the fix (default to the shown test files).
Rules: no unrelated refactors, no new dependencies, no credential or secret changes, never delete tests."""
        user = (
            f"Failure:\n{failure_summary}\n\n"
            f"Relevant code:\n{files_text or '(no files located)'}\n\n"
            f"Verify with: {test_command}"
        )
        return await llm.structured(system, user, FixProposal)
