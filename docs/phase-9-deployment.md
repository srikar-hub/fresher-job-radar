# Phase 9: secure scheduling and deployment preparation

## What is implemented

`.github/workflows/scan.yml` runs the existing finite scanner at
`7,22,37,52 * * * *` UTC and supports manual `workflow_dispatch`. Scheduled
runs use `python -m scanner.main --live --timeout-seconds 900`; manual runs
default to `--dry-run` and can be changed to live explicitly. A live run may
write durable D1 state and send real job alerts to the configured owner. The
workflow runs on the latest commit of the default branch; scheduled runs are
best-effort and may be delayed or dropped by GitHub, so alerts are not
guaranteed within 15 minutes.

The job has a 20-minute timeout, read-only `contents` permission, pinned
official checkout and setup-python action commits, no log artifacts, and a
shared concurrency group with `cancel-in-progress: false`. The existing D1
`scanner-global` lease is still authoritative across runners; it expires after
one hour and is released in the scanner's `finally` path. Workflow concurrency
allows the active run to finish and retains at most one pending run. If a run
fails or is cancelled, its safe message checkpoint prefix remains durable and
the next scan re-reads uncheckpointed messages. A notification with an
ambiguous Telegram response remains at-least-once; the system does not promise
exactly-once delivery.

No scan ran while preparing this workflow. Adding this file to the repository's
default branch enables the schedule once the workflow is active and configured.
Do not merge/enable it until the secrets are set and you are ready for scheduled
live scans and possible alerts.

## GitHub repository configuration

Create these repository **Actions secrets** (values by name only; never put
their values in source control, workflow logs, or chat):

| Secret | Used for |
| --- | --- |
| `TELEGRAM_API_ID` | Telethon account connection |
| `TELEGRAM_API_HASH` | Telethon account connection |
| `TELEGRAM_SESSION` | Reusable Telethon StringSession; treat as full Telegram account access |
| `SCANNER_API_SECRET` | Bearer authentication to the deployed Worker |
| `BOT_TOKEN` | Required only for live scans that can send owner alerts |

Create these repository **Actions variables**:

| Variable | Value |
| --- | --- |
| `SCANNER_API_BASE_URL` | HTTPS URL of the deployed Worker, with no `/api/v1` suffix |
| `OWNER_TELEGRAM_ID` | Positive numeric Telegram user ID; not an authentication credential |

The preflight checks only names/shape and prints missing names, never values.
Manual dry-run needs the Telethon and Worker values but not the bot token or
owner ID. Scheduled live runs need all five secrets and both variables. The
workflow sets `APP_ENV=production` and `AI_ENABLED=false`; no AI credential is
needed or read. The Worker must already have its own `SCANNER_API_SECRET` set
to the same value.

Set these under repository **Settings → Secrets and variables → Actions**.
Restrict who has repository write access and who can change trusted workflow
code: those people could alter a workflow to use the Telegram session. Avoid
running untrusted branches with secrets. Keep the repository private if you do
not want source/profile configuration public; a public repository makes
standard GitHub-hosted Actions minutes free, but source code and profile
preferences are visible to everyone. Do not commit `.env`, `worker/.dev.vars`,
`secrets/`, session files, tokens, or the contents of GitHub settings.

To test after configuration, use **Actions → Telegram job scanner → Run
workflow**, select the default `dry-run`, and inspect the summary only. The
manual `live` option can persist and send alerts. The schedule will only start
after the workflow is present on the default branch. To stop scheduled scans,
disable this workflow in the Actions UI; this does not delete data or secrets.

## Telethon session handling

Create/recreate the StringSession locally with `py -3.11
scripts/create_telegram_session.py`; enter the OTP and optional two-step
password only in that local terminal. Read the ignored local file into the
GitHub secret entry using a trusted local clipboard/secret manager, then clear
the clipboard. Never paste a session into chat or a command line. The workflow
passes it only as `TELEGRAM_SESSION` in the runner environment; it is not
written to D1, printed, or uploaded as an artifact.

