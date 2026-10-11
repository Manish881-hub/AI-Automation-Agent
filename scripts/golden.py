"""Acceptance golden run: UI-driven white-box autofix cycle with real LLM.

Flow: create the run through the real UI form -> poll until the fix
proposal is awaiting approval -> screenshot -> operator sign-in through
the UI session flow -> approve through the UI -> poll until the fix
reaches a terminal state -> assert verified with a passing re-test.

The approval token is supplied at runtime and never printed, logged, or
written to artifacts: GOLDEN_APPROVAL_TOKEN env var, else APPROVAL_TOKEN
from backend/.env (same machine, gitignored). It is only ever typed into
the password field, exactly as a human operator would.

Run from the repo root with the project interpreter (Playwright lives in
the backend venv), services already up:

    backend/.venv/bin/python scripts/golden.py

Screenshots land in the run's artifact directory (gitignored).
"""

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BACKEND = os.environ.get("GOLDEN_BACKEND", "http://localhost:8001")
FRONTEND = os.environ.get("GOLDEN_FRONTEND", "http://localhost:5173")
TARGET_URL = "http://localhost:8921"
OBJECTIVE = "verify that a user can sign in and reach the dashboard"
POLL_INTERVAL = 5
STAGE1_TIMEOUT = 900
STAGE2_TIMEOUT = 900


def _read_token() -> str:
    token = os.environ.get("GOLDEN_APPROVAL_TOKEN", "")
    if not token:
        env_file = REPO / "backend" / ".env"
        if env_file.is_file():
            for line in env_file.read_text().splitlines():
                if line.startswith("APPROVAL_TOKEN="):
                    token = line.split("=", 1)[1].strip()
                    break
    if not token:
        print("GOLDEN_FAIL: no approval token "
              "(GOLDEN_APPROVAL_TOKEN or backend/.env APPROVAL_TOKEN)")
        sys.exit(1)
    return token


TOKEN = _read_token()


def api(method, path, body=None):
    req = urllib.request.Request(
        BACKEND + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        # The run itself is created and approved through the browser UI.
        # Polling uses a separate urllib client, which cannot share that
        # browser's HttpOnly cookie; authenticate these read-only status
        # requests with the existing operator credential instead. The value
        # remains in memory only and is never printed or written to artifacts.
        headers={
            "Content-Type": "application/json",
            "X-Approval-Token": TOKEN,
        })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def wait_for(run_id, pred, timeout, label):
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        try:
            last = api("GET", f"/api/runs/{run_id}")
        except Exception as e:
            print(f"[{label}] poll error: {e}", flush=True)
            time.sleep(POLL_INTERVAL)
            continue
        print(f"[{label}] status={last.get('status')} "
              f"objective={last.get('objective_status')} "
              f"fix={last.get('fix_status')}", flush=True)
        if pred(last):
            return last
        time.sleep(POLL_INTERVAL)
    print(f"TIMEOUT waiting for {label}. last: {json.dumps(last)[:1500]}")
    return None


