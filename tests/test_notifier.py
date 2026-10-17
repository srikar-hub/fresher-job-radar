import json
from dataclasses import replace

import pytest

from scanner.job_filter import Classification, JobFilter
from scanner.job_parser import parse_job_post
from scanner.notifier import (
    BotConfig,
    DeliveryStatus,
    InMemoryNotificationStore,
    TelegramNotifier,
    classify_and_notify,
    format_job_notification,
    notification_key,
    split_telegram_message,
    validate_telegram_url,
)


def assessment(text="""Acme Technologies is hiring
Role: Software Engineer
Experience: 0-2 years
Eligible batch: 2026
Location: Bengaluru
Skills: Java, SQL
Apply: https://jobs.example/careers/req-12345"""):
    return JobFilter().classify(parse_job_post(text))


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, body, timeout):
        self.calls.append((url, json.loads(body), timeout))
        if not self.responses:
            return type("Response", (), {"status_code": 200, "body": b'{"ok":true}'})()
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def response(status=200, body=None):
    from scanner.notifier import _HttpResponse
    return _HttpResponse(status, json.dumps(body or {"ok": True}).encode())


def notifier(transport, *, store=None, sleeps=None, **config_overrides):
    config = BotConfig("12345:super-secret-token", 555123, **config_overrides)
    return TelegramNotifier(
        config,
        store=store,
        transport=transport,
        sleep=(sleeps.append if sleeps is not None else lambda _: None),
    )


def test_sends_matched_alert_to_only_the_configured_owner():
    transport = FakeTransport(response())

    outcome = notifier(transport).send_assessment(assessment())

    assert outcome.status is DeliveryStatus.SENT
    assert transport.calls[0][1]["chat_id"] == 555123
    assert outcome.parts_sent == 1


@pytest.mark.parametrize("classification", [Classification.REVIEW, Classification.REJECTED])
def test_non_matched_jobs_are_suppressed(classification):
    transport = FakeTransport()
    matched = assessment()
    sample = replace(matched, classification=classification)

    outcome = notifier(transport).send_assessment(sample)

    assert outcome.status is DeliveryStatus.SKIPPED
    assert transport.calls == []


def test_format_includes_evidence_fields_and_explanation():
    message = format_job_notification(assessment())

    assert "Acme Technologies" in message
    assert "Software Engineer" in message
    assert "Bengaluru" in message
    assert "2026" in message
    assert "0-2 years" in message
    assert "Java, SQL" in message
    assert "https://jobs.example/careers/req-12345" in message
    assert "Why this job matches" in message


def test_missing_company_and_application_url_are_omitted():
    post = parse_job_post("Hiring Software Engineer. Freshers can apply. Batch 2026.")
    result = JobFilter().classify(post)
    message = format_job_notification(result)

    assert result.classification is Classification.MATCHED
    assert "Company:" not in message
    assert "Apply:" not in message


def test_special_characters_are_safe_in_plain_text_without_parse_mode():
    result = assessment()
    post = replace(result.post, title="Engineer <&> _* [remote]")
    message = format_job_notification(replace(result, post=post))
    transport = FakeTransport(response())

    notifier(transport).send_assessment(replace(result, post=post))

    assert "Engineer <&> _* [remote]" in message
    assert "parse_mode" not in transport.calls[0][1]


