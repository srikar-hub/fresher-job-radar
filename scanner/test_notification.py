"""Manually send one classified sample alert to the configured Telegram owner."""

from __future__ import annotations

import sys

from scanner.job_filter import JobFilter
from scanner.job_parser import parse_job_post
from scanner.local_env import load_local_env
from scanner.notifier import BotConfig, DeliveryStatus, TelegramNotifier


def main() -> int:
    load_local_env()
    try:
        config = BotConfig.from_env()
        notifier = TelegramNotifier(config)
        sample = """Example Technologies is hiring
Role: Associate Software Engineer
Experience: 0-2 years
Eligible batch: 2026
Location: Bengaluru
Skills: Java, SQL, Spring Boot
Apply: https://example.com/careers/job123"""
        post = parse_job_post(sample)
        assessment = JobFilter().classify(post)
        print(f"Sample classification: {assessment.classification.value} ({assessment.score}/100)")
        if assessment.classification.value != "MATCHED":
            print("Sample was not classified MATCHED; no notification was sent.", file=sys.stderr)
            return 1
        outcome = notifier.send_assessment(assessment)
        print(f"Notification result: {outcome.status.value} — {outcome.detail}")
        return 0 if outcome.status is DeliveryStatus.SENT else 1
    except ValueError as exc:
        print(f"Notification setup error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
