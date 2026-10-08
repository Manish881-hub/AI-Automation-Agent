import asyncio
from dataclasses import dataclass, field
from uuid import uuid4
from ..agents.planner import PlannerAgent
from ..agents.browser_agent import BrowserAgent
from ..agents.debugger import DebuggerAgent
from ..agents.reporter import ReporterAgent
from ..schemas import TestPlan, StepResult, FailureAnalysis
from ..services.llm import LLM
from ..tools.browser import BrowserTool
from ..services.artifacts import run_dir, save_text


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
        planner = PlannerAgent()
        browser_agent = BrowserAgent()
        debugger = DebuggerAgent()
        reporter = ReporterAgent()
        browser = BrowserTool()
        try:
            run.events.append("planner: generating test plan")
            run.plan = await planner.run(self.llm, run.url, run.objective)
            save_text(run.run_id, "plan.json", run.plan.model_dump_json(indent=2))
            run.events.append(f"planner: generated {len(run.plan.steps)} steps")

            await browser.start()
            for step in run.plan.steps:
                run.events.append(f"browser: executing step {step.id} ({step.action})")
                screenshot = str(run_dir(run.run_id) / f"step_{step.id}.png")
                result = await browser_agent.execute(browser, step, screenshot)
                run.results.append(result)
                run.events.append(f"validator: step {step.id} -> {result.status}")
                if result.status != "passed":
                    # Continue collecting evidence for the remaining plan where safe.
                    run.events.append(f"browser: failure evidence captured for step {step.id}")

            run.events.append("debugger: analyzing results")
            run.analysis = await debugger.run(self.llm, run.results)
            run.events.append("reporter: generating report")
            run.report = reporter.run(run.results, run.analysis)
            save_text(run.run_id, "report.md", run.report)
            save_text(run.run_id, "events.log", "\n".join(run.events))
            run.status = "completed"
            run.events.append("orchestrator: completed")
        except Exception as exc:
            run.status = "failed"
            run.events.append(f"orchestrator: fatal error: {exc}")
        finally:
            await browser.close()


orchestrator = Orchestrator()
