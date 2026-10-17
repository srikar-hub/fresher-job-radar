"""Validation for Telegram API environment variables."""

from __future__ import annotations

import os


def telegram_credentials() -> tuple[int, str]:
    raw_id = os.getenv("TELEGRAM_API_ID", "").strip()
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
    if not raw_id or not api_hash:
        raise ValueError("TELEGRAM_API_ID and TELEGRAM_API_HASH must be set in the environment")
    try:
        api_id = int(raw_id)
    except ValueError as exc:
        raise ValueError("TELEGRAM_API_ID must be an integer") from exc
    if api_id <= 0:
        raise ValueError("TELEGRAM_API_ID must be positive")
    return api_id, api_hash
