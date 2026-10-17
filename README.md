# Fresher Job Radar

Fresher Job Radar is being developed incrementally according to `PROJECT_SPEC.md`.
The final development decisions in that document take precedence over earlier text.

## Implemented locally

- **Phase 1:** Python project setup, dependency declarations, configuration, and
  initial tests.
- **Phase 2:** local Telethon user authentication and read-only channel/post
  access. The session stays in a gitignored, restricted local file.
- **Phase 3:** deterministic job parsing, fresher eligibility classification,
  match scoring, and in-memory duplicate detection.
- **Phase 4:** owner-only Telegram Bot API notifications for `MATCHED` results,
  bounded retries, message splitting, and a replaceable notification-store interface.
- **Phase 5:** Cloudflare Worker API, D1 schema and Python API client, intended for local-first development. No cloud resources were created or deployed.
- **Phase 6:** finite Python scanner integrating active Worker channels, checkpointed Telethon reads, existing classification, D1 job/source persistence, and the durable notification outbox.
- **Phase 7:** owner-only Telegram webhook commands, dynamic channel verification, and lifecycle management.
- **Phase 8:** optional advisory Workers AI classification for `REVIEW` posts, with strict output validation, D1 caching, daily limits, and deterministic eligibility safeguards.

Phase 9 adds a scheduled GitHub Actions workflow and deployment runbooks. No
remote Worker deployment, remote D1 migration, production webhook change, or
scheduled scan has been performed by this development session.

## Architecture

- **Local scanner (Phase 6):** a manually invoked bounded Python process uses
  Telethon to read configured channels and deterministic rules to classify posts.
- **Persistence/API (Phases 5–6):** authenticated `/api/v1` Worker routes use D1 for
  channels, messages, jobs and sources, checkpoints, notification outbox,
  scan runs, settings, and processing leases.
- **Local notifications (Phases 4 and 6):** durable matched-job outbox items are
  sent through the Telegram Bot API to the configured owner during explicit live scans.
- **Bot management (Phase 7):** Telegram webhook commands manage pending,
  active, paused, inaccessible, and removed channel records.
- **Optional AI (Phase 8, disabled by default):** only deterministic `REVIEW`
  posts may be submitted to Cloudflare Workers AI after an explicit setting is
  enabled. No provider API key is used by Python.
- **Local classification (implemented):** Python rules extract available job
  details, classify posts as `MATCHED`, `REVIEW`, or `REJECTED`, and score them
  from 0–100. The score is a weighted fit score, not a probability.
- **Local notifications (implemented):** the Bot API sender uses `BOT_TOKEN` and
  `OWNER_TELEGRAM_ID`, always targets the configured owner, and does not share
  credentials or authentication with the Telethon user session.

GitHub Actions and Cloudflare are selected as free-tier targets, with no paid
provider or automatic billing upgrade in the design. Free-tier limits and
account/region eligibility can change and must be checked during deployment.
The project will not require a payment card by design; if an account requires
one, deployment must stop and use a genuinely no-card alternative.

## Local development

