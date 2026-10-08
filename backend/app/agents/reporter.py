from ..schemas import FailureAnalysis, StepResult


class ReporterAgent:
    """Render the evidence-backed report: observed facts first,
    agent hypothesis clearly labeled as such, never blended.

    Execution status (did the steps run?) and objective status (did the
    client's business goal succeed?) are reported as separate verdicts —
    a fully-executed plan can still fail the business objective.
    """

    name = "reporter"

    def run(
        self,
        results: list[StepResult],
        analysis: FailureAnalysis,
        objective: str = "",
        objective_status: str = "unknown",
        objective_reason: str = "",
    ) -> str:
        passed = sum(r.status == "passed" for r in results)
        failed = len(results) - passed
        failed_steps = [r for r in results if r.status != "passed"]
        recovered = [r for r in results if r.retried and r.status == "passed"]

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

        status_word = objective_status.upper() if objective_status != "unknown" else "UNKNOWN"
        lines = [
            "# AI Test Automation Report",
            "",
            "## EXECUTION",
            "",
            f"{passed} passed / {failed} failed",
            "",
            "## BUSINESS OBJECTIVE",
            "",
            status_word,
        ]
        if objective_reason:
            lines.append(objective_reason)
        lines += [""]
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
        if recovered:
            lines += ["", "## Recovery"] + [
                f"- Step {r.step_id}: {r.recovered_from!r} → retried target passed"
                for r in recovered
            ]
        lines += [
            "",
            "## Probable cause (agent hypothesis, not observed fact)",
            "",
            analysis.probable_root_cause,
            "",
            "## Confidence",
            "",
            confidence,
            "",
            "## Recommended actions",
            "",
        ]
        lines.extend([f"- {x}" for x in analysis.recommended_actions] or (
            ["- Investigate the failed steps above."] if analysis.failed
            else ["- No action required."]
        ))
        return "\n".join(lines)
