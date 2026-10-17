## FINAL DEVELOPMENT DECISIONS

- Total budget: ₹0.
- No credit or debit card required.
- Use GitHub Actions to scan Telegram channels every 15 minutes.
- Use Python and Telethon for reading Telegram posts.
- Use Python rules as the primary fresher job filtering engine.
- Cloudflare Workers AI is OPTIONAL and DISABLED by default.
- The application must work completely without AI.
- Use Cloudflare D1 for persistent storage.
- Allow dynamic channel management through Telegram bot commands.
- Send notifications only for relevant fresher software jobs.
- Prevent duplicate alerts.
- Recover missed posts after failed or delayed scans.
- Keep credentials secure.
- Build and test the application incrementally using Codex.

These final decisions override any conflicting requirements below.


# Fresher Job Radar — Complete ₹0 Cloud-Based Telegram Job Monitoring Application

## ROLE

Act as a senior Python backend engineer, Telegram API specialist, Cloudflare developer, GitHub Actions engineer, and software architect.

Build a complete, reliable, secure, cloud-based Telegram job monitoring application called **Fresher Job Radar**.

I want actual working code, not just an architecture explanation.

## 1. Project Goal

I am a 2026 Computer Science Engineering graduate searching for fresher software development opportunities in India.

I have joined multiple Telegram job channels where hundreds of job openings are posted.

Many of these openings require professional experience or are irrelevant to my profile.

I want to automatically:

1. Monitor the Telegram channels I select.
2. Collect new job posts every 15 minutes.
3. Identify only legitimate, relevant fresher software jobs.
4. Ignore experienced and irrelevant openings.
5. Detect and remove duplicate jobs across channels.
6. Use Cloudflare Workers AI when advanced eligibility analysis is required.
7. Send relevant job opportunities to my personal Telegram bot automatically.
8. Allow new channels to be added or removed dynamically.
9. Continue running even when my laptop is switched off.
10. Never require paid services, credit cards, or debit cards.

The application should save me time and minimize irrelevant job notifications.

## 2. NON-NEGOTIABLE: ₹0 BUDGET

Use only services available without mandatory credit/debit card verification and without payment.

Preferred stack:

- Python 3.11+
- Telethon
- GitHub Actions scheduled workflows
- Cloudflare Workers Free
- Cloudflare Workers AI Free
- Cloudflare D1 Free
- Telegram Bot API
- Pytest
- GitHub Secrets
- Cloudflare Secrets

Never use:

- Oracle Cloud
- AWS paid infrastructure
- Paid Google Cloud services
- OpenAI paid API
- Paid hosting
- Paid databases
- Paid LLM subscriptions
- Paid monitoring tools

Never enable paid billing or automatic upgrades.

If free-tier capacity is exhausted, gracefully stop optional AI operations and fall back to free Python classification.

If a service cannot be used without card verification in my region/account, clearly identify that limitation and provide a genuinely free alternative.

## 3. My Profile and Filtering Preferences

Education:
- B.Tech/B.E. in Computer Science and Engineering
- Graduation batch: 2026
- Experience: Zero years / fresher

Target roles:
- Software Engineer
- Software Developer
- SDE / SDE 1
- Associate Software Engineer
- Junior Software Engineer
- Graduate Software Engineer
- Software Engineer Trainee
- Graduate Engineer Trainee — Software/IT
- Java Developer
- Backend Developer
- Frontend Developer
- Full Stack Developer
- Python Developer

Technical skills:
- Java
- Spring Boot
- REST APIs
- SQL
- MySQL/PostgreSQL
- JavaScript
- React.js
- HTML/CSS
- Git
- JUnit
- Maven/Gradle
- Data Structures and Algorithms

Locations:
- Hyderabad
- Bengaluru
- Pune
- Chennai
- Other Indian cities
- Remote roles open to Indian applicants

I am willing to relocate.

Preferences must be configurable without modifying source code.

## 4. Core Architecture

Use two separate cloud components.

