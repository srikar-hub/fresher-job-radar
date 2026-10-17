# Worker and D1 (Phase 5)

## Local setup on Windows PowerShell

Install Node.js 22.12 or newer and npm, then:

```powershell
cd worker
npm install
Copy-Item .dev.vars.example .dev.vars
notepad .dev.vars
npm run db:migrate:local
npm run typecheck
npm test
npm run dev
```

Set random local-only values in `.dev.vars`: `SCANNER_API_SECRET`, a private
test-bot `BOT_TOKEN`, your numeric `OWNER_TELEGRAM_ID`, and a random
`TELEGRAM_WEBHOOK_SECRET` containing letters, numbers, `_` or `-`. The local
Worker listens at `http://127.0.0.1:8787`; local D1 files live in ignored
`.wrangler/`. Use `npm run db:console:local` to inspect tables. Local state is
development-only and is not the durable database for a hosted scanner.

Add `SCANNER_API_BASE_URL=http://127.0.0.1:8787` and the matching local secret
to the ignored root `.env`. With Worker running, open a second PowerShell window:

```powershell
cd ..
py -3.11 -c "from scanner.local_env import load_local_env; load_local_env(); import os; from scanner.api_client import WorkerAPIClient; print(WorkerAPIClient(os.environ['SCANNER_API_BASE_URL']).list_channels())"
```

## Schema

Migrations `0001_initial.sql` through `0005_optional_ai.sql` create/update:

- `channels`, supporting pending verification and lifecycle/access statuses.
- `channel_checkpoints`, one monotonic checkpoint per channel.
- `messages`, unique on channel and Telegram message ID with durable processing state.
- `jobs` and `job_sources`, allowing many source posts per idempotent job.
- `notifications`, a durable outbox with attempts, retry time, and reclaimable send leases.
- `scan_runs`, `settings`, and `scan_leases`. Migration 0003 adds saved
  classifier explanations/evidence, normalized message hashes, and scan counters.
- `telegram_updates` deduplicates webhook update IDs. Migration 0004 adds
  sanitized channel-verification failure codes and seeds global monitoring
  settings without replacing existing user preferences.
- `ai_assessments` stores validated AI outcome metadata and a content digest for
  successful-result caching; provider failures are retained for a one-hour
  cooldown. It does not store the submitted post text. `ai_daily_usage` tracks
  inference calls by UTC date. Migration 0005 preserves all existing rows.

Foreign keys and indexes support joins, de-duplication, and outbox scans. Raw
Telegram message bodies are not stored; AI assessment rows retain short source
evidence snippets when that optional feature is enabled.
The normalized-URL lookup index is non-unique and considers company and role as
well, reducing accidental merges when companies reuse a generic application
page. Stable `(company, requisition_id)` pairs remain uniquely constrained.

## API

All API routes require `Authorization: Bearer <SCANNER_API_SECRET>`. The Worker
does not log authorization values and returns generic 503 errors for database
exceptions.
Successful API calls and generic database failures are logged as structured
JSON containing only method, route, and status; credentials and request bodies
are omitted.

| Method and path | Operation |
| --- | --- |
| `GET /health` | Unauthenticated health probe; no database data |
| `GET /api/v1/channels?status=ACTIVE&limit=100&before_id=...` | List channels (max 200; paginate with the last returned ID) |
| `POST /api/v1/channels` | Idempotently create a pending channel using `original_reference` |
| `GET/PATCH /api/v1/channels/{id}` | Read/update status, verified Telegram ID, title, username |
| `GET/POST /api/v1/channels/{id}/checkpoint` | Read/advance a channel checkpoint |
| `POST /api/v1/messages` and `GET /api/v1/messages/{channel_id}/{message_id}` | Persist/check message processing state |
| `POST /api/v1/jobs`, `GET /api/v1/jobs/{id}` | Save job and associate optional source-message fields |
| `POST /api/v1/notifications`, `GET /api/v1/notifications/pending` | Create/list outbox records |
| `POST /api/v1/notifications/{id}/claim`, `/sent`, `/failed` | Claim, complete, or retry a notification |
| `POST/GET /api/v1/scan-runs` | Create/update metrics and list recent runs |
| `GET/POST /api/v1/settings` | Retrieve/save settings |
| `GET /api/v1/settings/monitoring` | Read global pause state for the scanner |
| `POST /api/v1/ai/classify` | Optional internal inference for one bounded ambiguous post; disabled unless `AI_ENABLED=true` |
| `GET /api/v1/ai/summary` and `/api/v1/ai/assessments?limit=20` | Inspect usage and validated outcome metadata (no secrets or source post) |
| `POST /api/v1/leases/acquire`, `/release` | Acquire/release a named expiring scan lease |

