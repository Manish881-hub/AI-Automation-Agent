# Architecture notes

## Execution flow (v0.2)

```text
website + business objective
          ↓
   reconnaissance            compact page model (buttons/links/inputs/forms)
          ↓
   website understanding     snapshot.json + recon.png
          ↓
      planner               grounded in snapshot, semantic targets only
          ↓
   executable plan           plan.json (≤8 steps, ≤12 executed)
          ↓
     browser agent           Playwright, deterministic locator chain
          ↓
     observation             screenshot + console + network evidence
          ↓
      validator             typed assertions (text/visible/url/title/...)
          ↓
       failure?
      /       \
    no         yes
    ↓           ↓
  next       recovery              alternate target → retry → validator
  step          ↓
             debugger             console + HTTP evidence → root cause
                ↓
             reporter             WHAT IT SAW / DID / WHY / RECOMMENDS
```

## Agent responsibilities

### Reconnaissance (new in v0.2)
Opens the website first and extracts a compact interaction model —
headings, buttons, links, inputs, forms, excerpted visible text. Never
dumps the DOM into the LLM. Degraded mode: if recon fails, planning
continues without context rather than failing the run.

### Planner
Converts a business objective + website snapshot into a bounded
structured test plan. Targets must literally appear in the snapshot.
Login credentials use `{{TEST_EMAIL}}` / `{{TEST_PASSWORD}}` placeholders
resolved from server-side settings at fill time.

### Plan validator (new in v0.2.1)
Deterministic policy over LLM-proposed plans — the LLM proposes, this
agent approves. Requires: navigate-first, assert-last, non-empty semantic
targets, no destructive actions, a verifiable final condition that
references the objective (never an error state). Rejections send the
plan back for revision (max 2); unrepairable plans fail the run before
any browser action. Records which assert steps verify the objective.

### Browser
Executes only explicit browser actions through Playwright. Resolves
semantic targets via role → label → text → placeholder → CSS fallback
(fill steps prefer label/textbox-role/placeholder). The LLM never
produces raw selectors.

### Validator
Pure verdict function over observations. Typed assertions: `text`
(default), `visible`, `url`, `title`, `element`, `no_console_error`,
`http_success`.

### Recovery (new in v0.2)
One retry per failed click/fill/press step: deterministic
semantic-equivalent lookup first (exact → contains → token overlap),
LLM judgment only when nothing is close, safe abort default. Retried
results are flagged `retried: true`.

### Debugger
Correlates failures with console and HTTP evidence and proposes a probable root cause.

### Reporter
Converts the run state into an engineer-friendly report.

## White-box fix loop (new in v0.3)

Black-box testing finds and diagnoses bugs; it cannot fix source it
cannot see. When a run fails and `fix_enabled` is set, the orchestrator
adds a second phase over a workspace root:

```text
debugger evidence
  → codebase agent (deterministic search terms → files)
  → fixer agent (unified diff as text, never touches disk)
  → fix.patch + fix_proposal.md artifacts
  → HUMAN APPROVAL (POST /api/runs/{id}/fix/approve|reject)
  → branch fix/<run> → git apply → pytest → verified
```

Safety boundaries (non-negotiable):

- Tools are confined to the workspace root; traversal raises.
- Dependency, build, secret, and artifact paths are never read.
- Secret-looking values are redacted before any LLM context.
- The test runner executes pytest only — no shells, no network.
- Nothing is applied without approval; rejection leaves zero trace.
- `fix_status`: none → awaiting_approval → verified | tests_failed |
  apply_failed | rejected, all in the session audit trail.

Proven live against `demo/whitebox-todo` (KeyError on a missing
gateway `transaction_id`): locate → minimal `.get()` patch → approve →
pytest 2 passed → verified.

## Execution status vs objective status (new in v0.2.1)

Step success and business success are separate verdicts. The plan
validator names the assert steps that verify the objective; the
orchestrator sets `objective_status` from those steps alone. A fully
executed plan can therefore report `EXECUTION 6/6` alongside
`BUSINESS OBJECTIVE FAILED` — the report shows both, with observed
evidence kept distinct from the labeled root-cause hypothesis.

## Shared execution state

`RunState` is the collaboration contract — each agent owns its slice:

| Field | Writer |
|---|---|
| `website` | Reconnaissance |
| `plan` | Planner |
| `results` (+ screenshots) | Browser |
| verdicts in `results` | Validator |
| `retried` results, `failures` | Recovery |
| `analysis` | Debugger |
| `report` | Reporter |
| `status`, `events`, `memory` | Orchestrator |

Budgets: `MAX_STEPS = 12`, planner/recon/recovery timeouts plus a
run-level wall-clock budget — every run terminates.

## Session audit

Every agent event is recorded three ways via
`Orchestrator._record`: the polling timeline (`events`), structured
`memory` on the run, and the persisted `session.json`
(`artifacts/<run_id>/session.json`). Verbs: `recon_started`,
`website_understood`, `plan_generated`, `step_started`,
`step_passed` / `step_failed`, `recovery_decided` / `recovery_retried` /
`recovery_skipped` / `recovery_aborted`, `analysis_completed`,
`report_generated`. Persisted in `finally`, so failed runs keep their trail.

## State model

A production version should persist:

- project
- test run
- test step
- agent event
- artifact
- failure

The current version uses in-memory run state intentionally so the core autonomous loop can be demonstrated first.

## Safety boundaries

Enforced now: timeouts, max steps, seeded demo credentials (never
production logins), `.env` secrets never committed.

Still to enforce:

- allowed domains
- action allowlists
- maximum token/context budget
- destructive-action approval
- secret redaction
- sandboxed browser sessions
