from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from scanner.api_client import WorkerAPIError
from scanner.notifier import DeliveryStatus, NotificationOutcome
from scanner.scanner import Scanner
from scanner.telegram_client import ChannelInfo, ChannelMessage, InaccessibleChannelError, InvalidTelegramSessionError


def message(message_id: int, text: str, channel_id: int = 100) -> ChannelMessage:
    return ChannelMessage(message_id, channel_id, "Example Jobs", text, datetime.now(timezone.utc), (), f"https://t.me/example/{message_id}")


MATCHED = "Company: Acme\nRole: Software Engineer\nFreshers can apply\nBatch: 2026\nEducation: CSE\nApply: https://jobs.example/req-12345"
REVIEW = "Hiring Software Engineer\nBatch 2026\nApply: https://jobs.example/req-55555"
REJECTED = "Company: Acme\nRole: Senior Software Engineer\n3+ years experience required\nBatch 2026"


class FakeReader:
    def __init__(self, batches: dict[str, list[ChannelMessage]] | None = None, error: Exception | None = None):
        self.batches = batches or {}
        self.error = error
        self.calls: list[tuple[str | int, int, int | None]] = []

    async def fetch_messages(self, channel, *, limit, after_id=None):
        self.calls.append((channel, limit, after_id))
        if self.error:
            raise self.error
        return self.batches.get(str(channel), [])[:limit]


class FakeNotifier:
    def __init__(self, status=DeliveryStatus.SENT):
        self.status = status
        self.sent = []

    def send_assessment(self, assessment):
        self.sent.append(assessment)
        return NotificationOutcome(self.status, "safe-key", "test outcome")


class FakeAPI:
    def __init__(self, channels=None, checkpoints=None):
        self.channels = channels or []
        self.checkpoints = checkpoints or {}
        self.mutations: list[str] = []
        self.messages = {}
        self.jobs = {}
        self.notifications = []
        self.pending = []
        self.fail_message_id = None
        self.sent_notifications = []
        self.failed_notifications = []
        self.monitor_paused = False

    def list_channels(self, status="ACTIVE", **kwargs):
        return [{**row, "last_message_id": self.checkpoints.get(row["id"], 0)} for row in self.channels if row.get("status", "ACTIVE") == status]

    def get_monitoring_state(self):
        return {"paused": self.monitor_paused}

    def get_channel(self, channel_id):
        row = next(row for row in self.channels if row["id"] == channel_id)
        return {**row, "status": row.get("status", "ACTIVE")}

    def update_channel(self, channel_id, **fields):
        self.mutations.append("channel")
        row = next(row for row in self.channels if row["id"] == channel_id)
        row.update(fields)
        return row

    def find_job(self, **criteria):
        return next((job for job in self.jobs.values() if job["content_hash"] == criteria["content_hash"]), None)

    def save_message(self, **record):
        self.mutations.append("message")
        if record["telegram_message_id"] == self.fail_message_id:
            raise WorkerAPIError("safe test failure", retryable=True)
        self.messages[(record["channel_id"], record["telegram_message_id"])] = record
        return {"created": True}

    def save_job(self, **record):
        self.mutations.append("job")
        self.jobs.setdefault(record["id"], record)
        return {"id": record["id"], "duplicate": record["id"] in self.jobs and len(self.jobs) > 1}

    def create_notification(self, **record):
        self.mutations.append("notification")
        self.notifications.append(record)
        return {"created": True}

    def advance_checkpoint(self, channel_id, message_id, processed_message_ids):
        self.mutations.append("checkpoint")
        self.checkpoints[channel_id] = message_id
        return {"last_message_id": message_id}

    def acquire_lease(self, *args):
        self.mutations.append("lease")
        return True

    def release_lease(self, *args):
        self.mutations.append("release")
        return True

    def record_scan_run(self, *args, **kwargs):
        self.mutations.append("scan_run")
        return {}

    def pending_notifications(self):
        return self.pending

    def claim_notification(self, notification_id, owner):
        return {"claimed": True, "attempt_count": 1}

    def get_job(self, job_id):
        return {"id": job_id, "company": "Acme", "role": "Software Engineer", "classification": "MATCHED",
                "match_score": 90, "explanation": "Suitable", "reasons_json": '["Fresher eligible"]',
                "evidence_json": "[]", "skills_json": "[]", "graduation_batch": 2026,
                "sources": [{"channel_name": "Example Jobs", "telegram_message_id": 5,
                             "original_message_link": "https://t.me/example/5"}]}

    def mark_notification_sent(self, notification_id):
        self.sent_notifications.append(notification_id)

    def mark_notification_failed(self, notification_id, error, retry_at=None, *, permanent=False):
        self.failed_notifications.append((notification_id, error, permanent))