### Component A: Scheduled Telegram Job Scanner

Host using GitHub Actions.

Schedule the scanner every 15 minutes with this cron expression:

`7,22,37,52 * * * *`

Responsibilities:

1. Start a Python process.
2. Authenticate using Telethon.
3. Retrieve the active monitored-channel list from Cloudflare D1 through a secure API.
4. Fetch new channel messages after the stored checkpoint.
5. Extract job information and application links.
6. Check eligibility.
7. Call Cloudflare Workers AI only when necessary.
8. Deduplicate jobs.
9. Store structured results in D1.
10. Send Telegram alerts for confirmed matches.
11. Save successful processing checkpoints.
12. Exit gracefully.

Use a bounded runtime and handle failures.

Do not assume GitHub Actions schedules execute at exact times.

### Component B: Cloudflare Worker API

Implement a Cloudflare Worker that provides:

- Authenticated API endpoints for the Python scanner.
- Access to Cloudflare D1.
- Cloudflare Workers AI inference.
- Telegram bot webhook processing.
- Dynamic channel configuration.
- Job status management.
- Notification settings.
- Secure internal authorization.

Use Workers Free and D1 Free exclusively.

Keep all API endpoints authenticated and validate incoming Telegram webhook requests.

Ensure bot callbacks are processed quickly and safely.

## 5. Secure Telethon Authentication

This is a critical requirement.

Telegram bots cannot automatically access all the third-party channels joined by my personal Telegram account.

Use Telethon to authenticate my own Telegram account for reading channels I am authorized to access.

Requirements:

1. Provide a local, one-time authentication setup script.
2. Ask for the Telegram login code locally.
3. Support Telegram two-step verification if enabled.
4. Generate a Telethon StringSession securely.
5. Store the session only as an encrypted GitHub Actions secret.
6. Never print the StringSession in CI logs.
7. Never commit session files or secrets.
8. Never store session credentials in Cloudflare D1.
9. Never expose credentials through bot commands.
10. Prevent multiple workflow runs from using the same session simultaneously.
11. Explain how to revoke compromised sessions through Telegram.
12. Limit access to the GitHub repository and workflow configuration.
13. Avoid processing private messages or unrelated chats.
14. Respect Telegram API rate limits, flood waits, and channel permissions.
15. Use the verified channel ID rather than relying only on mutable usernames.

Use the authenticated session exclusively for monitoring approved job channels.

Document the security risks of storing a Telegram user session in CI: it is a sensitive authorization credential, not an ordinary harmless API key.

## 6. Dynamic Channel Management

Initially I may monitor 7–8 channels, but I want to add more later.

The system must support adding and removing channels without changing Python code.

Bot commands:

`/addchannel @channelusername`

`/removechannel @channelusername`

`/pausechannel @channelusername`

`/resumechannel @channelusername`

`/channels`

`/stats`

`/latest`

`/today`

`/settings`

`/pause`

`/resume`

`/help`

`/start`

Requirements:

- Only my configured Telegram user ID can access management commands.
- Save channel configuration in D1.
- Load channel configuration during each scheduled scan.
- Support public channels and private channels that my Telegram account is already authorized to access.
- Resolve and validate newly added channels using Telethon.
- Since the Cloudflare bot webhook cannot independently access the Telethon user session, new channels should initially be marked PENDING_VERIFICATION.
- Validate pending additions during the next GitHub Actions scan.
- Activate monitoring only after access is confirmed.
- Inform me when validation succeeds or fails.
- Acknowledge that new channel monitoring may begin after the next successful scheduled scan, not instantly.
- Reject duplicate entries.
- Support paused, active, pending, and inaccessible statuses.
- Save independent checkpoints for every monitored channel.
- Do not require restarting a server.

## 7. Smart Job Classification

Use a hybrid approach:

**Step 1 — Fast Python rules**

Process every new Telegram post.

Detect whether the message contains a possible job opportunity.

