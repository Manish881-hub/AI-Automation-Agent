# Architecture notes

## Agent responsibilities

### Planner
Converts a business objective into a bounded structured test plan.

### Browser
Executes only explicit browser actions through Playwright.

### Validator
Determines whether an observable expected condition was satisfied.

### Debugger
Correlates failures with console and HTTP evidence and proposes a probable root cause.

### Reporter
Converts the run state into an engineer-friendly report.

## State model

A production version should persist:

- project
- test run
- test step
- agent event
- artifact
- failure

The current MVP uses in-memory state intentionally so the core autonomous loop can be demonstrated first.

## Safety boundaries

The browser agent should eventually enforce:

- allowed domains
- action allowlists
- timeouts
- max steps
- maximum token/context budget
- destructive-action approval
- secret redaction
- sandboxed browser sessions
