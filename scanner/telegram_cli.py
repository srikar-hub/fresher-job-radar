"""Local read-only CLI for listing authorized channels and fetching posts."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from typing import Sequence

from scanner.local_env import load_local_env
from scanner.telegram_client import TelegramChannelReader, TelegramReadError, make_client


def _channel_identifier(value: str) -> str | int:
    normalized = value.strip()
    try:
        return int(normalized)
    except ValueError:
        return normalized


async def _run(args: argparse.Namespace) -> int:
    # Keep CLI behavior consistent with the scanner entry point: read the ignored
    # project-root .env without overriding values explicitly set in the shell.
    load_local_env()
    try:
        client = make_client()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    try:
        await client.connect()
        if not await client.is_user_authorized():
            print("Telegram session is not authorized; create a new local session.", file=sys.stderr)
            return 2
        reader = TelegramChannelReader(client)
        if args.command == "list":
            channels = await reader.list_channels()
            for channel in channels:
                print(f"{channel.name}\t{channel.username or '(no username)'}\t{channel.channel_id}")
        else:
            messages = await reader.fetch_messages(
                _channel_identifier(args.channel),
                limit=args.limit,
                after_id=args.after_id,
            )
            for message in messages:
                print(json.dumps(asdict(message), ensure_ascii=False, default=str))
        return 0
    except TelegramReadError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        await client.disconnect()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fresher-job-radar-telegram")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="list accessible broadcast channels")
    posts = commands.add_parser("posts", help="fetch recent posts from one channel")
    posts.add_argument("channel", help="channel username (with or without @) or channel ID")
    posts.add_argument("--limit", type=int, default=5)
    posts.add_argument("--after-id", type=int, default=None)
    args = parser.parse_args(argv)
    if args.command == "posts" and args.limit < 1:
        parser.error("--limit must be at least 1")
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
