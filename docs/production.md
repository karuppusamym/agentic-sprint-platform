# Running it for the team (production)

On one machine the defaults are fine: dev sign-in, SQLite, demo controls. As soon as anyone else
can reach it, run it with `ASDP_DEV_MODE=false`. In that mode the server **refuses to start**
until the settings below are safe, and prints what is missing.

## What must be set

| Setting | Why |
|---|---|
| `ASDP_DEV_MODE=false` | turns off demo controls and unsigned webhooks |
| `ASDP_SECRET` | 32+ random characters; signs prototype preview links |
| `ASDP_AUTH_MODE=proxy` | identity comes from your SSO proxy, not from a name the browser sends |
| `ASDP_PROXY_SECRET` | 16+ random characters; the proxy adds it to every request as `X-Proxy-Secret`, so a client that reaches the API directly cannot pose as the proxy |
| `ASDP_PUBLIC_BASE_URL`, `ASDP_PORTAL_ORIGIN` | the address people open, for example `https://asdp.company.com` (both the same when the API serves the portal) |
| `ASDP_DATABASE_URL` | PostgreSQL for a team: `postgresql+psycopg://user:pass@host/asdp`. SQLite is for one machine. |

Generate a secret with `python -c "import secrets; print(secrets.token_urlsafe(48))"`.

Then `python -m asdp.cli doctor` (from `services/api`) checks Jira, Devin and the rest.

## The SSO proxy

Any reverse proxy that authenticates people works (oauth2-proxy, an ingress with OIDC, IIS/ARR,
Apache `mod_auth_openidc`). It must:

1. Remove any `X-Forwarded-User`, `X-Forwarded-Groups` and `X-Proxy-Secret` the client sent.
2. Set `X-Forwarded-User` to the signed-in person and `X-Forwarded-Groups` to a comma-separated
   list containing one or more of the platform roles: `product_owner`, `developer`, `qa`,
   `eng_lead`, `devops`, `security`, `admin`. Other group names are ignored.
3. Set `X-Proxy-Secret` to the value of `ASDP_PROXY_SECRET`.
4. Let `/api/v1/webhooks/github` and `/api/v1/webhooks/jira` through without sign-in (they are
   verified by their own secrets), and let CI through with its `Authorization: Bearer` token.

The API should listen only where the proxy can reach it.

## Container

```
docker build -t asdp .
docker run -d --name asdp -p 8000:8000 -v asdp-data:/data --env-file .env asdp
```

The image serves the portal and the API on port 8000. `/healthz` is liveness, `/readyz` checks the
database. Run **one** instance: the scheduler and Jira reconciliation run inside the process, and a
second instance would do the same work twice.

Without Docker: `npm run build` in `apps/portal`, set `ASDP_SERVE_PORTAL=true`, and start the API as
in the README. The built portal calls the API on its own origin; set `VITE_API_BASE` at build time
only if the API is on a different address.

## What the server does about failures

- A restart in the middle of generating a spec or prototype leaves the story without its job. The
  next reconciliation (every 15 seconds, once its 10-minute lease has expired) marks it `FAILED`
  with the reason, and **Retry after failure** on the story page sends it back to analysis.
- Agent runs with no result after `agents.per_run_timeout_minutes` are failed and retried within
  the retry budget.
- Jira write-back is idempotent; a retry never posts a second comment.
- Requests larger than `ASDP_MAX_BODY_BYTES` (8 MB) are refused.

## Known limits

- The schema is created on first start; there are no migration scripts yet. Back up the database
  before upgrading, and expect to add a migration tool (Alembic) before the first schema change.
- No rate limiting; put that in the proxy.
- The audit chain detects tampering; it does not prevent a database administrator from deleting
  the whole table. Ship the audit events to your log store if you need that.
- The Jira, Devin and GitHub adapters are tested with stand-ins here. Run `doctor` and one story
  end to end before relying on them.
