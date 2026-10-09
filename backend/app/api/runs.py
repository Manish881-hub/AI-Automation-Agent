import asyncio
from fastapi import APIRouter, HTTPException, Query
from ..schemas import TestRunRequest, TestRunResponse, normalize_url
from ..services.history import read_history
from ..services.orchestrator import orchestrator

router = APIRouter(prefix="/api/runs", tags=["runs"])


@router.get("", response_model=None)
async def list_runs(limit: int = Query(default=50, ge=1, le=200)):
    """Durable history: terminal-transition summaries, newest first.

    Survives backend restarts (the index is append-only on disk), unlike
    the live per-run endpoints which only know in-memory runs.
    """
    return {"runs": read_history(limit=limit)}


@router.post("", response_model=TestRunResponse, status_code=202)
async def create_run(request: TestRunRequest):
    # request.url is already normalized by TestRunRequest validation;
    # normalize again defensively (idempotent) before storing.
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
        error_stage=run.error_stage,
        error_kind=run.error_kind,
        error_message=run.error_message,
        llm_calls=run.llm_calls,
        fix_status=run.fix_status,
        fix_verify_summary=run.fix_verify_summary,
        fix_explanation=run.fix_explanation,
        fix_branch=run.fix_branch,
        fix_diff=run.fix_diff,
    )


@router.post("/{run_id}/fix/approve")
async def approve_fix(run_id: str):
    """Human approval gate: apply the proposed patch on a branch and test it."""
    try:
        run = await orchestrator.apply_fix(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {
        "run_id": run.run_id,
        "fix_status": run.fix_status,
        "fix_branch": run.fix_branch,
        "fix_explanation": run.fix_explanation,
    }


@router.post("/{run_id}/fix/reject")
async def reject_fix(run_id: str):
    """Human declines the proposed patch; it is never applied."""
    try:
        run = orchestrator.reject_fix(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"run_id": run.run_id, "fix_status": run.fix_status}


@router.get("/{run_id}/events")
async def get_events(run_id: str):
    run = orchestrator.runs.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"events": run.events}