Reject obvious:
- Advertisements
- Unrelated content
- Non-technical positions
- Senior positions
- Roles explicitly requiring 3+ years
- Wrong graduation batches
- Duplicate announcements

**Step 2 — Eligibility analysis**

Accept confirmed matches such as:
- Freshers eligible
- Zero experience required
- 0–1 years
- 0–2 years
- New graduates
- Entry-level software engineering
- Eligible 2026 graduates

Reject explicit requirements inconsistent with my profile.

Do not automatically accept jobs when experience information is missing.

Use three categories:

- MATCHED
- REVIEW
- REJECTED

**Step 3 — Cloudflare Workers AI**

Use AI only for unclear job descriptions.

Ask the AI to return structured JSON containing:

- Company
- Role
- Experience requirement
- Eligible graduation year
- Location
- Relevant technologies
- Application link
- Eligibility classification
- Confidence score
- Short explanation
- Evidence from the source text

Validate the JSON using a strict schema.

Treat model output as untrusted analysis, not confirmed factual evidence.

A high AI confidence score must never override an explicit mandatory requirement excluding freshers.

If Cloudflare AI is unavailable or the free allocation is exhausted, classify uncertain posts as REVIEW instead of silently discarding them.

## 8. Application Link Handling

Extract:
- Official careers links
- Workday links
- Greenhouse links
- Lever links
- Other recruitment links

Preserve original source links.

Do not assume a reachable URL means applications are open.

Classify links as:

- Confirmed open application
- Official application page, status unverified
- Third-party link
- Unreachable or closed
- Missing link

Do not invent deadlines, salaries, or eligibility requirements.

Link checking must not unnecessarily delay the first alert.

## 9. Cloudflare D1 Database

Use D1 as the persistent source of truth.

Design tables for:

**channels**
- channel_id
- username
- title
- access status
- active/paused state
- last successfully processed message ID
- created_at
- updated_at

**messages**
- unique channel ID and message ID
- timestamp
- processing state
- attempts
- error details
- associated job ID

**jobs**
- job_id
- normalized company
- title
- experience
- eligible batch
- location
- application URL
- classification
- score
- created_at

**job_sources**
- job_id
- channel_id
- message_id
- original message link

**notifications**
- notification ID
- job ID
- recipient ID
- state
- attempt count
- sent timestamp

**settings**
- owner Telegram user ID
- notification mode
- preferences
- filters

**scan_runs**
- run ID
- start/end timestamps
- status
- messages scanned
- matches
- errors

Add uniqueness constraints and relevant indexes.

## 10. Reliable Checkpoint Persistence

This is a mandatory reliability feature.

GitHub Actions runners are temporary, so never use a local SQLite database or local files as the only persistent storage.

Use Cloudflare D1.

Requirements:

1. Maintain one checkpoint for each channel.
2. Fetch messages after that channel's stored checkpoint.
3. Process messages in an order that prevents gaps.
4. Save per-message processing status and relevant job data durably.
5. Advance a checkpoint only when all preceding messages in the processed range are safely completed or durably queued for retry.
6. Never move a checkpoint past an unrecorded message.
7. Use database uniqueness constraints for idempotency.
8. Handle overlapping or retried workflow executions.
9. Prevent concurrent scans with GitHub Actions concurrency configuration and an appropriate lease/lock strategy where needed.
10. Use bounded batches so a large backlog does not exceed execution limits.
11. Resume from stored checkpoints after crashes or skipped schedules.
12. Retry failed jobs using controlled backoff.
13. Ensure a retry does not create duplicate job records.
14. Implement a durable notification outbox.
15. Handle Telegram send failures and retry safely.
16. Recognize that exactly-once Telegram delivery cannot be guaranteed when a send succeeds but the sender loses the acknowledgement.
17. Prefer minimizing duplicate alerts while ensuring matching jobs are not silently lost.
18. Provide recovery tests that simulate crashes at critical stages.

Never depend on GitHub Actions artifacts or caches as the only checkpoint store.

