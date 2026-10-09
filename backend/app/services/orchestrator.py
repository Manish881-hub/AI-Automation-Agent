import asyncio
from dataclasses import dataclass, field
from uuid import uuid4
from ..agents.planner import PlannerAgent
from ..agents.plan_validator import PlanValidatorAgent
from ..agents.codebase import CodebaseAgent
from ..agents.fixer import FixerAgent
from ..agents.verifier import VerifierAgent
from ..agents.browser_agent import BrowserAgent
from ..agents.debugger import DebuggerAgent
from ..agents.reconnaissance import ReconnaissanceAgent
from ..agents.recovery import RecoveryAgent
from ..agents.reporter import ReporterAgent
from ..schemas import TestPlan, StepResult, FailureAnalysis, TestEvidence, WebsiteSnapshot
from ..services.llm import (
    LLM, BudgetExhausted, CallBudget,
    describe_provider_error, is_infrastructure_error,
)
from ..config import settings
from ..tools.browser import BrowserTool
from ..tools.codebase import CodebaseTool
from ..tools.testrunner import TestRunnerTool
from ..services.artifacts import run_dir, save_text
from ..services.session import session_store, Session


MAX_STEPS = 12
# Stage budget must exceed the LLM retry budget (llm_max_attempts slow
# calls plus capped backoff/Retry-After waits), or resilience is strangled
# by this timeout and every congested provider looks like a planner bug.
PLANNER_TIMEOUT_SEC = 300
PLANNER_MAX_REVISIONS = 2
MAX_FIX_ATTEMPTS = 2
STEP_TIMEOUT_SEC = 60
RUN_TIMEOUT_SEC = 600
RECON_TIMEOUT_SEC = 60
RECOVERY_TIMEOUT_SEC = 60
RECOVERY_MAX_ATTEMPTS = 1


@dataclass
class RunState:
    """Shared execution state: every agent reads/writes its own slice.

    recon       -> website
    planner     -> plan
    browser     -> results (via BrowserAgent), screenshots on disk
    validator   -> StepResult verdicts inside results
    recovery    -> retried results, failures notes
    debugger    -> analysis
    reporter    -> report
    orchestrator-> status, events, memory (structured mirror of events)
    """

    run_id: str
    url: str
    objective: str
    status: str = "queued"
    website: WebsiteSnapshot | None = None
    plan: TestPlan | None = None
    results: list[StepResult] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    memory: list[dict] = field(default_factory=list)
    success_step_ids: list[int] = field(default_factory=list)
    objective_status: str = "unknown"
    objective_reason: str = ""
    # White-box fix loop. fix_status: none -> proposed (awaiting human
    # approval) -> approved/applying -> verified | apply_failed | rejected.
    fix_status: str = "none"
    fix_diff: str = ""
    fix_explanation: str = ""
    fix_branch: str = ""
    fix_tests_output: str = ""
    fix_attempts: int = 0
    fix_base_commit: str = ""
    fix_verify_summary: str = ""
    analysis: FailureAnalysis | None = None
    report: str | None = None
    events: list[str] = field(default_factory=list)
    # Terminal infrastructure failure (distinct from a failed test or a
    # failed business objective): which stage died and why, in dashboard-
    # safe wording. objective_status stays "unknown" — the website under
    # test was never judged.
    error_stage: str = ""
    error_kind: str = ""
    error_message: str = ""
    # Per-run LLM spend. The shared client is wrapped per run
    # (LLM.scoped) so concurrent runs account separately; llm_calls mirrors
    # the budget counter for API/dashboard observability.
    llm_budget: CallBudget = field(
        default_factory=lambda: CallBudget(cap=settings.llm_max_calls_per_run)
    )
    llm_calls: int = 0


