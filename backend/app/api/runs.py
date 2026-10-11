import asyncio
import hmac
from fastapi import APIRouter, HTTPException, Query, Request
from ..config import settings
from ..schemas import TestRunRequest, TestRunResponse, normalize_url
from ..services.history import read_history
from ..services.orchestrator import orchestrator

router = APIRouter(prefix="/api/runs", tags=["runs"])


def require_approval(request: Request) -> None:
    """Authorize a human approval decision. Fails closed: without a
    configured APPROVAL_TOKEN every decision is refused, so an
    unconfigured server can never apply code.

    Two equivalent credentials: the X-Approval-Token header (scripts, API
    clients) or an operator session cookie from POST /api/auth/login (the
    browser UI, which never sees the token itself). Compared in constant
    time; failures say nothing about the expected value.
    """
    from .auth import credentials_ok, session_authorized

    if credentials_ok(request.headers.get("x-approval-token", "")):
        return
    if session_authorized(request):
        return
    raise HTTPException(status_code=401, detail="approval not authorized")


@router.get("", response_model=None)
async def list_runs(request: Request, limit: int = Query(default=50, ge=1, le=200)):
    """Durable history: terminal-transition summaries, newest first.

    Survives backend restarts (the index is append-only on disk), unlike
    the live per-run endpoints which only know in-memory runs. Operator
    authentication required: history contains client objectives and diffs.
    """
    require_approval(request)
    return {"runs": read_history(limit=limit)}


@router.post("", response_model=TestRunResponse, status_code=202)
async def create_run(request: Request, body: TestRunRequest):
    # Every run spends shared LLM budget, so creating one is a privileged
    # operator action like approval — not an anonymous endpoint.
    require_approval(request)
    # body.url is already normalized by TestRunRequest validation;
    # normalize again defensively (idempotent) before storing.
    run = orchestrator.create_run(normalize_url(str(body.url)), body.objective)
    asyncio.create_task(orchestrator.execute(run.run_id))
    return TestRunResponse(run_id=run.run_id, status=run.status, objective=run.objective, url=run.url)


@router.get("/{run_id}", response_model=TestRunResponse)
async def get_run(run_id: str, request: Request):
    require_approval(request)
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
async def approve_fix(run_id: str, request: Request):
    """Human approval gate: apply the proposed patch on a branch and test it."""
    require_approval(request)
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
async def reject_fix(run_id: str, request: Request):
    """Human declines the proposed patch; it is never applied."""
    require_approval(request)
    try:
        run = orchestrator.reject_fix(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"run_id": run.run_id, "fix_status": run.fix_status}


@router.post("/{run_id}/cancel")
async def cancel_run(run_id: str, request: Request):
    """Operator stop: cooperative cancellation of a live run or fix.

    Same authorization as approval — stopping work is as sensitive as
    approving it. Unknown runs 404; already-terminal runs 409 (except an
    already-cancelled run, which repeats its state idempotently); live
    runs accept the request and reach `cancelled` at the next boundary.
    """
    require_approval(request)
    try:
        return orchestrator.request_cancel(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/{run_id}/events")
async def get_events(run_id: str, request: Request):
    require_approval(request)
    run = orchestrator.runs.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"events": run.events}