If the session is exposed, open Telegram **Settings → Devices**, terminate that
session, create a new local StringSession, and replace the GitHub secret. If
the bot token is exposed, revoke it with BotFather and update the Worker/GitHub
secret. If `SCANNER_API_SECRET` is exposed, replace it in the Worker and
GitHub Secrets; the scanner will fail authentication until both sides match.
Do not expose bot webhook secrets; rotate them separately if the webhook is
later configured.

## Local versus remote configuration

| Resource | Local development | Hosted production |
| --- | --- | --- |
| Worker | `npm run dev` at `http://127.0.0.1:8787` | Worker `fresher-job-radar-api` at its HTTPS URL |
| D1 | Wrangler local state in ignored `worker/.wrangler/` | The selected Cloudflare D1 database named `fresher-job-radar-db` |
| D1 migration target | `npm run db:migrate:local` / explicit `--local` | Explicit `--remote`, after reviewing migration status |
| Python config | ignored root `.env`; `APP_ENV=development` and loopback URL | GitHub Actions secrets/variables; `APP_ENV=production` and HTTPS URL |
| Worker config | ignored `worker/.dev.vars` plus local binding | `worker/wrangler.jsonc` binding and Worker secrets/vars |

For safety, scanner startup now rejects a remote API URL unless `APP_ENV` is
exactly `production`; in production the API client requires HTTPS. The checked
in `worker/wrangler.jsonc` still contains
`database_id: "local-development-placeholder"`. This is deliberately not a
real resource ID. Replace it only after checking your Cloudflare account and
selecting the intended database. The binding name must stay `DB`; Wrangler
binds the configured D1 database to that name. The existing optional `AI`
binding remains configured but `AI_ENABLED` is false in Wrangler and in the
workflow. No AI provider secret is used.

The Worker health endpoint is `GET /health` and does not prove D1/API
authentication readiness. Authenticated API readiness is verified by a
manual workflow `dry-run` after setting its secrets and variables; it performs
read-only channel/checkpoint reads and does not call the bot API.

## Proposed Cloudflare deployment commands (not run)

The account-specific Worker/database and database ID are unknown, so do not
create a second resource blindly. In `worker/`, first inspect/select resources:

```powershell
npx wrangler whoami
npx wrangler d1 list
```

These commands authenticate/inspect the selected account and list D1 databases;
they do not change a database or deploy. If and only if the intended database
does not exist, propose creating it:

```powershell
npx wrangler d1 create fresher-job-radar-db
```

This creates a remote D1 database. Copy its reported ID into
`worker/wrangler.jsonc`, after confirming that this is the intended database.
If a database already exists, use its ID instead; do not create a duplicate.
Then inspect its migration state before proposing any migration:

```powershell
npx wrangler d1 migrations list fresher-job-radar-db --remote
```

This reads remote migration status. Review the checked-in SQL under
`worker/migrations/`, check for existing data, and reconcile any already-applied
migrations. Only after explicit approval, apply pending migrations:

```powershell
npx wrangler d1 migrations apply fresher-job-radar-db --remote
```

This changes the selected remote D1 schema/data. It has not been run here. Do
not use `--remote` for local testing, and do not run `d1 drop`, `database
delete`, or an unreviewed SQL command.

Set the scanner API secret interactively (Wrangler prompts for the value; do
not put it in the command):

```powershell
npx wrangler secret put SCANNER_API_SECRET
```

This changes the deployed Worker's secret configuration. Set the same value in
the GitHub `SCANNER_API_SECRET` secret through the repository settings UI. The
worker webhook is independent and is not configured by Phase 9; do not set its
webhook URL or secrets as part of this scanner deployment unless separately
ready to activate Phase 7.

After confirming the D1 ID, migrations, bindings, `APP_ENV=production`, and
`AI_ENABLED=false`, this publishes the Worker:

```powershell
npx wrangler deploy
```

This is a production deployment and has not been run. Then verify the exact
Worker URL from Wrangler:

```powershell
Invoke-RestMethod -Uri 'https://YOUR-WORKER.workers.dev/health'
```

