"""Finite, checkpointed orchestration of Telegram scanning and D1 persistence."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from typing import Any

from scanner.deduplicator import extract_requisition_id, normalize_application_url
from scanner.ai_classifier import AIResponseError, apply_advisory_result, assessment_input, validate_result
from scanner.config import Settings
from scanner.job_filter import Classification, JobAssessment, JobFilter
from scanner.job_parser import JobPost, parse_job_post
from scanner.notifier import DeliveryStatus, TelegramNotifier
from scanner.telegram_client import ChannelMessage, InvalidTelegramSessionError, TelegramChannelReader, TelegramReadError

logger = logging.getLogger(__name__)
MAX_BATCH = 500


@dataclass(slots=True)
class ScanStats:
    channels_checked: int = 0
    messages_retrieved: int = 0
    messages_processed: int = 0
    matched: int = 0
    review: int = 0
    rejected: int = 0
    duplicates: int = 0
    notifications_sent: int = 0
    notifications_pending_retry: int = 0
    channel_errors: int = 0
    errors: int = 0
    duration_seconds: float = 0.0
    initial_scans: int = 0
    _processed_by_channel: dict[int, list[int]] = field(default_factory=dict, repr=False)

    def as_dict(self) -> dict[str, Any]:
        return {item.name: getattr(self, item.name) for item in fields(self) if not item.name.startswith("_")}


class Scanner:
    """Dependency-injected scanner; dry runs perform only Worker API GETs."""

    def __init__(self, api: Any, reader: TelegramChannelReader, *, notifier: TelegramNotifier | None = None,
                 job_filter: JobFilter | None = None, recipient_id: int | None = None,
                 dry_run: bool = True, max_messages: int = 100, initial_history: int = 20,
                 channel: str | None = None, ai_enabled: bool | None = None,
                 ai_max_requests_per_scan: int | None = None, ai_provider: Any | None = None) -> None:
        if not 1 <= max_messages <= MAX_BATCH or not 1 <= initial_history <= MAX_BATCH:
            raise ValueError(f"Message limits must be between 1 and {MAX_BATCH}")
        if not dry_run and (not notifier or not recipient_id):
            raise ValueError("Live scanning requires the configured notification client and owner ID")
        self.api, self.reader, self.notifier = api, reader, notifier
        self.filter = job_filter or JobFilter()
        self.recipient_id, self.dry_run = recipient_id, dry_run
        self.max_messages, self.initial_history, self.selected_channel = max_messages, min(initial_history, max_messages), channel
        settings = Settings.from_env()
        self.ai_enabled = settings.ai_enabled if ai_enabled is None else ai_enabled
        self.ai_max_requests_per_scan = settings.ai_max_requests_per_scan if ai_max_requests_per_scan is None else ai_max_requests_per_scan
        self.ai_max_input_chars = settings.ai_max_input_chars
        self.ai_provider = ai_provider
        self._ai_calls = 0
        self._ai_results: dict[str, Any] = {}

    async def run(self) -> ScanStats:
        started = time.monotonic()
        stats = ScanStats()
        self._ai_calls = 0
        self._ai_results.clear()
        run_id, owner = uuid.uuid4().hex, uuid.uuid4().hex
        lease = False
        run_started = False
        fatal: Exception | None = None
        cancelled = False
        monitor_state = getattr(self.api, "get_monitoring_state", None)
        try:
            if monitor_state and monitor_state().get("paused"):
                logger.info("Monitoring is paused; scanner run skipped")
                return stats
            if not self.dry_run:
                lease = self.api.acquire_lease("scanner-global", owner, 3600)
                if not lease:
                    logger.info("Scanner lease is held by another run")
                    return stats
                self.api.record_scan_run(run_id)
                run_started = True
            await self._verify_pending_channels()
            channels = self.api.list_channels("ACTIVE", limit=200)
            before_id = min((int(row["id"]) for row in channels), default=None)
            while len(channels) >= 200 and before_id is not None:
                page = self.api.list_channels("ACTIVE", limit=200, before_id=before_id)
                if not page:
                    break
                channels.extend(page)
                before_id = min(int(row["id"]) for row in page)
            if self.selected_channel:
                channels = [row for row in channels if _matches_channel(row, self.selected_channel)]
            for channel in channels:
                if monitor_state and monitor_state().get("paused"):
                    logger.info("Monitoring was paused; stopping at channel boundary")
                    break
                get_channel = getattr(self.api, "get_channel", None)
                if get_channel:
                    current = get_channel(int(channel["id"]))
                    if current.get("status") != "ACTIVE":
                        continue
                    channel = {**channel, **current}
                if not self.dry_run and not self.api.acquire_lease("scanner-global", owner, 3600):
                    raise RuntimeError("Scanner lease was lost")
                stats.channels_checked += 1
                try:
                    await self._scan_channel(channel, stats)
                except TelegramReadError as exc:
                    if isinstance(exc, InvalidTelegramSessionError):
                        raise
                    stats.channel_errors += 1
                    if type(exc).__name__ == "InaccessibleChannelError" and not self.dry_run:
                        self.api.update_channel(int(channel["id"]), status="INACCESSIBLE", access_status="INACCESSIBLE")
                    logger.warning("Channel scan failed", extra={"channel_id": channel.get("telegram_channel_id"), "failure": type(exc).__name__})
                except Exception as exc:
                    stats.channel_errors += 1
                    logger.error("Channel processing failed", extra={"channel_id": channel.get("telegram_channel_id"), "failure": type(exc).__name__})
                    # Persistence failures are global: stop to avoid claiming a gap was processed.
                    if _is_persistence_error(exc):
                        raise
            if not self.dry_run and not (monitor_state and monitor_state().get("paused")):
                self._deliver_pending(stats, owner)
        except Exception as exc:
            fatal = exc
            stats.errors += 1
            # Show only the exception class and an HTTP status when available.
            # Exception text and environment values can contain sensitive data.
            status = getattr(exc, "status", None)
            safe_status = f" (HTTP {status})" if isinstance(status, int) else ""
            logger.error("Scanner stopped safely: %s%s", type(exc).__name__, safe_status)
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            stats.duration_seconds = round(time.monotonic() - started, 3)
            if run_started:
                try:
                    self.api.record_scan_run(run_id, status="FAILED" if fatal or cancelled else "COMPLETED",
                        messages_scanned=stats.messages_processed, matches=stats.matched,
                        rejections=stats.rejected, review=stats.review, duplicates=stats.duplicates,
                        errors=stats.errors + stats.channel_errors, notifications_sent=stats.notifications_sent)
                except Exception as exc:
                    logger.error("Could not finalize scan statistics", extra={"failure": type(exc).__name__})
                    fatal = fatal or exc
            if lease:
                try:
                    self.api.release_lease("scanner-global", owner)
                except Exception as exc:
                    logger.error("Could not release scanner lease", extra={"failure": type(exc).__name__})
        if fatal:
            raise RuntimeError("Scanner run failed; see sanitized logs") from None
        return stats

    async def _verify_pending_channels(self) -> None:
        resolver = getattr(self.reader, "resolve_channel", None)
        if resolver is None:
            return
        pending = self.api.list_channels("PENDING_VERIFICATION", limit=200)
        before_id = min((int(row["id"]) for row in pending), default=None)
        while len(pending) >= 200 and before_id is not None:
            page = self.api.list_channels("PENDING_VERIFICATION", limit=200, before_id=before_id)
            if not page:
                break
            pending.extend(page)
            before_id = min(int(row["id"]) for row in page)
        for channel in pending:
            monitor_state = getattr(self.api, "get_monitoring_state", None)
            if monitor_state and monitor_state().get("paused"):
                logger.info("Monitoring was paused; stopping channel verification")
                break
            if channel.get("status") != "PENDING_VERIFICATION":
                continue
            try:
                info = await resolver(channel.get("telegram_channel_id") or channel["original_reference"])
                if monitor_state and monitor_state().get("paused"):
                    logger.info("Monitoring was paused; leaving channel pending")
                    break
                if not self.dry_run:
                    self.api.update_channel(int(channel["id"]), status="ACTIVE", telegram_channel_id=info.channel_id,
                        username=info.username, title=info.name, access_status="VERIFIED", verification_error="")
            except TelegramReadError as exc:
                if isinstance(exc, InvalidTelegramSessionError):
                    raise
                if not self.dry_run:
                    self.api.update_channel(int(channel["id"]), status="PENDING_VERIFICATION", access_status="INACCESSIBLE",
                        verification_error=type(exc).__name__)
                logger.warning("Pending channel verification failed", extra={"channel_id": channel.get("id"), "failure": type(exc).__name__})
            except Exception as exc:
                if not self.dry_run:
                    self.api.update_channel(int(channel["id"]), status="PENDING_VERIFICATION", access_status="INACCESSIBLE",
                        verification_error=type(exc).__name__)
                logger.warning("Pending channel verification failed", extra={"channel_id": channel.get("id"), "failure": type(exc).__name__})

    async def _scan_channel(self, channel: dict[str, Any], stats: ScanStats) -> None:
        db_channel_id = int(channel["id"])
        telegram_id = channel.get("telegram_channel_id")
        reference = telegram_id or channel.get("original_reference")
        if not reference:
            raise ValueError("Active channel has no Telegram reference")
        checkpoint = int(channel.get("last_message_id") or channel.get("checkpoint") or 0)
        initial = checkpoint == 0
        limit = min(self.initial_history if initial else self.max_messages, self.max_messages)
        if initial:
            stats.initial_scans += 1
        messages: list[ChannelMessage] = await self.reader.fetch_messages(reference, limit=limit, after_id=None if initial else checkpoint)
        if messages and telegram_id is None and not self.dry_run:
            self.api.update_channel(db_channel_id, telegram_channel_id=messages[0].channel_id,
                title=messages[0].channel_name, access_status="VERIFIED")
        if messages and telegram_id is None and not self.dry_run:
            self.api.update_channel(db_channel_id, telegram_channel_id=messages[0].channel_id,
                title=messages[0].channel_name, access_status="VERIFIED")
        stats.messages_retrieved += len(messages)
        processed_prefix: list[int] = []
        for message in messages:
            if message.channel_id != int(telegram_id or message.channel_id):
                raise ValueError("Telegram returned a different channel than the configured channel")
            try:
                result = self._process_message(channel, message)
                assessment: JobAssessment = result["assessment"]
                logger.info(json.dumps({"event": "message_classified", "channel_id": db_channel_id,
                    "message_id": message.message_id, "classification": assessment.classification.value,
                    "score": assessment.score, "duplicate": bool(result["existing"])}, sort_keys=True))
                if self.dry_run:
                    if assessment.classification is Classification.MATCHED:
                        stats.matched += 1
                    elif assessment.classification is Classification.REVIEW:
                        stats.review += 1
                    else:
                        stats.rejected += 1
                    if result["existing"]:
                        stats.duplicates += 1
                    stats.messages_processed += 1
                    processed_prefix.append(message.message_id)
                    continue
                self.api.save_message(channel_id=db_channel_id, telegram_message_id=message.message_id,
                    posted_at=_iso(message.date), processing_status="PROCESSING", attempt_count=1,
                    content_hash=result["content_hash"])
                existing = result["existing"]
                saved = None
                if (assessment.classification is Classification.MATCHED or assessment.post.title
                        or assessment.post.company or assessment.post.application_urls):
                    saved = self.api.save_job(**_job_record(assessment, channel, message,
                        result["content_hash"], existing.get("id") if existing else None))
                job_id = saved.get("id") if saved else None
                if assessment.classification is Classification.MATCHED:
                    stats.matched += 1
                    if job_id:
                        self.api.create_notification(id=uuid.uuid4().hex, job_id=job_id,
                            recipient_telegram_user_id=str(self.recipient_id))
                elif assessment.classification is Classification.REVIEW:
                    stats.review += 1
                else:
                    stats.rejected += 1
                if existing or (saved and saved.get("duplicate")):
                    stats.duplicates += 1
                self.api.save_message(channel_id=db_channel_id, telegram_message_id=message.message_id,
                    posted_at=_iso(message.date), processing_status="PROCESSED", attempt_count=1,
                    failure_reason=assessment.explanation[:1000], content_hash=result["content_hash"], job_id=job_id)
                stats.messages_processed += 1
                processed_prefix.append(message.message_id)
            except Exception as exc:
                if not self.dry_run:
                    try:
                        self.api.save_message(channel_id=db_channel_id, telegram_message_id=message.message_id,
                            posted_at=_iso(message.date), processing_status="RETRY", attempt_count=1,
                            failure_reason=type(exc).__name__)
                    except Exception:
                        pass
                logger.error("Message stopped at persistence boundary", extra={"channel_id": db_channel_id,
                    "message_id": message.message_id, "failure": type(exc).__name__})
                if _is_persistence_error(exc):
                    if processed_prefix:
                        self.api.advance_checkpoint(db_channel_id, processed_prefix[-1], processed_prefix)
                    raise
                break
        stats._processed_by_channel[db_channel_id] = processed_prefix
        if not self.dry_run and processed_prefix:
            self.api.advance_checkpoint(db_channel_id, processed_prefix[-1], processed_prefix)

    def _process_message(self, channel: dict[str, Any], message: ChannelMessage) -> dict[str, Any]:
        post = parse_job_post(message)
        assessment = self.filter.classify(post)
        content_hash = _content_hash(message.text)
        if assessment.classification is Classification.REVIEW and self.ai_enabled:
            provider = self.ai_provider or getattr(self.api, "classify_ambiguous", None)
            if provider is not None and self._ai_calls < self.ai_max_requests_per_scan:
                cache_key, limited_input = assessment_input(post, max_input_chars=self.ai_max_input_chars)
                try:
                    if cache_key not in self._ai_results:
                        self._ai_calls += 1
                        response = provider(limited_input) if callable(provider) else provider.classify_ambiguous(limited_input)
                        if isinstance(response, dict) and isinstance(response.get("assessment"), dict):
                            response = response["assessment"]
                        self._ai_results[cache_key] = validate_result(response, post, source_text=limited_input["description"])
                    assessment = apply_advisory_result(assessment, self._ai_results[cache_key], self.filter)
                except Exception as exc:
                    # Provider failures are optional; retain deterministic REVIEW and continue.
                    logger.info("AI assessment unavailable", extra={"failure": type(exc).__name__})
        normalized_url = _best_application_url(post)
        req_id = extract_requisition_id(post)
        company, role = _signature(post)
        existing = self.api.find_job(company=company, role=role, normalized_application_url=normalized_url,
            requisition_id=req_id, content_hash=content_hash) if (content_hash or (company and role)) else None
        return {"assessment": assessment, "content_hash": content_hash, "existing": existing}

    def _deliver_pending(self, stats: ScanStats, owner: str) -> None:
        for notification in self.api.pending_notifications():
            if str(notification.get("recipient_telegram_user_id")) != str(self.recipient_id):
                logger.warning("Skipping outbox item for an unconfigured recipient")
                continue
            claim = self.api.claim_notification(notification["id"], owner)
            if not claim.get("claimed"):
                continue
            try:
                job = self.api.get_job(notification["job_id"])
                assessment = _assessment_from_job(job)
                outcome = self.notifier.send_assessment(assessment)  # type: ignore[union-attr]
                if outcome.status in {DeliveryStatus.SENT, DeliveryStatus.DUPLICATE}:
                    self.api.mark_notification_sent(notification["id"])
                    stats.notifications_sent += 1
                else:
                    retrying = _retry_outbox(self.api, notification["id"], outcome.status.value,
                        int(claim.get("attempt_count", 1)), permanent=outcome.status is DeliveryStatus.FAILED)
                    stats.notifications_pending_retry += int(retrying)
            except Exception as exc:
                retrying = _retry_outbox(self.api, notification["id"], type(exc).__name__, int(claim.get("attempt_count", 1)))
                stats.notifications_pending_retry += int(retrying)


def _job_record(a: JobAssessment, channel: dict[str, Any], message: ChannelMessage, content_hash: str, existing_id: str | None) -> dict[str, Any]:
    p = a.post
    url = _best_application_url(p)
    return {"id": existing_id or _job_id(p, content_hash), "company": p.company, "role": p.title,
        "experience": p.experience_requirement, "graduation_batch": p.eligible_batches[0] if p.eligible_batches else None,
        "location": p.location, "skills": list(p.skills), "application_url": url,
        "normalized_application_url": normalize_application_url(url) if url else None,
        "requisition_id": extract_requisition_id(p), "classification": a.classification.value,
        "match_score": a.score, "explanation": a.explanation, "reasons": list(a.reasons), "evidence": list(p.evidence),
        "channel_id": int(channel["id"]), "telegram_message_id": message.message_id,
        "original_message_link": message.message_link, "content_hash": content_hash}


def _assessment_from_job(job: dict[str, Any]) -> JobAssessment:
    sources = job.get("sources") or []
    source = sources[0] if sources else {}
    try:
        skills = tuple(json.loads(job.get("skills_json") or "[]"))
    except (ValueError, TypeError):
        skills = ()
    post = JobPost(raw_text="", company=job.get("company"), title=job.get("role"),
        title_explicit=bool(job.get("role")), experience_requirement=job.get("experience"),
        eligible_batches=(int(job["graduation_batch"]),) if job.get("graduation_batch") else (),
        location=job.get("location"), skills=skills,
        application_urls=(job["application_url"],) if job.get("application_url") else (),
        source_channel_name=source.get("channel_name"), source_message_id=source.get("telegram_message_id"),
        original_message_link=source.get("original_message_link"), posted_at=_parse_dt(source.get("posted_at")))
    try:
        reasons = tuple(json.loads(job.get("reasons_json") or "[]"))
    except (ValueError, TypeError):
        reasons = ()
    return JobAssessment(post, Classification(job["classification"]), int(job.get("match_score") or 0),
        job.get("explanation") or "", reasons)


def _best_application_url(post: JobPost) -> str | None:
    return next((u for u in post.application_urls if not re.search(r"https?://(?:www\.)?(?:t\.me|telegram\.me)/", u, re.I)), None)


def _signature(post: JobPost) -> tuple[str | None, str | None]:
    company = " ".join((post.company or "").casefold().split()) or None
    role = " ".join((post.title or "").casefold().split()) or None
    return company, role


def _content_hash(text: str) -> str:
    normalized = re.sub(r"https?://\S+", " ", text.casefold())
    normalized = " ".join(re.sub(r"[^\w]+", " ", normalized).split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _job_id(post: JobPost, content_hash: str) -> str:
    signature = "|".join((_signature(post)[0] or "", _signature(post)[1] or "", extract_requisition_id(post) or "", _best_application_url(post) or "", content_hash))
    return "job-" + hashlib.sha256(signature.encode()).hexdigest()[:40]


def _matches_channel(channel: dict[str, Any], target: str) -> bool:
    return target.casefold() in {str(channel.get("original_reference", "")).casefold(), str(channel.get("username", "")).casefold(), str(channel.get("telegram_channel_id", ""))}


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _retry_outbox(api: Any, notification_id: str, reason: str, attempt: int, *, permanent: bool = False) -> bool:
    delay = min(3600, 30 * (2 ** min(max(attempt - 1, 0), 7)))
    retry_at = datetime.fromtimestamp(time.time() + delay, timezone.utc).isoformat()
    api.mark_notification_failed(notification_id, reason[:200], retry_at, permanent=permanent)
    return not permanent and attempt < 5


def _is_persistence_error(exc: Exception) -> bool:
    return exc.__class__.__name__ in {"WorkerAPIError", "TimeoutError", "URLError"} or getattr(exc, "retryable", False)