Checkpoint writes require the concrete `processed_message_ids` fetched in that
batch. Each ID must exist in D1 and be `PROCESSED`, and no
saved unfinished message at or below the new checkpoint can be skipped. Natural
Telegram ID gaps are allowed. Callers should process posts in ascending message
ID order and persist every fetched post before advancing. Crashes before advance
cause safe re-fetch; uniqueness constraints make persistence idempotent. A
`SENDING` notification may be reclaimed after its lease expiry. A Telegram send
that succeeds but loses its acknowledgement remains inherently ambiguous.

## Optional AI classification (Phase 8)

The Worker binding is present for local development, but its checked-in
`AI_ENABLED` variable is `false`. The authenticated internal
`POST /api/v1/ai/classify` route refuses to call the binding unless the setting
is exactly `true`. It accepts only bounded job fields, recomputes a SHA-256 key,
returns cached successful results, applies a configurable daily cap (default
10, maximum 20) and input limit (default and hard maximum 6,000 characters),
and stores validated output or a one-hour failed-attempt
cooldown. `GET /api/v1/ai/summary` and `GET /api/v1/ai/assessments?limit=20`
return usage/outcome metadata but not secrets or source text.

The model is pinned to `@cf/meta/llama-3.1-8b-instruct`; arbitrary model names
are refused. Cloudflare Workers AI always uses remote inference, including in
local development, so enabling `AI_ENABLED=true` can consume the account's AI
allocation even when the scanner runs with `--dry-run`. Keep it disabled unless
you explicitly accept that use. The automated tests set `remoteBindings:false`
and replace the AI binding with mocks; they do not authenticate to Cloudflare
or submit real inference requests.

## Telegram bot management (Phase 7)

The webhook endpoint is `POST /telegram/webhook`. Production requires HTTPS.
Telegram's `X-Telegram-Bot-Api-Secret-Token` is checked separately from owner
authorization. Only text commands from the configured owner in the owner's
private chat are processed. Group updates and other users are acknowledged
without receiving private data. Configure `BOT_TOKEN`, `OWNER_TELEGRAM_ID`, and
`TELEGRAM_WEBHOOK_SECRET` as Worker secrets/variables when deployed; use only
ignored `.dev.vars` locally. Keep `SCANNER_API_SECRET` private too.

Commands:

```text
/start
/help
/addchannel @channelusername
/removechannel @channelusername
/pausechannel @channelusername
/resumechannel @channelusername
/channels
/latest
/today
/stats
/settings
/pause
/resume
```

`/addchannel` also accepts a public `https://t.me/username` link. Private invite
links are rejected. The Worker records additions as `PENDING_VERIFICATION`; the
next scanner run must resolve the channel with the owner's authenticated
Telethon account before it becomes `ACTIVE`. An inaccessible request stays
pending with a sanitized error class and is retried on later runs. The scanner
does not join or leave channels. Pause and remove take effect at the next
scanner channel boundary and preserve checkpoints/history. Resume only restores
a previously verified paused channel.

`/pause` persists global pause state in D1. New scanner starts stop before work;
the lease API refuses acquisition while paused, and an in-progress scanner
stops at its next channel boundary. A channel already being read can finish
that safe unit. Manual dry-run scans respect the pause. `/resume` allows future
runs again. `/today` groups original message timestamps by India Standard Time
(UTC+05:30), not discovery time. Bot replies are plain text, with no Markdown or
HTML parsing, and are capped below Telegram's message limit.

Webhook update IDs have a unique D1 key. A duplicate completed or currently
processing update is acknowledged without repeating its command. Failed
processing releases its claim so Telegram can retry; a crashed claim may be
reclaimed after two minutes. Management writes are idempotent. If a Bot API
send fails after a database write, the command may be retried; if Telegram
accepted a reply but its acknowledgement was lost, a read-only response can be
sent twice. This is an at-least-once network boundary.

### Local webhook testing

Run `npm run db:migrate:local`, then start the local Worker with `npm run dev`.
To invoke the HTTP endpoint locally, use a private test bot and owner ID; a
successful command sends a real reply using that test bot, so do not use a
production token. In a second PowerShell window from `worker/`:

