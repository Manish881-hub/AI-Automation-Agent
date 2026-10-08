import asyncio
import re
from fastapi import APIRouter, HTTPException
from ..schemas import TestRunRequest, TestRunResponse
from ..services.orchestrator import orchestrator

router = APIRouter(prefix="/api/runs", tags=["runs"])

_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def normalize_url(url: str) -> str:
    """Strip chat/markdown wrapping so internal state never holds anything
    but a raw URL. The agent must never guess between `http://x` and
    `[http://x](http://x)` — normalize once, at the boundary."""
    url = url.strip()
    match = _MARKDOWN_LINK.fullmatch(url)
    if match:
        url = match.group(2).strip()
    return url


@router.post("", response_model=TestRunResponse, status_code=202)
async def create_run(request: TestRunRequest):
    run = orchestrator.create_run(normalize_url(str(request.url)), request.objective)
    asyncio.create_task(orchestrator.execute(run.run_id))
    return TestRunResponse(run_id=run.run_id, status=run.status, objective=run.objective, url=run.url)


@router.get("/{run_id}", response_model=TestRunResponse)
async def get_run(run_id: str):
    run = orchestrator.runs.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return TestRunResponse(
        run_id=run.run_id,
        status=run.status,
        objective=run.objective,
        url=run.url,
        plan=run.plan,
        results=run.results,
        analysis=run.analysis,
        report=run.report,
        website=run.website,
        objective_status=run.objective_status,
        objective_reason=run.objective_reason,
    )


@router.get("/{run_id}/events")
async def get_events(run_id: str):
    run = orchestrator.runs.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"events": run.events}
