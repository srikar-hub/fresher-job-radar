"""Sanitized GitHub Actions configuration checks for finite scanner runs."""
from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping
from urllib.parse import urlsplit


COMMON_REQUIRED = (
    "TELEGRAM_API_ID",
    "TELEGRAM_API_HASH",
    "TELEGRAM_SESSION",
    "SCANNER_API_BASE_URL",
    "SCANNER_API_SECRET",
)
LIVE_REQUIRED = ("BOT_TOKEN", "OWNER_TELEGRAM_ID")


def validate_environment(mode: str, env: Mapping[str, str]) -> list[str]:
    """Return safe error messages; never include environment values."""
    if mode not in {"dry-run", "live"}:
        return ["Scan mode must be dry-run or live."]
    names = COMMON_REQUIRED + (LIVE_REQUIRED if mode == "live" else ())
    missing = [name for name in names if not env.get(name, "").strip()]
    errors = [f"Required GitHub configuration is missing: {', '.join(missing)}."] if missing else []

    if env.get("AI_ENABLED", "false").strip().lower() not in {"false", "0", "no", "off", ""}:
        errors.append("AI_ENABLED must remain false for the scheduled scanner.")

    base_url = env.get("SCANNER_API_BASE_URL", "").strip()
    if base_url:
        parsed = urlsplit(base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            errors.append("SCANNER_API_BASE_URL must be the deployed HTTPS Worker URL.")

    if mode == "live":
        owner = env.get("OWNER_TELEGRAM_ID", "").strip()
        if owner and (not owner.isdecimal() or int(owner) <= 0):
            errors.append("OWNER_TELEGRAM_ID must be a positive numeric Telegram user ID.")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate scanner environment without displaying credentials")
    parser.add_argument("mode", choices=("dry-run", "live"))
    args = parser.parse_args(argv)
    errors = validate_environment(args.mode, os.environ)
    if errors:
        for error in errors:
            print(f"ERROR {error}", file=sys.stderr)
        return 1
    print(f"Scanner configuration is present for {args.mode} mode.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