def active_channel(db_id=1, tg_id=100, reference="@example"):
    return {"id": db_id, "telegram_channel_id": tg_id, "original_reference": reference, "username": "example", "title": "Example Jobs"}


def test_empty_channels_is_successful_and_read_only_dry_run():
    api = FakeAPI()
    stats = asyncio.run(Scanner(api, FakeReader()).run())
    assert stats.channels_checked == stats.messages_processed == 0
    assert stats.as_dict()["duration_seconds"] >= 0
    assert api.mutations == []


def test_dry_run_classifies_without_any_worker_mutations():
    api = FakeAPI([active_channel()])
    reader = FakeReader({"100": [message(10, MATCHED), message(12, REVIEW), message(14, REJECTED)]})
    stats = asyncio.run(Scanner(api, reader, dry_run=True, initial_history=3, max_messages=3).run())
    assert (stats.matched, stats.review, stats.rejected) == (1, 1, 1)
    assert stats.messages_processed == 3
    assert api.mutations == []
    assert reader.calls == [(100, 3, None)]


def test_live_persists_each_message_before_advancing_independent_checkpoint():
    api = FakeAPI([active_channel()], checkpoints={1: 8})
    reader = FakeReader({"100": [message(10, MATCHED), message(12, REVIEW)]})
    notifier = FakeNotifier()
    stats = asyncio.run(Scanner(api, reader, notifier=notifier, recipient_id=123, dry_run=False).run())
    assert reader.calls == [(100, 100, 8)]
    assert api.checkpoints[1] == 12
    assert [m["processing_status"] for m in api.messages.values()] == ["PROCESSED", "PROCESSED"]
    assert api.messages[(1, 10)]["failure_reason"]
    assert len(api.notifications) == 1
    assert stats.matched == stats.review == 1
    assert api.mutations.index("notification") < api.mutations.index("checkpoint")


def test_initial_scan_uses_only_configured_recent_history():
    api = FakeAPI([active_channel()], checkpoints={})
    reader = FakeReader({"100": [message(30, MATCHED)]})
    stats = asyncio.run(Scanner(api, reader, notifier=FakeNotifier(), recipient_id=123,
                                 dry_run=False, initial_history=7, max_messages=20).run())
    assert reader.calls == [(100, 7, None)]
    assert stats.initial_scans == 1
    assert api.checkpoints[1] == 30


def test_initial_history_is_capped_by_batch_limit():
    api = FakeAPI([active_channel()])
    reader = FakeReader({"100": [message(30, MATCHED)]})
    asyncio.run(Scanner(api, reader, notifier=FakeNotifier(), recipient_id=123, dry_run=False,
                        initial_history=20, max_messages=5).run())
    assert reader.calls == [(100, 5, None)]


def test_message_persistence_failure_does_not_advance_checkpoint():
    api = FakeAPI([active_channel()], checkpoints={1: 8})
    api.fail_message_id = 12
    reader = FakeReader({"100": [message(10, MATCHED), message(12, REVIEW), message(14, MATCHED)]})
    with pytest.raises(RuntimeError):
        asyncio.run(Scanner(api, reader, notifier=FakeNotifier(), recipient_id=123, dry_run=False).run())
    assert api.checkpoints[1] == 10
    assert api.messages[(1, 10)]["processing_status"] == "PROCESSED"
    assert 14 not in [key[1] for key in api.messages]


def test_channel_failure_isolated_from_next_channel():
    api = FakeAPI([active_channel(1, 100), active_channel(2, 200, "@second")])

    class SelectiveReader(FakeReader):
        async def fetch_messages(self, channel, *, limit, after_id=None):
            if channel == 100:
                raise RuntimeError("unrelated")
            return [message(2, REJECTED, 200)]

    # Generic unexpected Telegram errors are isolated as per-channel errors.
    stats = asyncio.run(Scanner(api, SelectiveReader(), dry_run=True).run())
    assert stats.channels_checked == 2
    assert stats.channel_errors == 1
    assert stats.messages_processed == 1


def test_invalid_telegram_session_stops_the_whole_run():
    api = FakeAPI([active_channel(1, 100), active_channel(2, 200, "@second")])

    class InvalidSessionReader(FakeReader):
        async def fetch_messages(self, channel, *, limit, after_id=None):
            raise InvalidTelegramSessionError("safe test error")

    with pytest.raises(RuntimeError):
        asyncio.run(Scanner(api, InvalidSessionReader(), dry_run=True).run())


