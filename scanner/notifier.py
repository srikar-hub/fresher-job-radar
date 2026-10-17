"""Owner-only Telegram Bot API notifications for matched fresher jobs."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import socket
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from scanner.deduplicator import extract_requisition_id, normalize_application_url
from scanner.job_filter import Classification, JobAssessment, JobFilter
from scanner.job_parser import JobPost, parse_job_post

logger = logging.getLogger(__name__)
TELEGRAM_TEXT_LIMIT = 4096


class DeliveryStatus(str, Enum):
    SENT = "SENT"
    FAILED = "FAILED"
    RETRYABLE = "RETRYABLE"
    SKIPPED = "SKIPPED"
    DUPLICATE = "DUPLICATE"


@dataclass(frozen=True, slots=True)
class NotificationOutcome:
    status: DeliveryStatus
    notification_key: str
    detail: str
    parts_sent: int = 0
    parts_total: int = 0


@dataclass(frozen=True, slots=True)
class BotConfig:
    """Validated Bot API settings. The token is excluded from repr output."""

    bot_token: str
    owner_telegram_id: int
    timeout_seconds: float = 15.0
    max_retries: int = 2
    max_retry_after_seconds: int = 60
    max_message_units: int = TELEGRAM_TEXT_LIMIT

    def __repr__(self) -> str:
        return (
            "BotConfig(bot_token=<redacted>, "
            f"owner_telegram_id={self.owner_telegram_id}, timeout_seconds={self.timeout_seconds}, "
            f"max_retries={self.max_retries}, max_retry_after_seconds={self.max_retry_after_seconds}, "
            f"max_message_units={self.max_message_units})"
        )

    @classmethod
    def from_env(cls) -> BotConfig:
        token = os.getenv("BOT_TOKEN", "").strip()
        raw_owner = os.getenv("OWNER_TELEGRAM_ID", "").strip()
        if not token:
            raise ValueError("BOT_TOKEN must be set in the local environment")
        if not raw_owner:
            raise ValueError("OWNER_TELEGRAM_ID must be set in the local environment")
        try:
            owner_id = int(raw_owner)
        except ValueError as exc:
            raise ValueError("OWNER_TELEGRAM_ID must be an integer") from exc
        if owner_id <= 0:
            raise ValueError("OWNER_TELEGRAM_ID must be positive")
        return cls(bot_token=token, owner_telegram_id=owner_id)


class NotificationStore(Protocol):
    """Persistence boundary for duplicate suppression; implement with D1 later."""

    def has_sent(self, notification_key: str) -> bool:
        """Return whether a complete notification with this key was sent."""

    def mark_sent(self, notification_key: str) -> None:
        """Record a fully delivered notification."""


class InMemoryNotificationStore:
    """Per-process dedupe only; not durable across scanner restarts."""

    def __init__(self) -> None:
        self._sent: set[str] = set()

    def has_sent(self, notification_key: str) -> bool:
        return notification_key in self._sent

    def mark_sent(self, notification_key: str) -> None:
        self._sent.add(notification_key)


@dataclass(frozen=True, slots=True)
class _HttpResponse:
    status_code: int
    body: bytes


Transport = Callable[[str, bytes, float], _HttpResponse]


def _urllib_transport(url: str, body: bytes, timeout: float) -> _HttpResponse:
    request = Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "FresherJobRadar/0.1"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return _HttpResponse(response.status, response.read())
    except HTTPError as exc:
        return _HttpResponse(exc.code, exc.read())


class TelegramNotifier:
    """Send plain-text Bot API messages only to the configured owner chat ID."""

    def __init__(
        self,
        config: BotConfig,
        *,
        store: NotificationStore | None = None,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if config.timeout_seconds <= 0 or config.max_retries < 0:
            raise ValueError("Timeout must be positive and max_retries cannot be negative")
        if config.max_retry_after_seconds < 0 or not 1 <= config.max_message_units <= TELEGRAM_TEXT_LIMIT:
            raise ValueError("Retry wait and Telegram message length limits are invalid")
        self._token = config.bot_token
        self._owner_id = config.owner_telegram_id
        self._timeout = config.timeout_seconds
        self._max_retries = config.max_retries
        self._max_retry_after = config.max_retry_after_seconds
        self._max_message_units = config.max_message_units
        self._store = store if store is not None else InMemoryNotificationStore()
        self._transport = transport or _urllib_transport
        self._sleep = sleep

    def __repr__(self) -> str:
        return f"TelegramNotifier(owner_telegram_id={self._owner_id}, bot_token=<redacted>)"

    def send_assessment(self, assessment: JobAssessment) -> NotificationOutcome:
        """Notify only for the actual MATCHED result returned by Phase 3."""
        key = notification_key(assessment.post)
        if assessment.classification is not Classification.MATCHED:
            return NotificationOutcome(DeliveryStatus.SKIPPED, key, "Only MATCHED jobs are sent.")
        try:
            already_sent = self._store.has_sent(key)
        except Exception as exc:
            logger.error(
                "Notification duplicate state could not be checked",
                extra={"event": "telegram_notification_store_failure", "failure": type(exc).__name__},
            )
            return NotificationOutcome(
                DeliveryStatus.RETRYABLE, key,
                "Duplicate state is unavailable; notification was not sent.",
            )
        if already_sent:
            return NotificationOutcome(DeliveryStatus.DUPLICATE, key, "This job was already sent in this store.")

        continuation = "🎯 FRESHER JOB ALERT (continued)\n"
        continuation_units = _utf16_units(continuation)
        use_continuation = self._max_message_units > continuation_units + 1
        chunk_limit = self._max_message_units - continuation_units if use_continuation else self._max_message_units
        chunks = split_telegram_message(format_job_notification(assessment), chunk_limit)
        sent_parts = 0
        for part_number, chunk in enumerate(chunks, start=1):
            if part_number > 1 and use_continuation:
                chunk = continuation + chunk
            result = self._send_chunk(chunk)
            if result.status is not DeliveryStatus.SENT:
                logger.warning(
                    "Telegram notification delivery did not complete",
                    extra={"event": "telegram_notification_delivery", "outcome": result.status.value,
                           "part": part_number, "parts_total": len(chunks), "failure": result.detail},
                )
                return NotificationOutcome(
                    result.status,
                    key,
                    result.detail,
                    parts_sent=sent_parts,
                    parts_total=len(chunks),
                )
            sent_parts += 1

        try:
            self._store.mark_sent(key)
        except Exception as exc:
            logger.error(
                "Notification sent but its dedupe record could not be saved",
                extra={"event": "telegram_notification_store_failure", "failure": type(exc).__name__},
            )
            return NotificationOutcome(
                DeliveryStatus.RETRYABLE,
                key,
                "Telegram accepted the message, but duplicate state could not be saved.",
                parts_sent=sent_parts,
                parts_total=len(chunks),
            )
        return NotificationOutcome(
            DeliveryStatus.SENT,
            key,
            "Telegram confirmed delivery.",
            parts_sent=sent_parts,
            parts_total=len(chunks),
        )

    def _send_chunk(self, text: str) -> NotificationOutcome:
        key = ""  # A chunk outcome does not independently own notification identity.
        endpoint = f"https://api.telegram.org/bot{self._token}/sendMessage"
        payload = json.dumps(
            {"chat_id": self._owner_id, "text": text, "disable_web_page_preview": True},
            ensure_ascii=False,
        ).encode("utf-8")

        attempt = 0
        while True:
            try:
                response = self._transport(endpoint, payload, self._timeout)
            except (TimeoutError, socket.timeout, URLError, OSError) as exc:
                if attempt < self._max_retries:
                    attempt += 1
                    self._sleep(min(2**attempt, 8))
                    continue
                return NotificationOutcome(
                    DeliveryStatus.RETRYABLE, key,
                    "Telegram network request timed out or could not connect.",
                )
            except Exception as exc:
                logger.error(
                    "Telegram transport failed",
                    extra={"event": "telegram_transport_failure", "failure": type(exc).__name__},
                )
                return NotificationOutcome(DeliveryStatus.FAILED, key, "Telegram transport failed.")

            data = _decode_response(response.body)
            if response.status_code == 429 or (data and data.get("error_code") == 429):
                retry_after = _retry_after_seconds(data)
                if retry_after is not None and attempt < self._max_retries and retry_after <= self._max_retry_after:
                    attempt += 1
                    self._sleep(retry_after)
                    continue
                return NotificationOutcome(
                    DeliveryStatus.RETRYABLE, key,
                    "Telegram rate limited delivery; retry later.",
                )

            if 200 <= response.status_code < 300 and data and data.get("ok") is True:
                return NotificationOutcome(DeliveryStatus.SENT, key, "Telegram confirmed delivery.")

            if response.status_code >= 500:
                if attempt < self._max_retries:
                    attempt += 1
                    self._sleep(min(2**attempt, 8))
                    continue
                return NotificationOutcome(DeliveryStatus.RETRYABLE, key, "Telegram service is temporarily unavailable.")

            error_code = data.get("error_code") if data else response.status_code
            if error_code == 401:
                return NotificationOutcome(DeliveryStatus.FAILED, key, "Telegram rejected the bot token; check BOT_TOKEN locally.")
            if error_code == 400:
                description = str(data.get("description", "")).casefold() if data else ""
                if "chat not found" in description or "bot can't initiate" in description:
                    detail = "Open your bot in Telegram and send /start before testing notifications."
                else:
                    detail = "Telegram rejected the message or recipient; verify OWNER_TELEGRAM_ID."
                return NotificationOutcome(DeliveryStatus.FAILED, key, detail)
            return NotificationOutcome(DeliveryStatus.FAILED, key, "Telegram returned an unsuccessful response.")


def format_job_notification(assessment: JobAssessment) -> str:
    """Format evidenced fields as plain text (no HTML/Markdown parse mode)."""
    post = assessment.post
    lines = ["🎯 FRESHER JOB ALERT"]
    _append_field(lines, "🏢 Company", post.company)
    _append_field(lines, "💼 Role", post.title)
    _append_field(lines, "📍 Location", post.location)
    if post.eligible_batches:
        _append_field(lines, "🎓 Eligible Batch", ", ".join(map(str, post.eligible_batches)))
    _append_field(lines, "👨‍💻 Experience", post.experience_requirement)
    if post.skills:
        _append_field(lines, "🛠 Skills", ", ".join(post.skills))
    _append_field(lines, "📅 Deadline", post.deadline)
    lines.extend(("", "✅ Status: Fresher Eligible", f"⭐ Match Score: {assessment.score}/100"))
    if assessment.explanation:
        lines.extend(("", "💡 Why this job matches:", _safe_plain_text(assessment.explanation)))
    valid_app_urls = [url for url in post.application_urls if validate_telegram_url(url)]
    if valid_app_urls:
        lines.extend(("", "🔗 Apply:", *valid_app_urls))
    if post.source_channel_name:
        _append_field(lines, "📢 Source", post.source_channel_name)
    if post.original_message_link and validate_telegram_url(post.original_message_link):
        _append_field(lines, "🔗 Original post", post.original_message_link)
    if post.posted_at is not None:
        _append_field(lines, "🕒 Posted", post.posted_at.isoformat(sep=" "))
    return "\n".join(lines)


def validate_telegram_url(value: str) -> bool:
    """Allow only absolute HTTP(S) URLs without embedded credentials or controls."""
    if not value or any(ord(character) < 32 for character in value):
        return False
    try:
        parts = urlsplit(value.strip())
        return (
            parts.scheme.casefold() in {"http", "https"}
            and bool(parts.hostname)
            and parts.username is None
            and parts.password is None
        )
    except ValueError:
        return False


def split_telegram_message(text: str, max_units: int = TELEGRAM_TEXT_LIMIT) -> list[str]:
    """Split plain text within Telegram's UTF-16 message length bound."""
    if not 1 <= max_units <= TELEGRAM_TEXT_LIMIT:
        raise ValueError(f"max_units must be between 1 and {TELEGRAM_TEXT_LIMIT}")
    chunks: list[str] = []
    current = ""
    current_units = 0
    for line in text.splitlines(keepends=True):
        line_units = _utf16_units(line)
        if line_units > max_units:
            if current:
                chunks.append(current.rstrip("\n"))
                current, current_units = "", 0
            remaining = line
            while remaining:
                part, remaining = _take_utf16_prefix(remaining, max_units)
                chunks.append(part.rstrip("\n") if remaining else part.rstrip("\n"))
            continue
        if current_units + line_units > max_units and current:
            chunks.append(current.rstrip("\n"))
            current, current_units = "", 0
        current += line
        current_units += line_units
    if current:
        chunks.append(current.rstrip("\n"))
    return chunks or [""]