```powershell
$env:LOCAL_WEBHOOK_SECRET = (Get-Content .dev.vars | Where-Object { $_ -like 'TELEGRAM_WEBHOOK_SECRET=*' } | ForEach-Object { $_.Substring('TELEGRAM_WEBHOOK_SECRET='.Length) })
$body = '{"update_id":900001,"message":{"text":"/help","from":{"id":123456789},"chat":{"id":123456789,"type":"private"}}}'
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8787/telegram/webhook -Headers @{ 'X-Telegram-Bot-Api-Secret-Token' = $env:LOCAL_WEBHOOK_SECRET } -ContentType 'application/json' -Body $body
Remove-Item Env:LOCAL_WEBHOOK_SECRET
```

Replace the sample ID in the JSON with `OWNER_TELEGRAM_ID`. Use a new
`update_id` for each unique update. Automated tests mock the Bot API and require
no credentials or network. Run `npm run typecheck; npm test` in `worker/` and
`py -3.11 -m pytest -q` from the project root.

For production, configure Worker secrets for `SCANNER_API_SECRET`, `BOT_TOKEN`,
and `TELEGRAM_WEBHOOK_SECRET`, plus the owner ID variable. Then use Telegram's
`setWebhook` Bot API with the HTTPS Worker URL and the same secret token. Do so
only when ready for production; this change does not deploy or configure it.
Never put the bot token in shell history, committed files, or chat. When ready,
PowerShell can prompt without putting the values in the command itself (replace
the Worker URL):

```powershell
$botSecure = Read-Host 'Bot token' -AsSecureString
$secretSecure = Read-Host 'Webhook secret' -AsSecureString
$botToken = [Net.NetworkCredential]::new('', $botSecure).Password
$webhookSecret = [Net.NetworkCredential]::new('', $secretSecure).Password
$payload = @{ url = 'https://YOUR-WORKER.example.workers.dev/telegram/webhook'; secret_token = $webhookSecret } | ConvertTo-Json
$null = Invoke-RestMethod -Method Post -Uri "https://api.telegram.org/bot$botToken/setWebhook" -ContentType 'application/json' -Body $payload
$botToken = $null; $webhookSecret = $null; $payload = $null; $botSecure = $null; $secretSecure = $null
```

The webhook URL must be publicly reachable over HTTPS. Save the same secret as
the Worker `TELEGRAM_WEBHOOK_SECRET`; never place it in the URL.

Troubleshooting: HTTP 401 means the webhook secret header is missing or wrong.
Only the configured numeric owner in their private chat can run commands. A
channel the Telethon account cannot resolve remains pending and is not scanned;
confirm the account has access and allow another verification attempt.

## Cloudflare manual setup (optional; no cloud operations performed)

Create an account at [dash.cloudflare.com/sign-up](https://dash.cloudflare.com/sign-up) and select Cloudflare Free only. Check account signup does not request a card in your
region; do not enable billing or automatic upgrades. From `worker/`:

```powershell
npx wrangler login
npx wrangler d1 create fresher-job-radar-db
```

Copy the generated database ID into `wrangler.jsonc` replacing
`local-development-placeholder`. Review the SQL before applying anything. Remote
migrations and deployment are deliberately manual:

```powershell
npx wrangler d1 migrations apply fresher-job-radar-db --remote
npx wrangler secret put SCANNER_API_SECRET
npx wrangler secret put BOT_TOKEN
npx wrangler secret put TELEGRAM_WEBHOOK_SECRET
npx wrangler secret put OWNER_TELEGRAM_ID
npx wrangler deploy
```

These commands have not been run. Use HTTPS for production, configure the Python
scanner's base URL and API secret through your secret store, and never put a
production token into a committed file.

Current Cloudflare documentation (limits may change): [Workers Free pricing and
limits](https://developers.cloudflare.com/workers/platform/pricing/), [D1
pricing](https://developers.cloudflare.com/d1/platform/pricing/), [D1
limits](https://developers.cloudflare.com/d1/platform/limits/), [Wrangler
installation](https://developers.cloudflare.com/workers/wrangler/install-and-update/).
Cloudflare currently lists Workers Free at 100,000 requests/day and D1 Free at
5 million rows read/day, 100,000 rows written/day, and 5 GB total storage. D1
operations fail when daily free limits are exhausted. Check dashboard usage and
do not upgrade automatically.
