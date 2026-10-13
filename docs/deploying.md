# Deploying

The service is one Docker image plus a Postgres database. Any host that runs containers works. Render is the quickest.

## Render (recommended for launch)

1. Push this repo to GitHub.
2. In Render: **New**, then **Blueprint**, then pick the repo. Render reads `render.yaml` and creates:
   - a Postgres database (`0.1c-256mb`)
   - the web service (`0.5c-512mb`, Docker), with `DATABASE_URL` wired to the database
3. When it asks for the `sync: false` values (SMTP, Sentry), leave them empty for now if you don't have them.
4. After the first deploy, set `BASE_URL` to the service URL Render gives you (or your domain) and redeploy. It's used in alert links and turns on secure cookies.
5. Open `/healthz`. You should see `{"ok": true, ...}`.

Check Render's pricing page for the current cost of those two plans; you can change plan names in `render.yaml`.

### Custom domain

Render: the service's **Settings**, then **Custom Domains**. Add the domain, create the DNS record it shows, then set `BASE_URL=https://yourdomain` and redeploy.

## Any other host (Fly.io, Railway, a VPS)

```bash
docker build -t agent-supervisor .
docker run -p 8000:8000 \
  -e DATABASE_URL=postgresql://user:pass@host:5432/db \
  -e BASE_URL=https://your-domain \
  agent-supervisor
```

The container runs database migrations on start, then serves on `$PORT` (default 8000) with `$WEB_CONCURRENCY` processes (default 2). Put it behind HTTPS; the server trusts `X-Forwarded-*` headers from the proxy.

## Email alerts

Set `SMTP_HOST`, `SMTP_PORT` (587), `SMTP_USER`, `SMTP_PASSWORD` and `SMTP_FROM`. Any SMTP provider works (Resend, Postmark, Amazon SES, a Gmail app password for testing).

## Error tracking

Create a free Sentry project and set `SENTRY_DSN`. The image already includes the Sentry SDK.

## Backups

Use your database host's automatic backups (Render Postgres includes them on paid plans). Test a restore once before launch.

## Upgrades

Deploy the new image. Migrations run automatically on start and refuse to run old code against a newer schema.

## Invite-only

Set `ALLOW_SIGNUP=false` and create accounts with:

```bash
agent-supervisor create-user someone@example.com --workspace "Their team"
```

## Scaling notes

- Rate limits and alert queues are per process. Fine for one instance; behind several instances, add limits at your proxy.
- The SQLite default is for local use. Use Postgres anywhere real.
