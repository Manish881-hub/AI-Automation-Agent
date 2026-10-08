import asyncio
from dataclasses import dataclass, field
from uuid import uuid4
from ..agents.planner import PlannerAgent
from ..agents.browser_agent import BrowserAgent
from ..agents.debugger import DebuggerAgent
from ..agents.reporter import ReporterAgent
from ..schemas import TestPlan, StepResult, FailureAnalysis, TestEvidence
from ..services.llm import LLM
from ..tools.browser import BrowserTool
from ..services.artifacts import run_dir, save_text
from ..services.session import session_store


MAX_STEPS = 12
PLANNER_TIMEOUT_SEC = 30
STEP_TIMEOUT_SEC = 45
RUN_TIMEOUT_SEC = 300


@dataclass
class RunState:
    run_id: str
    url: str
    objective: str
    status: str = "queued"
    plan: TestPlan | None = None
    results: list[StepResult] = field(default_factory=list)
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

    async def execute(self, run_id: str):
        run = self.runs[run_id]
        run.status = "running"
        run.events.append("orchestrator: started")
        session = session_store.create(run.run_id)
        session.record("orchestrator", "started", detail=run.objective)
        planner = PlannerAgent()
        browser_agent = BrowserAgent()
        debugger = DebuggerAgent()
        reporter = ReporterAgent()
        browser = BrowserTool()

        async def _run_pipeline():
            run.events.append("planner: generating test plan")
            session.record("planner", "tool_call", detail="generate test plan")
            run.plan = await asyncio.wait_for(
                planner.run(self.llm, run.url, run.objective),
                timeout=PLANNER_TIMEOUT_SEC,
            )
            save_text(run.run_id, "plan.json", run.plan.model_dump_json(indent=2))
            run.events.append(f"planner: generated {len(run.plan.steps)} steps")
            session.record("planner", "decision", detail=f"{len(run.plan.steps)} steps")

            await browser.start()
            for step in run.plan.steps[:MAX_STEPS]:
                run.events.append(f"browser: executing step {step.id} ({step.action})")
                session.record("browser", "tool_call", detail=f"step {step.id} ({step.action})")
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
                run.results.append(result)
                run.events.append(f"validator: step {step.id} -> {result.status}")
                session.record("validator", "decision", detail=f"step {step.id} -> {result.status}")

            run.events.append("debugger: analyzing results")
            session.record("debugger", "tool_call", detail="analyze results")
            run.analysis = await asyncio.wait_for(
                debugger.run(self.llm, run.results),
                timeout=PLANNER_TIMEOUT_SEC,
            )
            run.events.append("reporter: generating report")
            session.record("reporter", "tool_call", detail="generate report")
            run.report = reporter.run(run.results, run.analysis)
            save_text(run.run_id, "report.md", run.report)

        try:
            await asyncio.wait_for(_run_pipeline(), timeout=RUN_TIMEOUT_SEC)
            run.status = "completed"
            run.events.append("orchestrator: completed")
            session.record("orchestrator", "completed", detail=run.status)
        except asyncio.TimeoutError:
            run.status = "failed"
            run.events.append(f"orchestrator: run exceeded {RUN_TIMEOUT_SEC}s budget")
            session.record("orchestrator", "timeout", detail=f"exceeded {RUN_TIMEOUT_SEC}s budget")
        except Exception as exc:
            run.status = "failed"
            run.events.append(f"orchestrator: fatal error: {exc}")
            session.record("orchestrator", "fatal_error", detail=str(exc))
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
