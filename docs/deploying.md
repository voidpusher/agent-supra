# Deploying

The current hosted beta runs on Vercel with Neon Postgres at
`https://agent-supra.vercel.app`. The repository also supports a Docker host.

## Vercel and Neon (current production setup)

1. Connect this GitHub repository to a Vercel project. `vercel.json` selects the
   FastAPI entrypoint, runs it in Singapore near the Neon database, and schedules
   a daily retention cron.
2. Create a Neon Postgres database in Singapore and set its pooled connection URL
   as the Vercel production `DATABASE_URL` secret. Do not commit it or put it in a
   deployment URL.
3. Set `BASE_URL=https://agent-supra.vercel.app` (or the verified custom domain),
   `CONTACT_EMAIL`, and a random `CRON_SECRET` in Vercel's production environment.
   The cron is rejected with 401 unless the request carries Vercel's secret.
4. Deploy from `main`. Check `/healthz` returns `{"ok":true,...}`, then verify an
   authenticated dashboard session and an API-key run end to end. Check the
   production function logs for errors.
5. Configure Neon backups and test a restore before accepting real customer data.
   Set `SMTP_*` and the operator workspace's Slack webhook for alerts. Use the
   Settings test alert and confirm it arrives. Unexpected 500 errors appear in
   the operator-only `/app/errors` inbox and alert on first occurrence.

The SDK is currently installed from the public GitHub repository; publish it to
PyPI and change the site's install command only after a fresh install succeeds.

## Docker host (alternative)

The service is one Docker image plus a Postgres database. Any host that runs
containers works.

## Render

1. Push this repo to GitHub.
2. In Render: **New**, then **Blueprint**, then pick the repo. Render reads `render.yaml` and creates:
   - a Postgres database (`0.1c-256mb`)
   - the web service (`0.5c-512mb`, Docker), with `DATABASE_URL` wired to the database
3. When it asks for the `sync: false` SMTP values, leave them empty for now if you don't have them.
4. After the first deploy, set `BASE_URL` to the service URL Render gives you (or your domain) and redeploy. It's used in alert links and turns on secure cookies.
5. Open `/healthz`. You should see `{"ok": true, ...}`.

Check Render's pricing page for the current cost of those two plans; you can change plan names in `render.yaml`.

### Custom domain

Render: the service's **Settings**, then **Custom Domains**. Add the domain, create the DNS record it shows, then set `BASE_URL=https://yourdomain` and redeploy.

## Any other host (Fly.io, Railway, a VPS)

```bash
docker build -t agent-supra .
docker run -p 8000:8000 \
  -e DATABASE_URL=postgresql://user:pass@host:5432/db \
  -e BASE_URL=https://your-domain \
  agent-supra
```

The container runs database migrations on start, then serves on `$PORT` (default 8000) with `$WEB_CONCURRENCY` processes (default 2). Put it behind HTTPS; the server trusts `X-Forwarded-*` headers from the proxy.

## Email alerts

Set `SMTP_HOST`, `SMTP_PORT` (587), `SMTP_USER`, `SMTP_PASSWORD` and `SMTP_FROM`. Any SMTP provider works (Resend, Postmark, Amazon SES, a Gmail app password for testing).

## Error tracking

Unexpected server errors are grouped by type, route pattern and stack location
in `/app/errors`, accessible only to the account whose email matches
`CONTACT_EMAIL`. The first occurrence is sent to that account's workspace Slack
webhook and alert email, if configured; otherwise it goes to `CONTACT_EMAIL`
when SMTP is configured. Error data uses `RETENTION_DAYS`. The monitor does not
store exception messages, request bodies, headers or local variables. Also
watch Vercel function logs and an independent uptime check: an application
inbox cannot record failures when its database or process is unavailable.

The `CONTACT_EMAIL` address is reserved from public signup so another user
cannot claim operator access. Create that account with `agent-supra create-user`
before opening signup on a fresh installation. The existing hosted operator
account was created before this restriction.

## Backups

Use your database host's automatic backups (Render Postgres includes them on paid plans). Test a restore once before launch.

## Upgrades

Deploy the new image. Migrations run automatically on start and refuse to run old code against a newer schema.

## Invite-only

Set `ALLOW_SIGNUP=false` and create accounts with:

```bash
agent-supra create-user someone@example.com --workspace "Their team"
```

## Scaling notes

- Rate limits and alert queues are per process. Fine for one instance; behind several instances, add limits at your proxy.
- The SQLite default is for local use. Use Postgres anywhere real.
