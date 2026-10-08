from ..schemas import StepResult, Verification


class VerifierAgent:
    """Judge the post-fix browser re-test on the original objective.

    The replay itself runs in the orchestrator (fresh browser, same plan,
    same success assertions). This agent turns those results into the
    Verification verdict and never touches the network or disk.
    """

    name = "verifier"

    def run(
        self,
        objective: str,
        success_step_ids: list[int],
        results: list[StepResult],
    ) -> Verification:
        by_id = {r.step_id: r for r in results}
        success = [by_id[i] for i in success_step_ids if i in by_id]
        passed = sum(r.status == "passed" for r in results)
        failed = len(results) - passed
        if success_step_ids and all(r.status == "passed" for r in success):
            return Verification(
                verified=True,
                objective_status="passed",
                reason=(
                    f"original objective re-tested after patch: "
                    f"success assertion(s) {success_step_ids} passed"
                ),
                steps_passed=passed,
                steps_failed=failed,
            )
        bad = [r for r in success if r.status != "passed"]
        reason = (
            f"success assertion step {bad[0].step_id} still "
            f"{bad[0].status}: {bad[0].message}"
            if bad
            else "no success assertion verified the objective after patch"
        )
        return Verification(
            verified=False,
            objective_status="failed",
            reason=reason,
            steps_passed=passed,
            steps_failed=failed,
        )
