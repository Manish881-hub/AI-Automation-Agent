from ..schemas import FailureAnalysis, StepResult


class ReporterAgent:
    name = "reporter"

    def run(self, results: list[StepResult], analysis: FailureAnalysis) -> str:
        passed = sum(r.status == "passed" for r in results)
        failed = len(results) - passed
        lines = [
            "# AI Test Automation Report",
            "",
            f"**Result:** {'FAILED' if analysis.failed else 'PASSED'}",
            f"**Steps:** {passed} passed / {failed} failed",
            "",
            "## Summary",
            analysis.summary,
            "",
            "## Probable root cause",
            analysis.probable_root_cause,
            "",
            "## Evidence",
        ]
        lines.extend([f"- {x}" for x in analysis.evidence] or ["- No failure evidence."])
        lines += ["", "## Recommended actions"]
        lines.extend([f"- {x}" for x in analysis.recommended_actions] or ["- No action required."])
        return "\n".join(lines)