## 11. Notification Engine

Send confirmed fresher matches to my personal Telegram bot.

Example:

🎯 FRESHER JOB ALERT

🏢 Company: Example Technologies
💼 Role: Software Engineer
📍 Location: Bengaluru
🎓 Batch: 2026
👨‍💻 Experience: 0–2 Years
🛠 Skills: Java, SQL

✅ Eligibility: Matched
⭐ Match Score: 92/100

💡 Why it matches:
- Suitable for zero-experience candidates
- Relevant software engineering position

🔗 Apply: [Application URL]
📢 Source: [Original Telegram Channel]
🕒 Posted: [Timestamp]

Only populate fields backed by real evidence.

Default to instant notifications after each scheduled scan identifies a match.

Explain that the 15-minute GitHub Actions schedule introduces notification delay and is not real-time monitoring.

Support optional digest notifications later.

Deduplicate notifications across channels.

## 12. Rate Limits and Free-Tier Protection

### GitHub Actions

- Run every 15 minutes.
- Estimate monthly workflow minutes before deployment.
- Account for the user's repository type and available free usage.
- Avoid long-running idle processes.
- Apply concurrency controls.
- Set a sensible workflow timeout.
- Limit backlog processing per run.
- Check actual GitHub usage periodically.

### Telethon

- Handle FloodWait errors.
- Use bounded concurrency.
- Avoid repeatedly fetching old messages.
- Apply retry/backoff behavior.
- Never attempt to bypass Telegram restrictions.

### Cloudflare Workers AI

- Use only free-tier-eligible models.
- Respect the 10,000 Neurons/day free allowance, subject to the current plan limits.
- Track actual usage where supported.
- Maintain a configurable safety margin.
- Reduce token consumption through concise prompts.
- Cache repeated classification decisions.
- Apply rule-based filtering first.
- Respect model-specific request and token limits.
- Handle 429, capacity, and quota errors.
- Stop requesting AI inference before exhausting the configured safety budget.
- Never enable paid upgrades automatically.

### Cloudflare D1 / Workers

- Respect D1 storage, row-read, and row-write limits.
- Use batched requests and indexed queries.
- Avoid unnecessary database reads.
- Keep API requests within the Workers Free limits.
- Use appropriate pagination.
- Implement secure service-to-service authentication.

## 13. Telegram Bot Security

The Cloudflare Worker must:

- Use Telegram webhooks to receive bot commands.
- Validate the Telegram webhook secret token.
- Authorize my Telegram user ID.
- Reject commands from unauthorized users.
- Store tokens in Cloudflare Secrets.
- Never expose secrets through responses.
- Validate channel names, URLs, and command arguments.
- Use prepared statements for D1 operations.
- Restrict internal scanner API access through a dedicated authentication secret.
- Avoid leaking sensitive Telegram session information.

## 14. GitHub Actions Workflow

Generate a complete working workflow file:

`.github/workflows/job-scanner.yml`

Use:
- Scheduled execution at 7, 22, 37, and 52 minutes each hour.
- `workflow_dispatch` for manual tests.
- Python dependency installation.
- Secure environment variable injection.
- Restricted GitHub permissions.
- Concurrency controls.
- Reasonable timeouts.
- Useful sanitized logs.
- Exit status reporting.

Do not hardcode API keys.

Implement a manual test workflow before enabling automatic scheduling.

## 15. Free Deployment Instructions

Provide detailed instructions for:

1. Creating a GitHub repository.
2. Setting up the Cloudflare Free account.
3. Creating the D1 database.
4. Deploying the Cloudflare Worker.
5. Enabling Workers AI.
6. Creating a Telegram bot using BotFather.
7. Obtaining Telegram API ID and API hash.
8. Generating a Telethon StringSession locally.
9. Saving credentials in GitHub and Cloudflare Secrets.
10. Configuring the Telegram bot webhook.
11. Adding the initial monitored channels.
12. Running the first manual test.
13. Verifying successful checkpoint persistence.
14. Enabling the 15-minute schedule.
15. Confirming notifications arrive on my phone.
16. Viewing logs, scan statistics, and free-tier usage.