Requires Python 3.11 or newer. These setup and test steps do not need credentials
or make network calls after dependencies are installed:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m pytest
```

Copy `.env.example` to `.env` for local settings. Never put actual credentials
in a file that may be committed; `.env` is ignored by Git. Profile preferences
are in `config/job_profile.json`; edit that file or set `JOB_PROFILE_FILE` to a
different JSON file to change graduation years, target roles, skills, preferred
cities, relocation, and internship behavior.

## Local Telegram access (Phase 2)

Create a Telegram API application at `my.telegram.org` and set its API ID and
hash as `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` in your local environment (or
in your ignored `.env`, loaded by your own shell tooling). Do not send these
values, login codes, two-step passwords, or session strings in chat.

Run `py -3.11 scripts/create_telegram_session.py` in a local terminal. The
script prompts locally for your phone number, login code, and (if enabled)
two-step verification password. It stores the reusable StringSession at
`secrets/telegram_session.txt` and restricts the file to your Windows user (or
owner-only permissions on POSIX). It prints only the path, never the session.
Treat that file as account access: do not share it. If exposed, revoke the
session from Telegram **Settings → Devices** (terminate the relevant session),
then generate a new one. When deployment is implemented, put the value into a
GitHub Actions secret using a secure local secret-entry method; never commit
the file.

Set `TELEGRAM_SESSION` locally to the contents of the session file, then use:

```powershell
py -3.11 -m scanner.telegram_cli list
py -3.11 -m scanner.telegram_cli posts @channelusername --limit 5
py -3.11 -m scanner.telegram_cli posts -1001234567890 --after-id 1234 --limit 100
```

The `list` command shows accessible broadcast channels only; it does not read
private chats. Post retrieval is restricted to the selected channel. A normal
fetch returns the latest posts in oldest-first order; checkpoint fetches return
newer posts in ascending message ID order. The code never joins/leaves channels
or sends messages.

## Local job filtering (Phase 3)

Use the Python API with message text or a `ChannelMessage` returned by the
Telegram reader:

```powershell
py -3.11 -c "from scanner.job_parser import parse_job_post; from scanner.job_filter import classify_job; p=parse_job_post('Hiring Software Engineer. Freshers can apply. Batch 2026. CSE. Apply at https://jobs.example/req-12345'); a=classify_job(p); print(a.classification.value, a.score, a.explanation)"
```

The parser preserves raw text and source metadata, returns unknown fields as
empty or `None`, extracts plain/entity links, and flags posts that mention
multiple positions. Eligibility restrictions take priority over the score.
Posts without clear experience eligibility are sent to `REVIEW`; clear
experience or batch exclusions are `REJECTED`. Internship-only posts are
`REVIEW` by default.

The Phase 3 helper compares
normalized application links (removing common tracking parameters), requisition
IDs, exact normalized post text, and matching company/title/location signatures.
Phase 6 additionally checks persistent D1 records, associates repeated source
posts with saved jobs, and uses database constraints for cross-run idempotency.

## Local Telegram notifications (Phase 4)

Create a bot with Telegram's [@BotFather](https://t.me/BotFather): send `/newbot`, follow its prompts
for a display name and unique username ending in `bot`, then copy the token into
the ignored `.env` file as `BOT_TOKEN=...`. Never post that token in chat or put
it in source control. The free Telegram Bot API has no application charge.

Start a conversation with your new bot and send `/start` (the owner must contact
the bot before it can send a private message). The owner ID is the numeric ID of your personal
account. To read it locally using your existing Telethon session, set
`TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, and `TELEGRAM_SESSION` in `.env`, then
run:

```powershell
py -3.11 scripts/show_telegram_user_id.py
```

Copy the printed number into `.env` as `OWNER_TELEGRAM_ID=...`. This helper
prints only your numeric account ID and never prints the session. Alternatively,
use a trusted Telegram ID lookup method; avoid sharing account details with
unknown bots.

With `BOT_TOKEN` and `OWNER_TELEGRAM_ID` set in `.env`, send a sample alert:

```powershell
py -3.11 -m scanner.test_notification
```