This sends a read-only health request. Once the GitHub settings are populated,
run one manual workflow `dry-run` and verify that it completes; it reads
authorized Telegram channels and Worker state but does not persist messages or
send alerts. Only then should you enable scheduled operation on the default
branch.

## Free-tier and scheduling constraints (checked 2026-10-17)

Cloudflare currently documents a Workers Free allowance of 100,000 requests
per day and D1 Free limits of 5 million rows read per day, 100,000 rows written
per day, and 5 GB total storage. If a daily D1 limit is exhausted, queries fail
until reset; the application does not upgrade the account. The local project
does not require AI and keeps AI disabled. See [Workers pricing and
limits](https://developers.cloudflare.com/workers/platform/pricing/) and [D1
pricing](https://developers.cloudflare.com/d1/platform/pricing/).

GitHub says standard hosted runners are free for public repositories. GitHub
Free currently includes 2,000 Actions minutes/month for private repositories;
each private job's partial minutes round up. The requested 15-minute schedule
is 96 runs/day, or about 2,880 runs in a 30-day month. Thus a private repository
can exceed its included minutes even if every job lasts only one billed
minute. No card is required by this code, but GitHub account billing/eligibility
is controlled by GitHub; no private-repository 15-minute no-charge guarantee
can be made. If keeping the repository private and refusing billing is
mandatory, inspect account Actions usage/limits before enabling the schedule;
the requested cadence may stop when allowance is exhausted. Do not add payment
details to keep it running. A public repo avoids standard-runner minute billing
but publishes the code/profile and still requires strict control of trusted
workflow changes. Verify your account's current allowance before opting in.

GitHub also documents that schedules only run from the default branch and can
be delayed or dropped during high load; public-repository schedules can be
disabled after 60 days without activity. Check [schedule event behavior and
limits](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows),
[Actions runner billing](https://docs.github.com/en/billing/reference/actions-runner-pricing),
and [included plan usage](https://docs.github.com/en/enterprise-cloud%40latest/billing/reference/product-usage-included)
when configuring the account. Free-tier terms, account eligibility, and
signup/card prompts may vary and change. Stop if Cloudflare requires a payment
card for the intended account; no card is required by the application itself.

## Recovery

- **Expired session / Telegram authorization failure:** the job fails without
  advancing an unsafe checkpoint. Recreate the session locally, replace the
  GitHub secret, then run manual dry-run.
- **Missing GitHub configuration:** preflight reports missing names only; add
  the missing secret/variable and retry manual dry-run. Never copy values into
  logs or an issue.
- **Worker 401:** compare the Worker `SCANNER_API_SECRET` with the GitHub
  secret without printing either value; rotate both if uncertain. Confirm the
  variable URL targets the intended HTTPS Worker.
- **Migration problem:** do not rerun SQL manually or reset D1. Inspect
  `wrangler d1 migrations list ... --remote`, retain backups/exports, and
  resolve migration history before applying anything else.
- **Scheduled failures/delays:** inspect workflow job status and sanitized
  summary; GitHub schedules are best-effort. Fix the reported configuration or
  service issue, then use a manual dry-run. Avoid artifacts and pasting raw
  logs publicly.
- **Telegram FloodWait/network error:** let the safe run fail/retry later; do
  not bypass Telegram rate limits. Scanner checkpointing/outbox retries allow
  recovery.
- **Partial notification delivery:** inspect durable notification status
  through existing authenticated APIs. Retryable records remain pending; a
  lost Telegram send acknowledgement is ambiguous and may result in a
  duplicate. Never manually mark it sent without confirming delivery.

## Local validation

From the repository root (no Telegram/Cloudflare credentials needed):

```powershell
py -3.11 -m pytest -q
```

From `worker/`:

```powershell
npm run typecheck
npm test
```

The Python tests parse and check the workflow structure and exercise the
configuration preflight with fake values. Worker tests use mocks and local test
bindings. These tests do not run a real scan, send Telegram alerts, apply
remote migrations, configure a webhook, or deploy resources.
