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

### Backend

```bash
cd backend
cp .env.example .env
# add OPENAI_API_KEY
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
uvicorn app.main:app --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

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