Do not request my passwords, OTPs, Telegram session strings, or API secrets in chat.

## 16. Testing Requirements

Provide automated tests covering at least:

- Fresher job acceptance
- Experienced job rejection
- 2026 batch eligibility
- Wrong graduation batch
- Non-technical jobs
- Ambiguous eligibility
- Cloudflare AI failures
- Duplicate posts from different channels
- Same application URL with different tracking parameters
- New channel validation
- Channel removal and pausing
- Offline recovery
- Missed scheduled scans
- D1 API failures
- Telegram rate limits
- Notification retries
- Partial batch crashes
- Concurrent scanner attempts
- Checkpoint gap prevention
- Unauthorized bot commands
- Invalid AI JSON responses

Use mocks for external services.

Avoid making paid or unnecessary API calls during testing.

## 17. Project Structure

Create a clean maintainable project, for example:

fresher-job-radar/
- .github/workflows/job-scanner.yml
- scanner/
  - main.py
  - telegram_client.py
  - job_parser.py
  - job_filter.py
  - ai_classifier.py
  - deduplicator.py
  - checkpoint_manager.py
  - notifier.py
  - api_client.py
  - config.py
- worker/
  - src/index.ts
  - src/routes/
  - src/database/
  - src/telegram/
  - migrations/
  - wrangler.jsonc
- scripts/
  - create_telegram_session.py
- tests/
- requirements.txt
- .env.example
- .gitignore
- README.md

Adjust this structure if it improves maintainability.

## 18. Development Phases

### Phase 1 — Working MVP

Build:
- Scheduled GitHub Actions scanner
- Telethon authentication
- Telegram channel message reading
- Cloudflare D1 persistent checkpoints
- Python eligibility filtering
- Duplicate detection
- Telegram job alerts
- Basic Cloudflare Worker APIs

### Phase 2 — Smart AI

Add:
- Cloudflare Workers AI
- Structured extraction
- Match scoring
- Ambiguous-job review queue
- AI usage controls
- Better deduplication

### Phase 3 — Dynamic Management

Add:
- Telegram bot commands
- Dynamic channel configuration
- Channel validation
- Pause/resume controls
- Job statistics
- Notification preferences

### Phase 4 — Reliability

Add:
- Stronger crash recovery
- Durable notification outbox
- Backlog processing
- Detailed tests
- Operational logging
- Usage alerts

Keep every phase usable without paid services.

## 19. Expected Final Deliverables

Provide:

1. Architecture explanation.
2. Complete runnable source code.
3. GitHub Actions workflow.
4. Cloudflare Worker implementation.
5. D1 SQL migrations.
6. Telegram user authentication script.
7. Telegram bot integration.
8. Job filtering and AI classification.
9. Dynamic channel commands.
10. Database checkpoint logic.
11. Duplicate prevention.
12. Unit tests.
13. Configuration templates.
14. Step-by-step deployment guide.
15. Free-tier usage calculations.
16. Troubleshooting instructions.

Do not provide placeholder implementations for essential functionality.

If the complete application is too large for one response, implement it in coherent, runnable milestones, beginning with Phase 1.

## 20. Definition of Success

The application is successful when:

- My laptop can be turned off.
- Scheduled checks run in GitHub Actions.
- My configured Telegram channels are scanned.
- I can add or remove monitored channels through the bot.
- Only relevant fresher software-development jobs trigger automatic alerts.
- Duplicate openings are suppressed.
- Cloudflare AI improves classification for uncertain postings.
- Application links and eligibility evidence are preserved.
- Missed runs do not cause permanent loss of posts.
- Credentials remain protected.
- Every required component operates within a zero-cost plan, without a credit/debit card or paid upgrade.

**Start by explaining the final architecture and any limitations in the free tiers. Then provide the full implementation, beginning with the MVP.**