The command classifies a sample through the real Phase 3 filter before sending.
It reports only the classification and a safe delivery outcome. Notifications
use plain text without Telegram Markdown/HTML parsing, so post text cannot
inject formatting. Invalid/non-HTTP URLs are omitted. Text is split at Telegram's
UTF-16 message-size limit when needed.
Telegram currently documents a 1–4096 character limit for `sendMessage`; the
sender splits long content within the corresponding UTF-16 bound. See the
[Bot API sendMessage reference](https://core.telegram.org/bots/api#sendmessage).

`MATCHED` results are sent; `REVIEW` and `REJECTED` results are skipped. The
standalone Phase 4 test command uses an in-memory notification store. Phase 6
claims durable D1 outbox items and records confirmed outcomes. A network failure
after Telegram accepts a send but before the response arrives remains ambiguous;
delivery is at-least-once, not guaranteed exactly-once.

## End-to-end scanner (Phase 6)

The scanner is a finite process. `--dry-run` is the default and reads active
channels and checkpoints without writing D1 state or sending messages. It
classifies posts and prints a summary. `--live` takes a D1 lease, processes
messages oldest-first, writes message/job records and matched-job outbox intents,
advances each checkpoint only after the saved prefix is complete, and sends
pending alerts to the configured owner. Channels without a checkpoint inspect
only the latest 20 posts by default.

Set `SCANNER_API_BASE_URL` and the same local `SCANNER_API_SECRET` in the root
`.env` and `worker/.dev.vars`. Keep both files private. Start the local Worker
in one PowerShell window (`cd worker; npm run dev`) and run from the project
root in another:

```powershell
py -3.11 -m scanner.main --dry-run --verbose
py -3.11 -m scanner.main --dry-run --channel @example --max-messages 50
py -3.11 -m scanner.main --live --channel @example
```

`--live` requires the Telethon values, `BOT_TOKEN`, and `OWNER_TELEGRAM_ID` in
the ignored `.env`; the bot must already have been started by its owner. Start
with dry-run because live mode can save state and send real Telegram alerts.
See [docs/phase-6-local-testing.md](docs/phase-6-local-testing.md) for local D1
setup and recovery notes. These commands do not migrate or deploy remote
Cloudflare resources.

## Project layout

```text
scanner/       Python scanner package and authenticated Worker API client
worker/        TypeScript Cloudflare Worker, D1 migration, and local test setup
scripts/       Local Telegram session and account-ID setup utilities
tests/         Offline automated tests
```

See [worker/README.md](worker/README.md) for Worker local setup, D1 schema,
API routes, and Phase 7 bot commands, webhook, and channel verification details.
See [Phase 9 deployment and scheduling](docs/phase-9-deployment.md) for the
workflow, GitHub configuration, remote D1 separation, proposed deploy commands,
free-tier constraints, and recovery runbook.

## Optional AI classification (Phase 8)

AI stays off unless `AI_ENABLED=true` is set explicitly in the local ignored
`.env` and the Worker's local ignored `worker/.dev.vars`. The checked-in
Wrangler configuration keeps `AI_ENABLED=false`, so no production inference is
enabled. AI is not needed for scanning, D1, channel management, or Telegram
notifications. With AI off, the deterministic Python classifier behaves as
before and leaves ambiguous posts as `REVIEW`.

The supported provider is Cloudflare Workers AI through the existing
authenticated Worker API and `AI` binding. The implementation pins
`@cf/meta/llama-3.1-8b-instruct`, the model is on Cloudflare's JSON Mode
supported-model list, and it is not in the current paid-only model list. The
Workers AI Free plan currently documents 10,000 Neurons per UTC day; this is
account/plan dependent and can change. The Worker limits inference to 10 new
requests/day by default (configurable up to 20), caches results by canonical
post content, suppresses repeated failed attempts for an hour, and stores only
the assessment and grounded evidence, not the submitted post text. Cloudflare
states it does not use Workers AI customer content to train or improve models
or services absent explicit consent; review its [data usage policy](https://developers.cloudflare.com/workers-ai/platform/data-usage/).
Free usage exhaustion returns an error; the application does not upgrade a
plan or select a paid provider. On a paid Workers account, usage beyond the
free allocation may be billed by Cloudflare, so keep this setting off unless
you have verified the account's billing status and want to use it.

Configure local use only:

1. Apply D1 migration `0005_optional_ai.sql` locally with `npm run db:migrate:local`.
2. Set `AI_ENABLED=true` in the ignored root `.env` and `worker/.dev.vars`.
3. Keep `AI_MAX_REQUESTS_PER_SCAN=3`, `AI_TIMEOUT_SECONDS=8`,
   `AI_MAX_INPUT_CHARS=6000`, and `AI_MAX_REQUESTS_PER_DAY=10` unless you
   have a reason to lower them. Input size may be reduced but not raised above
   the 6,000-character hard limit.
4. Run the scanner only when you accept that enabled AI may send authorized
   channel post excerpts to Cloudflare and consume the account's Workers AI
   free allocation. This also applies to `--dry-run`; successful AI outcomes
   are cached in D1 by content digest even though dry-run skips job/checkpoint
   writes.

The Worker enforces the same explicit enable switch, a configurable input
limit up to 6,000 characters, a 350-token output limit, a daily request cap, result caching, and an
hourly cooldown after failed requests. The Python client does not retry model
inference. AI output has strict schema, length, confidence, and source-evidence
checks. The unchanged Python rules remain authoritative: AI is never called
for clear `MATCHED`/`REJECTED` posts; AI cannot promote an uncertain post to an
alert unless the deterministic classifier independently proves a match; an
AI rejection is accepted only when the deterministic rules independently
identify an explicit eligibility exclusion. Otherwise the final state remains
`REVIEW`. Provider, quota, timeout, or validation failures also leave it in
`REVIEW` and do not block other channels or use the notification API.

Cloudflare's JSON Mode documentation warns that model output is not guaranteed
to satisfy the schema, so both Worker and Python validate it. See [Workers AI
JSON Mode](https://developers.cloudflare.com/workers-ai/features/json-mode/),
[model details](https://developers.cloudflare.com/workers-ai/models/llama-3.1-8b-instruct/),
and [current pricing and limits](https://developers.cloudflare.com/workers-ai/platform/pricing/).

Tests use mocked provider bindings and require no AI credentials or external
inference. Run `py -3.11 -m pytest -q` from the project root and `npm test`
from `worker`. To inspect only redacted metadata/outcomes from the local D1
database, with the Worker running, use:

```powershell
py -3.11 -c "from scanner.local_env import load_local_env; load_local_env(); import os; from scanner.api_client import WorkerAPIClient; c=WorkerAPIClient(os.environ['SCANNER_API_BASE_URL']); print(c.ai_assessment_summary()); print(c.list_ai_assessments(limit=20))"
```

These endpoints require the existing scanner API secret but never display it.
Assessment rows contain decision, reason, exact evidence, confidence,
missing-information list, provider/model, and timestamp. No production Worker
was deployed or enabled as part of Phase 8.
