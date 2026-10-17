# Phase 6 local scanner runbook

The scanner stays idle until you run it. Its default mode is dry-run. It never
joins or leaves channels and does not inspect private conversations. AI remains
disabled. The test suite uses mocks and does not need Telegram credentials.

## 1. Apply the local D1 migration

Use Node.js 22.12 or newer. In PowerShell:

```powershell
cd "C:\Users\koppu\Desktop\Job Finder\fresher-job-radar\worker"
npm install
npm run db:migrate:local
```

This applies migrations to Wrangler's local D1 only. It does not access remote
Cloudflare resources. Migration 0003 adds classifier evidence, normalized
message hashes, and review/notification counters to scan runs.

## 2. Configure the local Worker API

Create a random local-only value and put that same value in `worker/.dev.vars`
and the root `.env`. Never use or paste a production secret in these files.
Each file should contain a line in this form:

```dotenv
SCANNER_API_SECRET=your-local-random-secret
```

In the root `.env`, also set:

```dotenv
SCANNER_API_BASE_URL=http://127.0.0.1:8787
```

Keep `.env` and `.dev.vars` private; both are gitignored. Start the Worker in
PowerShell window 1:

```powershell
cd "C:\Users\koppu\Desktop\Job Finder\fresher-job-radar\worker"
npm run dev
```

From PowerShell window 2, at the repository root, verify the authenticated
local API without printing the secret:

```powershell
cd "C:\Users\koppu\Desktop\Job Finder\fresher-job-radar"
py -3.11 -c "from scanner.local_env import load_local_env; load_local_env(); import os; from scanner.api_client import WorkerAPIClient; print('reachable; active channels:', len(WorkerAPIClient(os.environ['SCANNER_API_BASE_URL']).list_channels()))"
```

## 3. Check Telegram access and configure a local channel

The ignored root `.env` needs `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, and
`TELEGRAM_SESSION` from your local session setup. Do not paste them into chat.
List channels that the account can access:

```powershell
py -3.11 -m scanner.telegram_cli list
```

Channel management commands are a later phase. For local testing only, create
an active channel row in Wrangler's local D1, using a channel ID shown by that
list command. Replace the sample username and integer ID with your own joined
channel values:

```powershell
cd worker
npx wrangler d1 execute fresher-job-radar-db --local --command "INSERT INTO channels(telegram_channel_id,username,title,original_reference,status,access_status) VALUES(-1001234567890,'example','Example Jobs','@example','ACTIVE','VERIFIED') ON CONFLICT(original_reference) DO UPDATE SET telegram_channel_id=excluded.telegram_channel_id,status='ACTIVE',access_status='VERIFIED';"
```

Use only a channel your Telegram account already joined and can read. This
command changes local D1 only.

## 4. Run a read-only dry-run

From the repository root, preview up to 100 new posts per channel. New channels
without a checkpoint inspect the latest 20 posts, oldest-first:

```powershell
cd ..
py -3.11 -m scanner.main --dry-run --verbose
```

Restrict the preview or adjust bounded batch size:

```powershell
py -3.11 -m scanner.main --dry-run --channel @example --max-messages 50 --verbose
py -3.11 -m scanner.main --dry-run --timeout-seconds 600
```

The output contains IDs, classifications, scores, and aggregate counts, but not
message bodies or credentials. Dry-run reads channel and checkpoint data only;
it does not acquire a lease, create scan runs, save deduplication state, change
checkpoints, alter notification states, or call the bot API.

Example sanitized output (the counts depend on your channels):

```json
{"channel_id":1,"classification":"MATCHED","duplicate":false,"event":"message_classified","message_id":31,"score":93}
{"channel_errors":0,"channels_checked":1,"duration_seconds":1.42,"duplicates":0,"errors":0,"initial_scans":1,"matched":1,"messages_processed":3,"messages_retrieved":3,"mode":"dry-run","notifications_pending_retry":0,"notifications_sent":0,"rejected":1,"review":1}
```

## 5. Optional live local test

Live scanning persists messages/jobs/outbox rows and sends real bot alerts. It
requires the bot to have been started by its owner, and `BOT_TOKEN` plus
`OWNER_TELEGRAM_ID` in ignored `.env`. First ensure dry-run output is expected.
Then run only if an actual alert is acceptable:

```powershell
py -3.11 -m scanner.main --live --channel @example --max-messages 10
```

Run the same command again. It should fetch only posts above the durable
checkpoint; a confirmed sent outbox item is not sent a second time. Inspect
local state with:

```powershell
cd worker
npm run db:console:local
npx wrangler d1 execute fresher-job-radar-db --local --command "SELECT c.username,cp.last_message_id FROM channels c LEFT JOIN channel_checkpoints cp ON cp.channel_id=c.id;"
npx wrangler d1 execute fresher-job-radar-db --local --command "SELECT delivery_status,attempt_count,sent_at FROM notifications ORDER BY rowid DESC LIMIT 20;"
```

## Recovery and limits

- Posts are read oldest-first. A message must have a durable processing row;
  parsed jobs and matched outbox intents are saved before it is marked processed.
  The checkpoint advances only after the channel's processed prefix is durable.
- A failed persistence call stops that run before checkpoint movement. The next
  run re-fetches the uncheckpointed range and D1 uniqueness constraints make
  retries idempotent.
- A temporary channel read error is isolated so later channels can run. Invalid
  Telegram authentication stops the run. Inaccessible configured channels are
  marked inaccessible during live mode.
- A global D1 lease prevents overlapping live scanner runs. Dry-runs take no
  lease and remain read-only.
- Notification claims expire and can be recovered. Telegram delivery cannot be
  exactly-once if Telegram accepted a message but the client lost its response.
- Live sends are only to `OWNER_TELEGRAM_ID`; a pending outbox item for another
  recipient is skipped.
- No workflow schedule, bot management command, AI classifier, remote migration,
  or deployment is part of Phase 6.

## Tests

From the project root:

```powershell
py -3.11 -m pytest
```

From `worker/`:

```powershell
npm run typecheck
npm test
```

These tests use local mocks/Workers test bindings and do not contact production
Telegram or Cloudflare APIs.
