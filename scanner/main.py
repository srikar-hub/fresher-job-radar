"""Command-line entry point for a finite Telegram scan."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Sequence
from urllib.parse import urlsplit

from scanner.api_client import WorkerAPIClient
from scanner.config import Settings
from scanner.local_env import load_local_env
from scanner.notifier import BotConfig, TelegramNotifier
from scanner.scanner import Scanner
from scanner.telegram_client import InvalidTelegramSessionError, TelegramChannelReader, make_client


def validate_api_target(api_url: str, app_env: str) -> None:
    """Keep local development pointed at loopback and production on HTTPS."""
    parsed_api_url = urlsplit(api_url)
    if app_env == "production":
        if parsed_api_url.scheme != "https" or not parsed_api_url.netloc:
            raise ValueError("Production Worker API URL must use HTTPS")
        return
    if (parsed_api_url.scheme != "http" or parsed_api_url.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parsed_api_url.port not in {None, 80, 8787}):
        raise ValueError("Non-production scanner must use the local Worker URL; set APP_ENV=production for a deployed Worker")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scan configured Telegram channels for fresher software jobs")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="read and classify without persistent writes or alerts (default)")
    mode.add_argument("--live", action="store_true", help="persist results and deliver eligible Telegram alerts")
    parser.add_argument("--channel", help="restrict to a configured channel username/reference/Telegram ID")
    parser.add_argument("--max-messages", type=int, default=100, help="maximum new posts per channel per run (1-500)")
    parser.add_argument("--initial-history", type=int, default=20, help="posts to inspect when a channel has no checkpoint")
    parser.add_argument("--timeout-seconds", type=int, default=900, help="maximum scanner runtime including connection setup (60-7200)")
    parser.add_argument("--verbose", action="store_true", help="show sanitized per-post classifications")
    return parser


async def async_main(args: argparse.Namespace) -> int:
    if not 60 <= args.timeout_seconds <= 7200:
        raise ValueError("--timeout-seconds must be between 60 and 7200")
    load_local_env()
    level = logging.INFO if args.verbose else logging.WARNING
    logging.basicConfig(level=level, format="%(levelname)s %(message)s")
    api_url = os.getenv("SCANNER_API_BASE_URL", "").strip()
    api_secret = os.getenv("SCANNER_API_SECRET", "").strip()
    if not api_url or not api_secret:
        raise ValueError("SCANNER_API_BASE_URL and SCANNER_API_SECRET must be set in the local environment")
    settings = Settings.from_env()
    validate_api_target(api_url, settings.app_env)
    api = WorkerAPIClient(api_url, api_secret, ai_timeout=settings.ai_timeout_seconds)
    notifier = None
    owner_id = None
    if args.live:
        bot_config = BotConfig.from_env()
        notifier = TelegramNotifier(bot_config)
        owner_id = bot_config.owner_telegram_id
    client = make_client()
    try:
        async with asyncio.timeout(args.timeout_seconds):
            await client.connect()
            if not await client.is_user_authorized():
                raise InvalidTelegramSessionError("Telegram session is not authorized; recreate it locally")
            scanner = Scanner(api, TelegramChannelReader(client), notifier=notifier, recipient_id=owner_id,
                              dry_run=not args.live, max_messages=args.max_messages,
                              initial_history=args.initial_history, channel=args.channel,
                              ai_enabled=settings.ai_enabled,
                              ai_max_requests_per_scan=settings.ai_max_requests_per_scan)
            stats = await scanner.run()
        print(json.dumps({"mode": "live" if args.live else "dry-run", **stats.as_dict()}, sort_keys=True))
        return 0
    finally:
        try:
            await asyncio.wait_for(client.disconnect(), timeout=10)
        except (TimeoutError, OSError):
            logging.getLogger(__name__).warning("Telegram disconnect did not complete within the shutdown bound")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(async_main(args))
    except KeyboardInterrupt:
        logging.getLogger(__name__).warning("Scanner interrupted; safe checkpoint prefix was preserved")
        return 130
    except Exception as exc:
        # Exception strings from dependencies may include user-controlled text. Emit type only.
        logging.getLogger(__name__).error("Scanner startup/run failed: %s", type(exc).__name__)
        return 1


if __name__ == "__main__":
    sys.exit(main())
