# Demo: white-box fix loop fixture

A tiny payment module with the exact bug class from the brief: a missing
null-handling path when the gateway response has no `transaction_id`.

## Layout

```text
demo/whitebox-todo/
  payments/service.py      # charge() — raises KeyError today
  tests/test_service.py    # 1 passing + 1 failing test (the eval)
```

## The eval (run before and after the agent's patch)

```bash
cd demo/whitebox-todo
python -m pytest -q
# before fix: 1 passed, 1 failed (KeyError: 'transaction_id')
# after fix:  2 passed
```

## How the agent uses it

```text
debugger evidence (KeyError, transaction_id, payment)
  → codebase search → payments/service.py
  → fixer proposes unified diff → fix.patch artifact
  → HUMAN APPROVAL (API) → branch + apply + pytest
  → verified report
```

Nothing here is importable by the backend (no shared code); the agent
touches it only through the sandboxed codebase tools.
