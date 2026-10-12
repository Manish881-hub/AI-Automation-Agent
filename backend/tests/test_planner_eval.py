"""Tier-1 planner evaluation: deterministic scoring over a committed corpus.

No LLM, no network — every case replays a real recorded plan through the
live validator plus the groundedness rubric. This locks decision quality:
any planner/validator change that degrades recorded good plans, or starts
approving recorded bad ones, fails here before it reaches a live run.

Corpus lives in tests/eval_corpus/*.json: real artifact pairs plus one
synthetic origin-violation proof. See backend/app/eval/scorer.py.
"""

import glob
import json
from pathlib import Path

from app.eval.scorer import score_plan
from app.schemas import TestPlan, WebsiteSnapshot

CORPUS = sorted(glob.glob(str(Path(__file__).parent / "eval_corpus" / "*.json")))


def _load(path: str) -> tuple[dict, TestPlan, WebsiteSnapshot | None]:
    case = json.load(open(path))
    plan = TestPlan.model_validate(case["plan"])
    snapshot = None
    if case.get("snapshot"):
        try:
            snapshot = WebsiteSnapshot.model_validate(case["snapshot"])
        except Exception:
            snapshot = None
    return case, plan, snapshot


def test_corpus_approval_matches_recorded_verdicts():
    for path in CORPUS:
        case, plan, snapshot = _load(path)
        scored = score_plan(
            plan, snapshot, case["objective"], allowed_url=case.get("allowed_url", ""),
        )
        assert scored["approved"] is case["expect"]["approved"], (
            f"{case['id']}: approved={scored['approved']} "
            f"reasons={scored['reasons']}"
        )


def test_corpus_groundedness_floors():
    for path in CORPUS:
        case, plan, snapshot = _load(path)
        scored = score_plan(
            plan, snapshot, case["objective"], allowed_url=case.get("allowed_url", ""),
        )
        assert scored["groundedness"] >= case["expect"]["min_groundedness"], (
            f"{case['id']}: groundedness={scored['groundedness']}"
        )


def test_corpus_average_groundedness_reported():
    scored = []
    for path in CORPUS:
        case, plan, snapshot = _load(path)
        scored.append(score_plan(
            plan, snapshot, case["objective"], allowed_url=case.get("allowed_url", ""),
        )["groundedness"])
    avg = sum(scored) / len(scored)
    print(f"\ncorpus groundedness: avg={avg:.3f} min={min(scored):.3f} n={len(scored)}")
    # Ratchet, not a wish: today's measured floor. Raise it as plans improve.
    assert avg >= 0.6