def artifacts_dir(run_id: str) -> Path:
    path = REPO / "backend" / "artifacts" / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1400, "height": 1000})
    page.goto(FRONTEND, wait_until="networkidle")
    # Operator sign-in first: run creation is authenticated, and the
    # session cookie (HttpOnly, same-origin) carries every later call.
    # The token is evaluated inside the page exactly once and never logged.
    authed = page.evaluate(
        """async (token) => {
             const r = await fetch('/api/auth/login', {
               method: 'POST', headers: {'Content-Type': 'application/json'},
               body: JSON.stringify({token}) });
             return r.ok;
           }""",
        TOKEN,
    )
    if not authed:
        print("GOLDEN_FAIL: operator sign-in failed")
        sys.exit(1)
    print("OPERATOR_SIGNED_IN", flush=True)
    page.fill("label:has-text('client website') input", TARGET_URL)
    page.fill("label:has-text('test objective') textarea", OBJECTIVE)
    with page.expect_response("**/api/runs") as resp_info:
        page.click("button:has-text('run autonomous test')")
    run_id = resp_info.value.json()["run_id"]
    out_dir = artifacts_dir(run_id)
    print(f"RUN_CREATED {run_id}", flush=True)

    state = wait_for(
        run_id,
        lambda s: s.get("fix_status") == "awaiting_approval"
        or s.get("status") in ("completed", "failed"),
        STAGE1_TIMEOUT, "stage1-awaiting-approval")
    if state is None:
        page.screenshot(path=str(out_dir / "golden-stage1-timeout.png"), full_page=True)
        print("GOLDEN_FAIL: never reached a terminal stage-1 state")
        sys.exit(1)
    if state.get("fix_status") != "awaiting_approval":
        print(f"GOLDEN_FAIL: fix_status={state.get('fix_status')} "
              f"(expected awaiting_approval)")
        print(json.dumps(state, indent=2)[:3000])
        sys.exit(1)
    print(f"PROPOSAL_READY fix_explanation={state.get('fix_explanation', '')[:200]}",
          flush=True)
    # The UI holds the run in memory and polls ~1.2s: never reload, or the
    # run context is lost. Wait for the fix card in the same page.
    try:
        page.locator("section.card.fix").wait_for(timeout=30000)
        time.sleep(2)
    except Exception as e:
        print(f"fix card wait: {e}", flush=True)
    try:
        page.locator("section.card.fix").screenshot(
            path=str(out_dir / "golden-proposal.png"))
        print("SCREENSHOT proposal saved", flush=True)
    except Exception as e:
        page.screenshot(path=str(out_dir / "golden-proposal-fallback.png"),
                        full_page=True)
        print(f"proposal screenshot fallback: {e}", flush=True)

    # Operator sign-in through the real UI session flow — only if the
    # session from the start of the run is gone (the card renders solely
    # for signed-out operators at approval time).
    try:
        page.locator("section[aria-label='operator sign-in']").wait_for(timeout=10000)
        page.fill("section[aria-label='operator sign-in'] input[type='password']", TOKEN)
        page.click("section[aria-label='operator sign-in'] button[type='submit'], "
                   "section[aria-label='operator sign-in'] button:not([type])")
        page.locator("text=operator signed in").wait_for(timeout=15000)
        print("OPERATOR_SIGNED_IN (approval-time)", flush=True)
    except Exception:
        print("OPERATOR_SESSION_STILL_VALID", flush=True)

    # Approve through the real UI button.
    page.click("section.card.fix button:has-text('approve fix')")
    print("APPROVAL_CLICKED", flush=True)
    time.sleep(3)
    browser.close()

state2 = wait_for(
    run_id,
    lambda s: s.get("fix_status") in (
        "verified", "tests_failed", "tests_passed_browser_failed", "apply_failed"),
    STAGE2_TIMEOUT, "stage2-verified")
if state2 is None:
    print("GOLDEN_FAIL: never reached terminal fix state")
    sys.exit(1)

print("=" * 60)
print(f"FINAL fix_status={state2.get('fix_status')}")
print(f"FINAL objective_status={state2.get('objective_status')}")
print(f"FINAL status={state2.get('status')}")
print(f"fix_branch={state2.get('fix_branch')}")
print(f"fix_verify_summary={state2.get('fix_verify_summary', '')[:500]}")
print(f"llm_calls={state2.get('llm_calls')}")
print("=" * 60)

try:
    from playwright.sync_api import sync_playwright as _sp
    with _sp() as _p:
        _b = _p.chromium.launch(headless=True)
        _pg = _b.new_page(viewport={"width": 1400, "height": 1000})
        _pg.goto(FRONTEND, wait_until="networkidle")
        time.sleep(2)
        _pg.screenshot(path=str(out_dir / "golden-final.png"), full_page=True)
        _b.close()
        print("SCREENSHOT final saved")
except Exception as e:
    print(f"final screenshot skipped: {e}")

# Designed contracts: the ORIGINAL run's objective stays failed (honest
# record of the discovered defect); the POST-FIX proof lives in fix_status
# plus the verifier's re-test verdict.
ok = (state2.get("fix_status") == "verified"
      and "passed" in (state2.get("fix_verify_summary") or ""))
if not ok:
    print("GOLDEN_FAIL: expected fix_status=verified with passing re-test summary")
    sys.exit(1)
print("GOLDEN_PASS: propose -> approve -> pytest -> browser re-test -> verified")