def test_live_lease_conflict_skips_without_creating_scan_run():
    api = FakeAPI([active_channel()])
    api.acquire_lease = lambda *args: False
    stats = asyncio.run(Scanner(api, FakeReader(), notifier=FakeNotifier(), recipient_id=123, dry_run=False).run())
    assert stats.channels_checked == 0
    assert "scan_run" not in api.mutations


def test_pending_outbox_is_claimed_sent_and_marked_only_after_bot_confirmation():
    api = FakeAPI()
    api.pending = [{"id": "notice-1", "job_id": "job-1", "recipient_telegram_user_id": "123"}]
    notifier = FakeNotifier(DeliveryStatus.SENT)
    stats = asyncio.run(Scanner(api, FakeReader(), notifier=notifier, recipient_id=123, dry_run=False).run())
    assert len(notifier.sent) == 1
    assert api.sent_notifications == ["notice-1"]
    assert api.failed_notifications == []
    assert stats.notifications_sent == 1


def test_pending_outbox_retries_transient_bot_failure():
    api = FakeAPI()
    api.pending = [{"id": "notice-2", "job_id": "job-1", "recipient_telegram_user_id": "123"}]
    notifier = FakeNotifier(DeliveryStatus.RETRYABLE)
    stats = asyncio.run(Scanner(api, FakeReader(), notifier=notifier, recipient_id=123, dry_run=False).run())
    assert api.sent_notifications == []
    assert api.failed_notifications[0][0:2] == ("notice-2", "RETRYABLE")
    assert stats.notifications_pending_retry == 1


def test_outbox_refuses_to_send_to_non_owner_recipient():
    api = FakeAPI()
    api.pending = [{"id": "notice-3", "job_id": "job-1", "recipient_telegram_user_id": "999"}]
    notifier = FakeNotifier()
    asyncio.run(Scanner(api, FakeReader(), notifier=notifier, recipient_id=123, dry_run=False).run())
    assert notifier.sent == []
    assert api.sent_notifications == []


def test_global_pause_prevents_dry_run_and_live_scanner_work():
    api = FakeAPI([active_channel()]); api.monitor_paused = True
    reader = FakeReader({"100": [message(2, MATCHED)]})
    stats = asyncio.run(Scanner(api, reader, dry_run=True).run())
    assert stats.channels_checked == 0
    assert reader.calls == []
    assert api.mutations == []


def test_pending_channel_is_verified_and_activated_only_in_live_mode():
    pending = {"id":4,"original_reference":"@newjobs","status":"PENDING_VERIFICATION","username":"newjobs"}
    api = FakeAPI([pending])
    class Resolver(FakeReader):
        async def resolve_channel(self, channel):
            assert channel == "@newjobs"
            return ChannelInfo(-100123,"New Jobs","newjobs")
    reader = Resolver()
    asyncio.run(Scanner(api, reader, dry_run=True).run())
    assert api.channels[0]["status"] == "PENDING_VERIFICATION"
    asyncio.run(Scanner(api, reader, notifier=FakeNotifier(), recipient_id=123, dry_run=False).run())
    assert api.channels[0]["status"] == "ACTIVE"
    assert api.channels[0]["telegram_channel_id"] == -100123
    assert api.channels[0]["access_status"] == "VERIFIED"


def test_failed_pending_verification_keeps_channel_unscannable_and_retryable():
    pending = {"id":4,"original_reference":"@privatejobs","status":"PENDING_VERIFICATION"}
    api = FakeAPI([pending])
    class Resolver(FakeReader):
        async def resolve_channel(self, channel):
            raise InaccessibleChannelError("private channel")
    stats = asyncio.run(Scanner(api, Resolver(), notifier=FakeNotifier(), recipient_id=123, dry_run=False).run())
    assert api.channels[0]["status"] == "PENDING_VERIFICATION"
    assert api.channels[0]["access_status"] == "INACCESSIBLE"
    assert api.channels[0]["verification_error"] == "InaccessibleChannelError"
    assert stats.channels_checked == 0


def test_paused_and_removed_channels_are_skipped_and_checkpoint_is_preserved():
    api = FakeAPI([active_channel(1,100), {**active_channel(2,200),"status":"PAUSED"}, {**active_channel(3,300),"status":"REMOVED"}], checkpoints={1:9,2:11,3:12})
    reader = FakeReader({"100": [message(10,MATCHED,100)], "200": [message(13,MATCHED,200)], "300": [message(14,MATCHED,300)]})
    stats = asyncio.run(Scanner(api, reader, dry_run=True).run())
    assert stats.channels_checked == 1
    assert [call[0] for call in reader.calls] == [100]
    assert api.checkpoints == {1:9,2:11,3:12}
