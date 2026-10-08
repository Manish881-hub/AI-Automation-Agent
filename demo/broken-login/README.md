# Demo: deliberately broken login site

A tiny static site with a login form whose auth endpoint does not exist.
Every sign-in fails with a console error + failed HTTP request — exactly
the evidence the agent is built to capture.

## Serve it

```bash
cd demo/broken-login
python3 -m http.server 8901
# open http://localhost:8901
```

## Run the agent against it

```bash
curl -X POST http://localhost:8001/api/runs \
  -H "Content-Type: application/json" \
  -d '{
    "url": "http://localhost:8901",
    "objective": "verify that a user can sign in and reach the dashboard"
  }'
```

## What to expect

```text
recon: website understood        <- finds Sign in, Email, Password, links
planner: 6 steps generated       <- grounded in the snapshot, not invented
browser: fill Email / fill Password / click Sign in
validator: assert fails          <- no dashboard; error banner present
debugger: probable cause         <- /api/login 404 + console error captured
reporter: report generated       <- WHAT IT SAW / DID / WHY IT FAILED
```

Show the report plus `artifacts/<run_id>/` (snapshot.json, recon.png,
step_*.png, session.json) in the demo.
