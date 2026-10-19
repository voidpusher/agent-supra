# Production database backups

The `production database backup` GitHub Actions workflow exports the Neon
`agent_supra` database daily at 02:17 UTC (07:47 IST). It creates a custom-format
`pg_dump`, encrypts the dump with AES-256-GCM, decrypts it on the disposable
runner, restores it into a temporary PostgreSQL 16 database, checks the core
tables, and uploads only the encrypted archive. Each artifact is retained for
30 days, subject to the repository's retention limit. The workflow can also be
started from **Actions → production database backup → Run workflow**.

**Current status (19 October 2026): first backup verified.** Manual
[run #2](https://github.com/voidpusher/agent-supra/actions/runs/39300257444)
passed export, AES-256-GCM encryption, decryption, isolated PostgreSQL 16
restore, and table checks. GitHub retained encrypted artifact
`agent-supra-db-39300257444-1`. The dedicated `agent_supra_backup_v2` login
authenticated and had SELECT on all 13 public tables and sequences, no INSERT
grants, and no administrative role attributes. The first scheduled run is
still pending; check it at the next daily execution.

## Required repository secrets

- `BACKUP_DATABASE_URL`: a direct Neon connection to the `agent_supra` database
  on the `production` branch. Use a dedicated read-only login with `CONNECT`,
  schema `USAGE`, and `SELECT` on all application tables and sequences. It
  needs access to tables created by later migrations as well.
- `BACKUP_ENCRYPTION_KEY`: base64 encoding of 32 random bytes. Keep a separate,
  private recovery copy; GitHub will not show a secret's value after saving it.
  Losing this key makes the archived backups unreadable.

Both secrets must be added under the `voidpusher/agent-supra` repository's
**Settings → Secrets and variables → Actions**. Never put either value in Git,
issue comments, workflow logs, or a deployment URL. The workflow requests only
read access to repository contents. Its database login should have no write
permission.

## Verify and recover

Check every scheduled run's result and its artifact on GitHub Actions. A green
run verifies the encrypted archive was decrypted and restored to a separate
PostgreSQL database. If a run fails or the schedule stops, investigate before
the latest 30-day artifact expires. GitHub schedules can be delayed and may be
disabled after repository inactivity, so they are not a precise recovery-time
guarantee.

To recover, download an encrypted artifact from a successful workflow run.
On a trusted computer with `BACKUP_ENCRYPTION_KEY` in the environment, run:

```bash
python scripts/backup_archive.py decrypt backup.dump.enc recovered.dump
pg_restore --list recovered.dump
pg_restore --exit-on-error --no-owner --no-privileges \
  --dbname="$ISOLATED_RESTORE_URL" recovered.dump
```

`ISOLATED_RESTORE_URL` must point to a newly created, empty database, never to
the live `production` database. Compare a known run and table counts before
moving any application connection. Delete plaintext dump files promptly after
verification. The Neon manual snapshot and six-hour point-in-time history
remain separate recovery options; neither replaces the recurring export.
