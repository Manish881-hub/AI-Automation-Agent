"""Tier-2 planner evaluation: small real-LLM spot-checks, manual gate.

Calls the live planner a few times against fixed objectives and scores
each plan with the same deterministic rubric as Tier-1 (validation +
groundedness). This spends real quota — keep N small, run deliberately,
and never in CI. Tier-1 (tests/test_planner_eval.py) is the always-on net.

Usage, from the repo root with services configured:

    backend/.venv/bin/python scripts/eval_planner_live.py [--calls 3]
"""

import argparse
import asyncio
import sys
import time

sys.path.insert(0, "backend")

from app.agents.planner import PlannerAgent  # noqa: E402
from app.eval.scorer import score_plan  # noqa: E402
from app.schemas import WebsiteSnapshot  # noqa: E402
from app.services.llm import LLM  # noqa: E402

CASES = [
    {
        "url": "http://localhost:8921/",
        "objective": "verify that a user can sign in and reach the dashboard",
        "snapshot": {
            "url": "http://localhost:8921/",
            "title": "Acme Portal — Sign in",
            "headings": ["Welcome back"],
            "buttons": ["Sign in", "Forgot password?", "Create account"],
            "inputs": [
                {"label": "Email", "type": "email"},
                {"label": "Password", "type": "password"},
            ],
            "links": [],
        },
    },
    {
        "url": "https://example.com/",
        "objective": ("verify that the homepage loads and contains "
                      "the expected Example Domain text"),
        "snapshot": {
            "url": "https://example.com/",
            "title": "Example Domain",
            "headings": ["Example Domain"],
            "buttons": [],
            "inputs": [],
            "links": [],
            "visible_text": "Example Domain",
        },
    },
]


async def main(calls: int) -> int:
    llm = LLM()
    planner = PlannerAgent()
    failures = 0
    elapsed: list[float] = []
    for i in range(calls):
        case = CASES[i % len(CASES)]
        snap = WebsiteSnapshot.model_validate(case["snapshot"])
        started = time.monotonic()
        try:
            plan = await planner.run(llm, case["url"], case["objective"],
                                     website_context=snap)
        except Exception as exc:  # noqa: BLE001 — probe reports, never crashes
            print(f"[{i}] planner call failed: {type(exc).__name__}: {str(exc)[:150]}")
            failures += 1
            continue
        finally:
            elapsed.append(time.monotonic() - started)
        scored = score_plan(plan, snap, case["objective"], allowed_url=case["url"])
        flag = "APPROVED" if scored["approved"] else "REJECTED"
        print(f"[{i}] {flag} groundedness={scored['groundedness']} "
              f"steps={len(plan.steps)} {elapsed[-1]:.1f}s "
              f"reasons={scored['reasons'][:2]}")
        if not scored["approved"]:
            failures += 1
    avg = sum(elapsed) / len(elapsed) if elapsed else 0.0
    print(f"live probe: {calls - failures}/{calls} approved, "
          f"avg {avg:.1f}s/call (pass@1={(calls - failures) / calls:.2f})")
    return 1 if failures else 0


if __name__ == "__main__":
    args = argparse.ArgumentParser()
    args.add_argument("--calls", type=int, default=2)
    sys.exit(asyncio.run(main(args.parse_args().calls)))
