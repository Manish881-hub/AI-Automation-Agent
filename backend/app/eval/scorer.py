"""Deterministic plan-quality scoring: judge decisions without an LLM.

The planner is non-deterministic, but its OUTPUTS are data. This module
scores a TestPlan against the snapshot it was planned from and the
objective it serves — same rules every run, no quota spent. It powers the
Tier-1 eval suite (committed corpus, CI-safe) and the Tier-2 live probe
(small real-LLM sample, manual gate).

Checks mirror the validator's contract plus groundedness — whether each
semantic target actually appears in the observed page. A plan can pass
validation yet score low on groundedness (hallucinated elements the
browser will never find); that gap is reported, not hidden.
"""

from ..agents.plan_validator import PlanValidatorAgent, objective_keywords
from ..schemas import TestPlan, WebsiteSnapshot

MAX_STEPS_DEFAULT = 8


def _snapshot_text(snapshot: WebsiteSnapshot | None) -> str:
    if snapshot is None:
        return ""
    parts = [
        snapshot.title or "",
        *(snapshot.headings or []),
        *(snapshot.buttons or []),
        " ".join(
            (i.label or "") + " " + (i.placeholder or "") + " " + (i.name or "")
            for i in (snapshot.inputs or [])
        ),
        " ".join(
            (l.text if hasattr(l, "text") else str(l)) for l in (snapshot.links or [])
        ),
        snapshot.visible_text or "",
    ]
    return "\n".join(parts).lower()


def groundedness(plan: TestPlan, snapshot: WebsiteSnapshot | None) -> float:
    """Fraction of actionable targets grounded in the observed snapshot.

    Navigate steps are exempt (URLs, not page text). Empty target sets
    score 1.0 — nothing to hallucinate. Case-insensitive substring match
    keeps it deterministic and cheap.
    """
    if snapshot is None:
        return 1.0
    haystack = _snapshot_text(snapshot)
    targets = [
        s.target for s in plan.steps
        if s.action in ("click", "fill", "press", "assert") and (s.target or "").strip()
    ]
    if not targets:
        return 1.0
    hit = sum(1 for t in targets if t.strip().lower() in haystack)
    return hit / len(targets)


def score_plan(
    plan: TestPlan,
    snapshot: WebsiteSnapshot | None,
    objective: str,
    max_steps: int = MAX_STEPS_DEFAULT,
    allowed_url: str = "",
) -> dict:
    """Score one plan. Keys: approved, reasons, success_step_ids,
    groundedness, checks{...}. Pure function — safe to run anywhere."""
    validation = PlanValidatorAgent().validate(
        plan, objective, max_steps, allowed_url=allowed_url
    )
    steps = plan.steps or []
    checks = {
        "starts_with_navigate": bool(steps) and steps[0].action == "navigate",
        "ends_with_assert": bool(steps) and steps[-1].action == "assert",
        "within_step_limit": bool(steps) and len(steps) <= max_steps,
        "has_success_signal": bool(validation.success_step_ids),
    }
    return {
        "approved": validation.approved,
        "reasons": validation.reasons,
        "success_step_ids": validation.success_step_ids,
        "groundedness": round(groundedness(plan, snapshot), 3),
        "keywords": objective_keywords(objective),
        "checks": checks,
    }