def notification_key(post: JobPost) -> str:
    """Build a stable, non-secret hash from the strongest available job identity."""
    company = _identity_text(post.company)
    requisition_id = extract_requisition_id(post)
    if requisition_id:
        identity = f"req:{company}:{requisition_id}"
    else:
        app_urls = [
            normalize_application_url(url)
            for url in post.application_urls
            if validate_telegram_url(url) and urlsplit(url).hostname not in {"t.me", "telegram.me"}
        ]
        if app_urls:
            identity = f"url:{app_urls[0]}"
        elif company and post.title:
            identity = ":".join(("job", company, _identity_text(post.title), _identity_text(post.location)))
        elif post.source_channel_id is not None and post.source_message_id is not None:
            identity = f"source:{post.source_channel_id}:{post.source_message_id}"
        else:
            identity = f"text:{_identity_text(post.raw_text)}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def classify_and_notify(
    message: Any,
    notifier: TelegramNotifier,
    *,
    job_filter: JobFilter | None = None,
) -> tuple[JobAssessment, NotificationOutcome]:
    """Run the Phase 3 parser/classifier and deliver only its MATCHED result."""
    post = parse_job_post(message, profile=(job_filter.profile if job_filter else None))
    assessment = (job_filter or JobFilter()).classify(post)
    return assessment, notifier.send_assessment(assessment)


