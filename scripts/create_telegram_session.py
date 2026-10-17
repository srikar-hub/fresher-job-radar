py -3.11 -c "from scanner.local_env import load_local_env; load_local_env(); import os; from scanner.api_client import WorkerAPIClient; print([{k: c.get(k) for k in ('id','title','username','telegram_channel_id','status')} for c in WorkerAPIClient(os.environ['SCANNER_API_BASE_URL']).list_channels('ACTIVE')])""""Create a local Telethon StringSession using terminal-only login prompts."""

from __future__ import annotations

import asyncio
import argparse
import getpass
import os
import subprocess
import sys
from pathlib import Path

from telethon import TelegramClient
from telethon.sessions import StringSession

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SESSION_FILE = PROJECT_ROOT / "secrets" / "telegram_session.txt"


def _restrict_file(path: Path) -> None:
    """Restrict session file to the current user; fail closed if ACL setup fails."""
    if os.name == "nt":
        identity = subprocess.run(
            ["whoami"], capture_output=True, text=True, check=True
        ).stdout.strip()
        subprocess.run(
            [
                "icacls",
                str(path),
                "/inheritance:r",
                "/grant:r",
                f"{identity}:F",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    else:
        path.chmod(0o600)


async def _create_session(session_file: Path, api_id: int, api_hash: str) -> None:
    session_file.parent.mkdir(parents=True, exist_ok=True)
    if session_file.exists():
        raise FileExistsError(f"Session file already exists: {session_file}")

    client = TelegramClient(StringSession(), api_id, api_hash)
    try:
        phone = input("Telegram phone number (with country code): ").strip()
        if not phone:
            raise ValueError("Phone number cannot be empty")
        await client.start(
            phone=phone,
            code_callback=lambda: input("Telegram login code: ").strip(),
            password=lambda: getpass.getpass("Telegram two-step verification password: "),
        )
        session_file.touch(mode=0o600, exist_ok=False)
        try:
            _restrict_file(session_file)
            session_file.write_text(client.session.save(), encoding="utf-8")
        except Exception:
            session_file.unlink(missing_ok=True)
            raise
    finally:
        await client.disconnect()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-file", type=Path, default=DEFAULT_SESSION_FILE)
    args = parser.parse_args()
    try:
        raw_id = os.getenv("TELEGRAM_API_ID", "").strip()
        api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
        if not raw_id or not api_hash:
            raise ValueError("Set TELEGRAM_API_ID and TELEGRAM_API_HASH in the local environment")
        api_id = int(raw_id)
        if api_id <= 0:
            raise ValueError("TELEGRAM_API_ID must be positive")
        asyncio.run(_create_session(args.session_file.resolve(), api_id, api_hash))
    except (ValueError, FileExistsError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except NameError as exc:
        # A missing identifier is useful to diagnose and cannot contain a secret.
        print(f"Telegram session setup failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        # Do not print exception contents from Telegram/auth SDKs; they may contain
        # request metadata. The exception class gives a useful sanitized diagnosis.
        print(f"Telegram session setup failed ({type(exc).__name__}).", file=sys.stderr)
        return 1
    print(f"Session saved with restricted permissions: {args.session_file.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
