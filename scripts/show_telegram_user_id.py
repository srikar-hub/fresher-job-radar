"""Print only the numeric ID of the locally authenticated Telegram account."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scanner.local_env import load_local_env
from scanner.telegram_client import make_client


async def _show_id() -> int:
    client = make_client()
    try:
        await client.connect()
        if not await client.is_user_authorized():
            print("Telegram session is not authorized; create a local session first.", file=sys.stderr)
            return 1
        me = await client.get_me()
        print(me.id)
        return 0
    finally:
        await client.disconnect()


def main() -> int:
    load_local_env()
    try:
        return asyncio.run(_show_id())
    except Exception as exc:
        # Deliberately omit exception text to prevent credential/request metadata leaks.
        print(f"Could not read Telegram account ID ({type(exc).__name__}).", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
