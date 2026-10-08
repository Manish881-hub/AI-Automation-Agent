from ..schemas import FailureAnalysis, StepResult


class ReporterAgent:
    """Render the evidence-backed report: observed facts first,
    agent hypothesis clearly labeled as such, never blended."""

    name = "reporter"

    def run(
        self,
        results: list[StepResult],
        analysis: FailureAnalysis,
        objective: str = "",
    ) -> str:
        passed = sum(r.status == "passed" for r in results)
        failed = len(results) - passed
        failed_steps = [r for r in results if r.status != "passed"]

        console: list[str] = []
        network: list[str] = []
        screenshots: list[str] = []
        for r in failed_steps:
            ev = r.evidence
            if not ev:
                continue
            console.extend(ev.console_errors)
            network.extend(ev.network_errors)
            if ev.screenshot:
                screenshots.append(ev.screenshot.split("/")[-1])

        if not analysis.failed:
            confidence = "high"
        elif network or console:
            confidence = "high"
        elif failed_steps:
            confidence = "medium"
        else:
            confidence = "low"

        lines = [
            "# AI Test Automation Report",
            "",
            f"**Result:** {'FAILED' if analysis.failed else 'PASSED'}",
            f"**Steps:** {passed} passed / {failed} failed",
            f"**Confidence:** {confidence}",
            "",
        ]
        if objective:
            lines += ["## Expected", "", objective, ""]
        lines += ["## Observed evidence", ""]
        if failed_steps:
            for r in failed_steps:
                lines.append(f"- Step {r.step_id}: {r.message}")
        else:
            lines.append("- All steps passed; no failure evidence.")
        if console:
            lines += ["", "### Console"] + [f"- {c}" for c in console[:10]]
        if network:
            lines += ["", "### Network"] + [f"- {n}" for n in network[:10]]
        if screenshots:
            lines += ["", "### Screenshots"] + [f"- {s}" for s in screenshots]
        lines += [
            "",
            "## Probable cause (agent hypothesis, not observed fact)",
            "",
            analysis.probable_root_cause,
            "",
            "## Recommended actions",
            "",
        ]
        lines.extend([f"- {x}" for x in analysis.recommended_actions] or (
            ["- Investigate the failed steps above."] if analysis.failed
            else ["- No action required."]
        ))
        return "\n".join(lines)