def test_long_notifications_are_split_under_telegram_utf16_limit():
    result = assessment()
    post = replace(result.post, title="😀" * 5000)
    result = replace(result, post=post)
    transport = FakeTransport(response(), response(), response())

    outcome = notifier(transport).send_assessment(result)

    assert outcome.status is DeliveryStatus.SENT
    assert len(transport.calls) > 1
    assert all(len(call[1]["text"].encode("utf-16-le")) // 2 <= 4096 for call in transport.calls)


def test_timeout_is_retried_then_succeeds():
    transport = FakeTransport(TimeoutError(), response())
    sleeps = []

    outcome = notifier(transport, sleeps=sleeps, max_retries=1).send_assessment(assessment())

    assert outcome.status is DeliveryStatus.SENT
    assert len(transport.calls) == 2
    assert sleeps == [2]


def test_exhausted_timeout_is_retryable():
    transport = FakeTransport(TimeoutError(), TimeoutError())

    outcome = notifier(transport, max_retries=1).send_assessment(assessment())

    assert outcome.status is DeliveryStatus.RETRYABLE
    assert "token" not in outcome.detail.lower()


def test_rate_limit_retry_after_is_respected():
    transport = FakeTransport(
        response(429, {"ok": False, "error_code": 429, "parameters": {"retry_after": 4}}),
        response(),
    )
    sleeps = []

    outcome = notifier(transport, sleeps=sleeps, max_retries=1).send_assessment(assessment())

    assert outcome.status is DeliveryStatus.SENT
    assert sleeps == [4]


def test_long_rate_limit_delay_returns_retryable_without_sleeping():
    transport = FakeTransport(response(429, {"ok": False, "parameters": {"retry_after": 300}}))
    sleeps = []

    outcome = notifier(transport, sleeps=sleeps).send_assessment(assessment())

    assert outcome.status is DeliveryStatus.RETRYABLE
    assert sleeps == []


def test_invalid_bot_token_fails_safely_without_echoing_secret():
    secret = "12345:super-secret-token"
    transport = FakeTransport(response(401, {"ok": False, "error_code": 401, "description": "Unauthorized"}))
    client = notifier(transport)

    outcome = client.send_assessment(assessment())

    assert outcome.status is DeliveryStatus.FAILED
    assert secret not in repr(client)
    assert secret not in repr(outcome)
    assert "BOT_TOKEN" in outcome.detail


def test_user_must_start_bot_before_private_notifications():
    transport = FakeTransport(response(400, {
        "ok": False,
        "error_code": 400,
        "description": "Bad Request: chat not found",
    }))

    outcome = notifier(transport).send_assessment(assessment())

    assert outcome.status is DeliveryStatus.FAILED
    assert "/start" in outcome.detail


def test_duplicate_is_suppressed_after_first_success():
    transport = FakeTransport(response())
    client = notifier(transport, store=InMemoryNotificationStore())

    first = client.send_assessment(assessment())
    second = client.send_assessment(assessment())

    assert first.status is DeliveryStatus.SENT
    assert second.status is DeliveryStatus.DUPLICATE
    assert len(transport.calls) == 1


def test_owner_recipient_cannot_be_overridden_by_post_content():
    result = assessment("""Company: Acme
Role: Software Engineer
No experience required
Batch: 2026
""")
    transport = FakeTransport(response())

    notifier(transport).send_assessment(result)

    assert transport.calls[0][1]["chat_id"] == 555123


def test_network_failure_is_retryable_and_does_not_log_exception_text(caplog):
    transport = FakeTransport(OSError("contains a private URL and token"), OSError("still failed"))

    outcome = notifier(transport, max_retries=1).send_assessment(assessment())

    assert outcome.status is DeliveryStatus.RETRYABLE
    assert "private URL" not in caplog.text
    assert "super-secret-token" not in caplog.text


def test_bad_or_unsafe_urls_are_not_included():
    assert validate_telegram_url("https://jobs.example/apply")
    assert not validate_telegram_url("javascript:alert(1)")
    assert not validate_telegram_url("https://user:pass@jobs.example/apply")
    assert not validate_telegram_url("https://jobs.example/\nnot-safe")


def test_tracking_parameter_variants_share_notification_key():
    first = parse_job_post("Role: Software Engineer\nBatch: 2026\nhttps://jobs.example/apply?utm_source=one")
    second = replace(first, application_urls=("https://jobs.example/apply?utm_source=two",))

    assert notification_key(first) == notification_key(second)


def test_bot_config_reads_credentials_and_redacts_token(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "my-private-token")
    monkeypatch.setenv("OWNER_TELEGRAM_ID", "123456")

    config = BotConfig.from_env()

    assert config.owner_telegram_id == 123456
    assert "my-private-token" not in repr(config)


def test_bot_config_requires_numeric_owner(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.setenv("OWNER_TELEGRAM_ID", "not-a-number")

    with pytest.raises(ValueError, match="integer"):
        BotConfig.from_env()


def test_classifier_integration_sends_only_matched_result():
    transport = FakeTransport(response())
    client = notifier(transport)
    matched_text = "Hiring Software Engineer. Freshers can apply. Batch 2026. CSE."
    review_text = "Hiring Software Engineer. Batch 2026."

    matched, sent = classify_and_notify(matched_text, client)
    review, suppressed = classify_and_notify(review_text, client)

    assert matched.classification is Classification.MATCHED
    assert sent.status is DeliveryStatus.SENT
    assert review.classification is Classification.REVIEW
    assert suppressed.status is DeliveryStatus.SKIPPED
    assert len(transport.calls) == 1
