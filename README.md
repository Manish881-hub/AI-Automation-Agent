# AI Test Automation Agent

An MVP for an autonomous web testing and debugging agent.

## Architecture

`React UI → FastAPI → Orchestrator → Planner Agent → Browser Agent → Validator → Debugger Agent → Reporter`

Playwright is the deterministic browser execution layer. The LLM plans tests and performs failure analysis rather than directly controlling the browser.

## MVP scope

- natural-language test objective
- LLM-generated test plan
- Playwright browser execution
- screenshots per step
- console + HTTP error capture
- pass/fail validation
- LLM-based root-cause analysis
- markdown report
- React live execution dashboard

## Run locally

Three processes: demo app (`:8921`), backend (`:8001`), frontend (`:5173`).

### 1. Demo app (the intentionally buggy fixture)

```bash
python3 demo/autofix-app/server.py 8921
```

Serves the login site plus a real `POST /api/login`. `main` keeps the
`KeyError` defect in `demo/autofix-app/backend/auth.py` on purpose so the
failure reproduces every run. Do not fix it on `main`.

### 2. Backend

```bash
cd backend
cp .env.example .env   # then set OPENAI_API_KEY (never commit .env)
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
uvicorn app.main:app --host 127.0.0.1 --port 8001
```

Port `8001` is used because `8000` is commonly occupied on shared machines;
the frontend proxy/`VITE_API_URL` must point at whichever port you choose.
Required `.env` keys for the white-box loop:

```text
OPENAI_BASE_URL=https://openrouter.ai/api/v1
OPENAI_MODEL=<vendor/model:free>   # e.g. a free OpenRouter text model
WORKSPACE_ROOT=<repo>/demo/autofix-app
```

Check health: `curl http://localhost:8001/health` → `{"status":"ok"}`.
One cheap LLM ping before a full run saves time if free-model quota
(`429 free-models-per-day`) is exhausted — wait for reset, don't loop.

Troubleshooting:
- `pip install` fails building `greenlet` (`g++` missing, or packages land
  under Python 3.14 user site-packages): skip the reinstall. The repo's
  `backend/.venv` (Python 3.12) already has every dependency — run it
  directly with `backend/.venv/bin/python -m uvicorn ...` instead of
  activating and reinstalling.
- `address already in use` on `:8001`: the backend is already running.
  Reuse it (check `/health`) or stop that process before starting another.

### 3. Frontend

```bash
cd frontend
npm install
VITE_API_URL=http://localhost:8001 npm run dev -- --host 127.0.0.1 --port 5173
```

Open `http://localhost:5173`.

### 4. Run the white-box demo

- Client website: `http://localhost:8921`
- Objective: `verify that a user can sign in and reach the dashboard`
- Click **run autonomous test**, wait for the **ai proposed fix** card
  (`status: awaiting_approval`), review the diff, then **approve fix**.
- The agent branches, patches, runs pytest, re-tests in the browser, and
  reports `verified` only if both gates pass.

A scripted version of this flow lives at `/tmp/opencode/golden.py`
(local machine only, not committed).

## Golden run walkthrough (reference: run `463d968d`)

Proven end-to-end with a real LLM through the UI:

1. **Failure discovery** — plan executes; recovery retries `Sign in` as
   `Log in`, but the dashboard assertion still fails, so the business
   objective is honestly reported as failed (recovery success ≠ objective
   success).
2. **Source/test discovery** — codebase agent locates `backend/auth.py`,
   `backend/tests/test_auth.py`, `server.py`; tests are passed to the fixer
   as the behavioral contract.
3. **Structured proposal** — the LLM returns `PatchEdit` intents
   (`provider_response["token"]` → `.get("token")`, `limited: False` →
   `True`), never patch syntax; the deterministic patch engine builds the
   diff and it passes `git apply --check`.
4. **Human approval** — the UI fix card shows the explanation, diff, and
   approve/reject buttons; nothing is applied before approval.
5. **Apply + test + re-test** — dedicated branch `fix/463d968d`, patch
   applied and committed (`M backend/auth.py`), pytest **2 passed**,
   browser replay **7/7 steps passed**.
6. **Verified** — `fix_status=verified`.

Evidence for the reference run lives under
`backend/artifacts/463d968d-e5de-4952-a9f3-c2a60f0132bf/` (gitignored):
`fix.patch`, `fix_proposal.md`, `fix_tests.log`, `verify.json`
(`verified:true`), `events.log`, step screenshots, plus
`golden-proposal.png` / `golden-final.png` UI captures.

### Verdict semantics (do not "simplify")

- The **original run keeps `objective_status=failed`** — that is the honest
  record that the defect was found.
- The **re-test reports `objective_status=passed`** inside `verify.json`
  and `fix_verify_summary`.
- The fix becomes **`verified`** only when pytest is green AND the browser
  replay passes. Never derive one verdict from the other.

### Demo-baseline protection

- `main` stays deliberately buggy (the fixture's failing test is the demo).
- Each approved run creates its own `fix/<runid>` branch; the reference fix
  is on `fix/463d968d` for inspection.
- Do not merge a generated fix into `main` unless you intend to change the
  fixture. To re-demo from scratch, stay on `main` and start a fresh run.

## Engineering notes

- **Nested-workspace `git apply` scoping** (`backend/app/tools/codebase.py`):
  `git apply` resolves patch paths against the repo toplevel and silently
  *skips* out-of-scope patches with exit 0. Because the workspace
  (`demo/autofix-app`) is nested inside the repo, applies ran with
  `cwd=<workspace>` changed nothing while reporting success. The tool now
  runs `git apply` at the toplevel with `--directory=<workspace-prefix>`
  and raises if an in-repo apply changes nothing. Regression test:
  `test_apply_patch_changes_content_in_nested_repo_workspace`.

## Important design decision

The LLM is not given unrestricted browser control. The LLM produces structured test steps and Playwright executes a constrained toolset. This makes the system easier to observe, test, secure, and explain to an enterprise client.

## Next iterations

1. Replace in-memory run state with PostgreSQL.
2. Add Redis for ephemeral execution state and event streaming.
3. Add authentication and organization/project RBAC.
4. Add MCP tool adapters.
5. Add RAG over client documentation and acceptance criteria.
6. Add human approval gates for destructive actions.
7. Add CI/CD and GitHub integration.
8. Add multi-browser/device testing.
9. Add WebSocket/SSE streaming instead of polling.
