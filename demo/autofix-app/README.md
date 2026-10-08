# Demo: closed-loop autonomous fixing (autofix-app)

One full-stack fixture where the browser bug and the source bug are the
**same bug**: valid logins crash `POST /api/login` with a 500 because
`auth.py` reads `provider_response["token"]` from a degraded (empty)
provider payload. No dashboard, console error, failed assertion.

## Run it

```bash
cd demo/autofix-app
python3 server.py 8921          # site + real auth backend, stdlib only
python -m pytest backend/tests -q   # eval: 1 passed, 1 failed (KeyError)
```

## The golden loop

```text
agent tests login → 500 → debugger → codebase search finds auth.py
→ fixer proposes .get() fallback → HUMAN APPROVAL → apply → pytest green
→ server picks up the patch live (dev reload) → browser re-test
→ dashboard opens → BUSINESS OBJECTIVE PASSED
```

The rename drill from broken-login applies here too (first load
"Sign in", later loads "Log in"), so recovery fires mid-loop.