class Orchestrator:
    def __init__(self):
        self.runs: dict[str, RunState] = {}
        self.llm = LLM()

    def create_run(self, url: str, objective: str) -> RunState:
        run = RunState(run_id=str(uuid4()), url=url, objective=objective)
        self.runs[run.run_id] = run
        return run

    @staticmethod
    def _record(run: RunState, session: Session, agent: str, event: str, detail: str = "") -> None:
        """One call writes the audit trail everywhere: timeline, memory, session."""
        run.events.append(f"{agent}: {event}" + (f" {detail}" if detail else ""))
        run.memory.append({"agent": agent, "event": event, "detail": detail})
        session.record(agent, event, detail=detail)

    async def execute(self, run_id: str):
        run = self.runs[run_id]
        run.status = "running"
        session = session_store.create(run.run_id)
        self._record(run, session, "orchestrator", "started", run.objective)
        recon_agent = ReconnaissanceAgent()
        planner = PlannerAgent()
        browser_agent = BrowserAgent()
        recovery_agent = RecoveryAgent()
        debugger = DebuggerAgent()
        reporter = ReporterAgent()
        browser = BrowserTool()
        stage = "starting"
        # All agent reasoning in this run spends from its own budget, so one
        # looping run cannot spend another's allowance on the shared client.
        llm = self.llm.scoped(run.llm_budget)

        async def _run_pipeline():
            nonlocal stage
            # --- reconnaissance: understand the page before planning ---
            stage = "reconnaissance"
            await browser.start()
            self._record(run, session, "reconnaissance", "recon_started", run.url)
            try:
                recon_shot = str(run_dir(run.run_id) / "recon.png")
                run.website = await asyncio.wait_for(
                    recon_agent.run(browser, run.url, recon_shot),
                    timeout=RECON_TIMEOUT_SEC,
                )
                save_text(run.run_id, "snapshot.json", run.website.model_dump_json(indent=2))
                self._record(
                    run, session, "reconnaissance", "website_understood",
                    f"{len(run.website.buttons)} buttons, "
                    f"{len(run.website.inputs)} inputs, "
                    f"{len(run.website.links)} links",
                )
            except Exception as exc:
                # Degraded mode: plan blind rather than fail outright.
                run.website = None
                self._record(run, session, "reconnaissance", "recon_failed", str(exc))

            # --- planning, grounded in the snapshot when available ---
            stage = "planning"
            self._record(run, session, "planner", "tool_call", "generate test plan")
            try:
                run.plan = await asyncio.wait_for(
                    planner.run(llm, run.url, run.objective, website_context=run.website),
                    timeout=PLANNER_TIMEOUT_SEC,
                )
            except asyncio.TimeoutError:
                # Re-raise with the true stage so the outer handler cannot
                # mislabel a slow free-tier model as a run-budget timeout.
                raise RuntimeError(f"planner timed out after {PLANNER_TIMEOUT_SEC}s")
            save_text(run.run_id, "plan.json", run.plan.model_dump_json(indent=2))
            self._record(run, session, "planner", "plan_generated", f"{len(run.plan.steps)} steps")

            # --- plan validation: LLM proposes, deterministic policy approves ---
            validator = PlanValidatorAgent()
            validation = validator.validate(run.plan, run.objective, MAX_STEPS)
            revisions = 0
            while not validation.approved and revisions < PLANNER_MAX_REVISIONS:
                self._record(
                    run, session, "plan_validator", "plan_rejected",
                    "; ".join(validation.reasons),
                )
                try:
                    try:
                        run.plan = await asyncio.wait_for(
                            planner.revise(
                                llm, run.url, run.objective, run.website,
                                run.plan, validation.reasons,
                            ),
                            timeout=PLANNER_TIMEOUT_SEC,
                        )
                    except asyncio.TimeoutError:
                        raise RuntimeError(
                            f"planner revision timed out after {PLANNER_TIMEOUT_SEC}s"
                        )
                except RuntimeError:
                    raise
                except Exception as exc:
                    # A malformed revision is a failed attempt, not a dead run.
                    revisions += 1
                    self._record(
                        run, session, "plan_validator", "plan_repair_failed",
                        f"attempt {revisions}: unparsable revision ({str(exc)[:150]})",
                    )
                    continue
                revisions += 1
                save_text(run.run_id, "plan.json", run.plan.model_dump_json(indent=2))
                validation = validator.validate(run.plan, run.objective, MAX_STEPS)
                self._record(
                    run, session, "plan_validator", "plan_repaired",
                    f"attempt {revisions}: "
                    + ("approved" if validation.approved else "; ".join(validation.reasons)),
                )
            if not validation.approved:
                run.objective_status = "failed"
                run.objective_reason = "plan rejected: " + "; ".join(validation.reasons)
                self._record(run, session, "plan_validator", "plan_rejected_final",
                             run.objective_reason)
                raise RuntimeError(run.objective_reason)
            run.success_step_ids = validation.success_step_ids
            self._record(
                run, session, "plan_validator", "plan_validated",
                f"success via step(s) {run.success_step_ids}",
            )

            # --- execute with recovery retries ---
            stage = "execution"
            for step in run.plan.steps[:MAX_STEPS]:
                self._record(
                    run, session, "browser", "step_started", f"step {step.id} ({step.action})",
                )
                screenshot = str(run_dir(run.run_id) / f"step_{step.id}.png")
                try:
                    result = await asyncio.wait_for(
                        browser_agent.execute(browser, step, screenshot),
                        timeout=STEP_TIMEOUT_SEC,
                    )
                except asyncio.TimeoutError:
                    result = StepResult(
                        step_id=step.id,
                        status="error",
                        message=f"step timed out after {STEP_TIMEOUT_SEC}s",
                        evidence=TestEvidence(),
                    )
                attempts = 0
                while (
                    result.status != "passed"
                    and attempts < RECOVERY_MAX_ATTEMPTS
                    and step.action in ("click", "fill", "press")
                ):
                    attempts += 1
                    self._record(
                        run, session, "recovery", "recovery_decided",
                        f"step {step.id}: {result.message}",
                    )
                    try:
                        available = await asyncio.wait_for(
                            browser.available_targets(),
                            timeout=RECOVERY_TIMEOUT_SEC,
                        )
                        decision = await asyncio.wait_for(
                            recovery_agent.run(llm, step, available, result.message),
                            timeout=RECOVERY_TIMEOUT_SEC,
                        )
                    except asyncio.TimeoutError:
                        from ..schemas import RecoveryDecision

                        decision = RecoveryDecision(action="abort", reason="recovery timed out")
                    if decision.action == "retry_alternate_target" and decision.target:
                        self._record(
                            run, session, "recovery", "recovery_retried",
                            f"step {step.id} with {decision.target!r} ({decision.reason})",
                        )
                        retry_shot = str(run_dir(run.run_id) / f"step_{step.id}_retry{attempts}.png")
                        retry_step = step.model_copy(
                            update={
                                "target": decision.target,
                                "reason": f"{step.reason} [recovery: {decision.reason}]",
                            }
                        )
                        try:
                            result = await asyncio.wait_for(
                                browser_agent.execute(browser, retry_step, retry_shot),
                                timeout=STEP_TIMEOUT_SEC,
                            )
                            result = result.model_copy(update={
                                "retried": True,
                                "recovered_from": step.target,
                            })
                        except asyncio.TimeoutError:
                            result = StepResult(
                                step_id=step.id,
                                status="error",
                                message=f"recovery retry timed out after {STEP_TIMEOUT_SEC}s",
                                evidence=TestEvidence(),
                                retried=True,
                            )
                    elif decision.action == "abort":
                        self._record(
                            run, session, "recovery", "recovery_aborted",
                            f"step {step.id}: {decision.reason}",
                        )
                        break
                    else:  # skip: keep the failure, move to the next step
                        self._record(
                            run, session, "recovery", "recovery_skipped",
                            f"step {step.id}: {decision.reason}",
                        )
                        break
                run.results.append(result)
                verdict = "step_passed" if result.status == "passed" else "step_failed"
                self._record(
                    run, session, "validator", verdict,
                    f"step {step.id} -> {result.status}"
                    + (" (retried)" if result.retried else ""),
                )
                if result.status != "passed":
                    run.failures.append(f"Step {step.id}: {result.message}")

            # --- objective verdict: business success is not step success ---
            by_id = {r.step_id: r for r in run.results}
            success_results = [by_id[i] for i in run.success_step_ids if i in by_id]
            if run.success_step_ids and all(r.status == "passed" for r in success_results):
                run.objective_status = "passed"
                run.objective_reason = (
                    f"success assertion(s) {run.success_step_ids} passed"
                )
            else:
                failed = [r for r in success_results if r.status != "passed"]
                missing = [i for i in run.success_step_ids if i not in by_id]
                run.objective_status = "failed"
                if failed:
                    run.objective_reason = (
                        f"success assertion step {failed[0].step_id} "
                        f"{failed[0].status}: {failed[0].message}"
                    )
                elif missing:
                    run.objective_reason = f"success assertion step(s) {missing} never executed"
                else:
                    run.objective_reason = "no success assertion verified the objective"
            self._record(
                run, session, "objective", f"objective_{run.objective_status}",
                run.objective_reason,
            )

            stage = "diagnosis"
            self._record(run, session, "debugger", "tool_call", "analyze results")
            run.analysis = await asyncio.wait_for(
                debugger.run(llm, run.results),
                timeout=PLANNER_TIMEOUT_SEC,
            )
            self._record(
                run, session, "debugger", "analysis_completed",
                "failure" if run.analysis.failed else "no remaining failure",
            )
            stage = "reporting"
            self._record(run, session, "reporter", "tool_call", "generate report")
            run.report = reporter.run(
                run.results, run.analysis,
                objective=run.objective, objective_status=run.objective_status,
                objective_reason=run.objective_reason,
            )
            save_text(run.run_id, "report.md", run.report)
            self._record(run, session, "reporter", "report_generated")

            # --- white-box fix loop: propose a patch, wait for a human ---
            stage = "fix_proposal"
            if (
                settings.fix_enabled
                and run.analysis is not None
                and run.analysis.failed
            ):
                await self._propose_fix(run, session)

        try:
            await asyncio.wait_for(_run_pipeline(), timeout=RUN_TIMEOUT_SEC)
            run.status = "completed"
            self._sync_budget(run)
            self._record(
                run, session, "orchestrator", "completed",
                f"{run.llm_calls}/{run.llm_budget.cap} LLM calls",
            )
        except asyncio.TimeoutError:
            self._fail_run(
                run, session, stage,
                RuntimeError(f"run exceeded {RUN_TIMEOUT_SEC}s budget"),
            )
            self._record(
                run, session, "orchestrator", "timed_out",
                f"exceeded {RUN_TIMEOUT_SEC}s budget during {stage}",
            )
        except Exception as exc:
            self._fail_run(run, session, stage, exc)
            self._record(run, session, "orchestrator", "fatal_error", run.error_message)
        finally:
            save_text(run.run_id, "events.log", "\n".join(run.events))
            try:
                session_store.persist(run.run_id)
            except Exception:
                pass
            try:
                await browser.close()
            except Exception:
                pass

    def _fail_run(
        self, run: RunState, session: Session, stage: str, exc: BaseException
    ) -> RunState:
        """Terminal infrastructure failure: the run died before it could
        produce a verdict. Records WHERE (stage) and WHY (sanitized,
        actionable) without touching objective_status — a dead provider is
        not a website failure, and the dashboard must show that split."""
        run.status = "failed"
        run.error_stage = stage or "unknown"
        if isinstance(exc, BudgetExhausted):
            run.error_kind = "budget"
        else:
            run.error_kind = (
                "infrastructure" if is_infrastructure_error(exc) else "agent"
            )
        run.error_message = describe_provider_error(exc)
        self._sync_budget(run)
        self._record(
            run, session, "orchestrator", "run_failed",
            f"{run.error_stage}: {run.error_message} "
            f"({run.llm_calls}/{run.llm_budget.cap} LLM calls)",
        )
        return run

    @staticmethod
    def _sync_budget(run: RunState) -> None:
        run.llm_calls = run.llm_budget.calls

    async def _propose_fix(self, run: RunState, session: Session) -> None:
        """Diagnose code, propose a diff, and park for human approval.

        Read-only against the workspace. The only write here is the
        fix.patch artifact for review — applying it requires the
        approve endpoint.
        """
        codebase_agent = CodebaseAgent()
        fixer = FixerAgent()
        tool = CodebaseTool(settings.workspace_root)
        self._record(run, session, "codebase", "tool_call", "locate failure code")
        try:
            context = await asyncio.wait_for(
                asyncio.to_thread(
                    codebase_agent.investigate, tool, run.results, run.analysis
                ),
                timeout=RECOVERY_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError:
            self._record(run, session, "codebase", "investigate_failed", "search timed out")
            return
        files = list(context.get("files", {}))
        self._record(
            run, session, "codebase", "code_located",
            ", ".join(files) if files else "no files located",
        )
        if not files:
            return
        failure_summary = (
            (run.analysis.summary or "")
            + "\n"
            + (run.analysis.probable_root_cause or "")
        )[:4000]
        self._record(run, session, "fixer", "tool_call", "propose patch")
        from ..tools.patch import PatchEngineError, build_diff

        proposal = None
        diff = ""
        feedback = ""
        llm = self.llm.scoped(run.llm_budget)
        for attempt in range(2):
            try:
                proposal = await asyncio.wait_for(
                    fixer.propose(llm, failure_summary, context, feedback=feedback),
                    timeout=PLANNER_TIMEOUT_SEC,
                )
            except asyncio.TimeoutError:
                self._record(run, session, "fixer", "propose_failed", "LLM timed out")
                break
            except Exception as exc:
                self._record(run, session, "fixer", "propose_failed", str(exc)[:300])
                break
            if not proposal.edits:
                # One bounded retry, same as a rejected edit: cheap models
                # sometimes return an explanation with no edit intents.
                self._record(run, session, "fixer", "propose_failed", "empty edit list")
                feedback = (
                    "Your previous reply contained no edits. Reply with the "
                    "FULL corrected JSON including the edits array."
                )
                proposal = None
                continue
            try:
                diff = await asyncio.to_thread(build_diff, tool, proposal.edits)
            except PatchEngineError as exc:
                # One bounded retry: hand the model the exact rejection.
                save_text(run.run_id, f"fix_rejected_attempt{attempt}.json",
                          proposal.model_dump_json(indent=2))
                self._record(
                    run, session, "fixer", "propose_failed",
                    f"{exc}; retrying once",
                )
                feedback = (
                    f"Your previous edit was rejected: {exc} "
                    f"Reply with the FULL corrected JSON."
                )
                proposal = None
                continue
            break
        if proposal is None or not diff:
            run.fix_status = "proposal_failed"
            self._record(run, session, "fixer", "propose_failed", "no applicable edit")
            return
        self._record(
            run, session, "fixer", "fix_proposed",
            "applies cleanly: True; awaiting human approval",
        )
        run.fix_diff = diff
        run.fix_explanation = proposal.explanation
        run.fix_base_commit = tool.base_commit()
        run.fix_status = "awaiting_approval"
        save_text(run.run_id, "fix.patch", diff)
        save_text(
            run.run_id, "fix_proposal.md",
            "## Proposed fix\n\n" + proposal.explanation + "\n\n"
            f"Verify with: `python -m pytest {' '.join(proposal.test_targets) or '-q'}`\n",
        )

    async def apply_fix(self, run_id: str) -> RunState:
        """Human-approved: verify base, branch, apply, test, re-verify.

        Terminal fix states keep every verdict distinct: verified needs
        BOTH pytest green and browser objective passed; otherwise
        tests_failed or tests_passed_browser_failed. Bounded re-proposal
        while attempts remain.
        """
        run = self.runs.get(run_id)
        if run is None:
            raise KeyError(f"unknown run {run_id}")
        if run.fix_status != "awaiting_approval":
            raise ValueError(f"fix is {run.fix_status}, not awaiting approval")
        session = session_store.get(run.run_id) or session_store.create(run.run_id)
        tool = CodebaseTool(settings.workspace_root)
        runner = TestRunnerTool(settings.workspace_root, timeout=settings.test_timeout_sec)
        run.fix_status = "applying"
        self._record(run, session, "fixer", "fix_approved", "applying on a new branch")
        try:
            if run.fix_base_commit:
                current = tool.base_commit()
                if current != run.fix_base_commit:
                    raise RuntimeError(
                        f"workspace moved since proposal "
                        f"({current[:8] or 'none'} != {run.fix_base_commit[:8]}); refusing"
                    )
                if tool.has_tracked_changes():
                    raise RuntimeError(
                        "workspace has uncommitted tracked changes since proposal; refusing"
                    )
            suffix = "" if run.fix_attempts == 0 else f"-r{run.fix_attempts}"
            branch = f"fix/{run.run_id[:8]}{suffix}"
            await asyncio.to_thread(tool.create_branch, branch)
            run.fix_branch = branch
            status = await asyncio.to_thread(tool.apply_patch, run.fix_diff)
            self._record(run, session, "fixer", "patch_applied", status[:200])
            touched = tool.diff_paths(run.fix_diff)
            if touched:
                await asyncio.to_thread(
                    tool.commit_files, touched,
                    f"fix: agent patch for run {run.run_id[:8]}",
                )
            result = await asyncio.to_thread(runner.run_pytest, [])
            run.fix_tests_output = result["output"]
            save_text(run.run_id, "fix_tests.log", result["output"])
            if not result["passed"]:
                run.fix_status = "tests_failed"
                self._record(
                    run, session, "test_runner", "tests_failed",
                    f"exit {result['returncode']}",
                )
                return self._finish_fix(run)
            self._record(run, session, "test_runner", "tests_passed", "pytest green")
            verified, reason = await self._verify_fix(run, session)
            run.fix_verify_summary = reason
            if verified:
                run.fix_status = "verified"
                self._record(run, session, "verifier", "objective_verified", reason)
                return self._finish_fix(run)
            run.fix_verify_summary = reason
            self._record(run, session, "verifier", "verify_failed", reason)
            if run.fix_attempts + 1 < MAX_FIX_ATTEMPTS:
                run.fix_attempts += 1
                self._record(
                    run, session, "fixer", "retrying_fix",
                    f"attempt {run.fix_attempts + 1} of {MAX_FIX_ATTEMPTS}",
                )
                await self._propose_fix(run, session)
                return self._finish_fix(run)
            # pytest green but the customer workflow still fails: keep the
            # two verdicts distinct instead of collapsing them.
            run.fix_status = "tests_passed_browser_failed"
            return self._finish_fix(run)
        except Exception as exc:
            run.fix_status = "apply_failed"
            self._record(run, session, "fixer", "apply_failed", str(exc)[:300])
            return self._finish_fix(run)

    def _finish_fix(self, run: RunState) -> RunState:
        self._sync_budget(run)
        save_text(run.run_id, "events.log", "\n".join(run.events))
        try:
            session_store.persist(run.run_id)
        except Exception:
            pass
        return run

    async def _verify_fix(self, run: RunState, session: Session) -> tuple[bool, str]:
        """Replay the approved plan against the patched app: does the
        original objective pass now? No LLM — deterministic re-execution."""
        from ..agents.browser_agent import BrowserAgent
        from ..agents.validator import ValidatorAgent

        browser_agent = BrowserAgent()
        validator = ValidatorAgent()
        browser = BrowserTool()
        results: list[StepResult] = []
        self._record(run, session, "verifier", "tool_call", "re-test original objective")
        try:
            await browser.start()
            for step in (run.plan.steps if run.plan else [])[:MAX_STEPS]:
                shot = str(run_dir(run.run_id) / f"verify_step_{step.id}.png")
                try:
                    result = await asyncio.wait_for(
                        browser_agent.execute(browser, step, shot),
                        timeout=STEP_TIMEOUT_SEC,
                    )
                except asyncio.TimeoutError:
                    result = StepResult(
                        step_id=step.id, status="error",
                        message=f"verify step timed out after {STEP_TIMEOUT_SEC}s",
                        evidence=TestEvidence(),
                    )
                results.append(result)
                self._record(
                    run, session, "verifier", f"verify_step_{step.id}",
                    f"-> {result.status}",
                )
        finally:
            try:
                await browser.close()
            except Exception:
                pass
        verdict = VerifierAgent().run(run.objective, run.success_step_ids, results)
        save_text(run.run_id, "verify.json", verdict.model_dump_json(indent=2))
        return verdict.verified, verdict.reason

    def reject_fix(self, run_id: str) -> RunState:
        run = self.runs.get(run_id)
        if run is None:
            raise KeyError(f"unknown run {run_id}")
        if run.fix_status != "awaiting_approval":
            raise ValueError(f"fix is {run.fix_status}, not awaiting approval")
        run.fix_status = "rejected"
        session = session_store.get(run.run_id) or session_store.create(run.run_id)
        self._record(run, session, "fixer", "fix_rejected", "human declined the patch")
        save_text(run.run_id, "events.log", "\n".join(run.events))
        return run


orchestrator = Orchestrator()
