import asyncio
from dataclasses import dataclass, field
from uuid import uuid4
from ..agents.planner import PlannerAgent
from ..agents.plan_validator import PlanValidatorAgent
from ..agents.browser_agent import BrowserAgent
from ..agents.debugger import DebuggerAgent
from ..agents.reconnaissance import ReconnaissanceAgent
from ..agents.recovery import RecoveryAgent
from ..agents.reporter import ReporterAgent
from ..schemas import TestPlan, StepResult, FailureAnalysis, TestEvidence, WebsiteSnapshot
from ..services.llm import LLM
from ..tools.browser import BrowserTool
from ..services.artifacts import run_dir, save_text
from ..services.session import session_store, Session


MAX_STEPS = 12
PLANNER_TIMEOUT_SEC = 120
PLANNER_MAX_REVISIONS = 2
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
    analysis: FailureAnalysis | None = None
    report: str | None = None
    events: list[str] = field(default_factory=list)


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

        async def _run_pipeline():
            # --- reconnaissance: understand the page before planning ---
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
            self._record(run, session, "planner", "tool_call", "generate test plan")
            try:
                run.plan = await asyncio.wait_for(
                    planner.run(self.llm, run.url, run.objective, website_context=run.website),
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
                    run.plan = await asyncio.wait_for(
                        planner.revise(
                            self.llm, run.url, run.objective, run.website,
                            run.plan, validation.reasons,
                        ),
                        timeout=PLANNER_TIMEOUT_SEC,
                    )
                except asyncio.TimeoutError:
                    raise RuntimeError(
                        f"planner revision timed out after {PLANNER_TIMEOUT_SEC}s"
                    )
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
                            recovery_agent.run(self.llm, step, available, result.message),
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

            self._record(run, session, "debugger", "tool_call", "analyze results")
            run.analysis = await asyncio.wait_for(
                debugger.run(self.llm, run.results),
                timeout=PLANNER_TIMEOUT_SEC,
            )
            self._record(
                run, session, "debugger", "analysis_completed",
                "failure" if run.analysis.failed else "no remaining failure",
            )
            self._record(run, session, "reporter", "tool_call", "generate report")
            run.report = reporter.run(
                run.results, run.analysis,
                objective=run.objective, objective_status=run.objective_status,
                objective_reason=run.objective_reason,
            )
            save_text(run.run_id, "report.md", run.report)
            self._record(run, session, "reporter", "report_generated")

        try:
            await asyncio.wait_for(_run_pipeline(), timeout=RUN_TIMEOUT_SEC)
            run.status = "completed"
            self._record(run, session, "orchestrator", "completed")
        except asyncio.TimeoutError:
            run.status = "failed"
            self._record(
                run, session, "orchestrator", "timed_out",
                f"exceeded {RUN_TIMEOUT_SEC}s budget",
            )
        except Exception as exc:
            run.status = "failed"
            self._record(run, session, "orchestrator", "fatal_error", str(exc))
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


orchestrator = Orchestrator()