def _append_field(lines: list[str], label: str, value: str | None) -> None:
    if value:
        lines.append(f"{label}: {_safe_plain_text(value)}")


def _safe_plain_text(value: str) -> str:
    # We intentionally omit Telegram parse_mode. HTML/Markdown characters stay
    # literal; control characters are removed and embedded line breaks collapsed.
    value = "".join(character for character in value if character in "\n\t" or ord(character) >= 32)
    return re.sub(r"\s+", " ", value).strip()


def _decode_response(body: bytes) -> dict[str, Any] | None:
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _retry_after_seconds(data: dict[str, Any] | None) -> int | None:
    if not data:
        return None
    parameters = data.get("parameters")
    retry_after = parameters.get("retry_after") if isinstance(parameters, dict) else None
    try:
        seconds = int(retry_after)
    except (TypeError, ValueError):
        return None
    return max(0, seconds)


def _identity_text(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).strip()


def _utf16_units(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _take_utf16_prefix(value: str, max_units: int) -> tuple[str, str]:
    used = 0
    split_at = 0
    for index, character in enumerate(value):
        units = _utf16_units(character)
        if used + units > max_units:
            break
        used += units
        split_at = index + 1
    if split_at == 0:
        raise ValueError("max_units is too small for a Unicode character")
    return value[:split_at], value[split_at:]
