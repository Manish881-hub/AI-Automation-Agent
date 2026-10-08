from ..schemas import FailureAnalysis, StepResult
from ..services.llm import LLM


class DebuggerAgent:
    name = "debugger"

    async def run(self, llm: LLM, results: list[StepResult]) -> FailureAnalysis:
        failed = [r for r in results if r.status != "passed"]
        if not failed:
            return FailureAnalysis(
                failed=False,
                summary="All executed steps passed.",
                probable_root_cause="None detected.",
                evidence=[],
                recommended_actions=[],
            )

        evidence = []
        for r in failed:
            if r.evidence:
                evidence.append(f"Step {r.step_id}: {r.message}")
                evidence.extend(r.evidence.console_errors[-5:])
                evidence.extend(r.evidence.network_errors[-5:])

        prompt = "\n".join(evidence)[-12000:]
        system = """You are a senior production debugging engineer. Analyze failed browser-test evidence. Do not invent facts. Distinguish observed evidence from hypotheses. Return JSON matching the requested schema."""
        analysis = await llm.structured(system, prompt, FailureAnalysis)
        # Cheap models often leave evidence/summary blank even when the
        # diagnosis is right — backfill from what was actually observed so
        # the report never says "no failure evidence" after real failures.
        if not analysis.evidence:
            analysis = analysis.model_copy(update={"evidence": evidence[:10]})
        if not analysis.summary:
            analysis = analysis.model_copy(
                update={"summary": f"{len(failed)} of {len(results)} steps failed."}
            )
        return analysis
