"""Read-only access to channels available to the authenticated Telegram user."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, AsyncIterator

from telethon import TelegramClient, utils
from telethon.errors import (
    AuthKeyInvalidError,
    AuthKeyUnregisteredError,
    ChannelPrivateError,
    FloodWaitError,
    RPCError,
    SessionExpiredError,
    SessionRevokedError,
)
from telethon.tl.types import Channel, MessageEntityTextUrl, MessageEntityUrl


class TelegramReadError(RuntimeError):
    """Base error for safe, user-readable Telegram reader failures."""


class InvalidTelegramSessionError(TelegramReadError):
    """The saved user session is expired, revoked, or invalid."""


class InaccessibleChannelError(TelegramReadError):
    """The requested channel cannot be accessed by this account."""


class TelegramRateLimitError(TelegramReadError):
    """Telegram requested a wait longer than this reader's configured bound."""


class TelegramConnectionError(TelegramReadError):
    """A transient network failure persisted after bounded retries."""


@dataclass(frozen=True, slots=True)
class ChannelInfo:
    channel_id: int
    name: str
    username: str | None


@dataclass(frozen=True, slots=True)
class ChannelMessage:
    message_id: int
    channel_id: int
    channel_name: str
    text: str
    date: datetime | None
    urls: tuple[str, ...]
    message_link: str | None


class TelegramChannelReader:
    """Read messages from channels only; never enumerate or process private chats."""

    def __init__(
        self,
        client: Any,
        *,
        retries: int = 2,
        max_flood_wait_seconds: int = 60,
        sleep: Any = asyncio.sleep,
    ) -> None:
        if retries < 0 or max_flood_wait_seconds < 0:
            raise ValueError("Retry and wait limits cannot be negative")
        self.client = client
        self.retries = retries
        self.max_flood_wait_seconds = max_flood_wait_seconds
        self._sleep = sleep

    async def list_channels(self) -> list[ChannelInfo]:
        """Return accessible broadcast channels, excluding users and group chats."""
        try:
            dialogs = await self._retry(self.client.get_dialogs)
        except TelegramReadError:
            raise
        channels = [
            ChannelInfo(
                channel_id=utils.get_peer_id(dialog.entity),
                name=dialog.name or getattr(dialog.entity, "title", ""),
                username=getattr(dialog.entity, "username", None),
            )
            for dialog in dialogs
            if getattr(dialog, "is_channel", False)
            and isinstance(getattr(dialog, "entity", None), Channel)
            and getattr(dialog.entity, "broadcast", False)
        ]
        return sorted(channels, key=lambda item: item.name.casefold())

    async def fetch_messages(
        self,
        channel: str | int,
        *,
        limit: int = 5,
        after_id: int | None = None,
    ) -> list[ChannelMessage]:
        """Fetch a bounded, oldest-first set of channel posts.

        `after_id` is exclusive. Without a checkpoint this returns the latest
        posts. With a checkpoint, Telethon's reverse iterator reads newer posts
        in ascending ID order for checkpoint-safe processing.
        """
        if limit < 1:
            raise ValueError("limit must be at least 1")
        if after_id is not None and after_id < 0:
            raise ValueError("after_id cannot be negative")
        entity = await self._resolve_channel(channel)
        channel_id = utils.get_peer_id(entity)
        channel_name = getattr(entity, "title", None) or getattr(entity, "username", None) or str(channel)

        async def collect() -> list[ChannelMessage]:
            result: list[ChannelMessage] = []
            iterator = self.client.iter_messages(
                entity,
                limit=limit,
                min_id=after_id or 0,
                reverse=after_id is not None,
            )
            async for message in iterator:
                if getattr(message, "action", None) is not None:
                    continue
                text = getattr(message, "message", None) or getattr(message, "text", None) or ""
                message_id = int(message.id)
                result.append(
                    ChannelMessage(
                        message_id=message_id,
                        channel_id=channel_id,
                        channel_name=channel_name,
                        text=text,
                        date=getattr(message, "date", None),
                        urls=_extract_urls(text, getattr(message, "entities", None)),
                        message_link=_message_link(entity, message_id),
                    )
                )
            return result

        messages = await self._retry(collect)
        # Defensive ordering also makes the contract independent of client mocks.
        return sorted(messages, key=lambda item: item.message_id)

    async def resolve_channel(self, channel: str | int) -> ChannelInfo:
        """Resolve a channel identifier and return its verified stable metadata."""
        entity = await self._resolve_channel(channel)
        if not getattr(entity, "broadcast", False):
            raise InaccessibleChannelError("The selected Telegram channel is not a broadcast channel")
        return ChannelInfo(
            channel_id=utils.get_peer_id(entity),
            name=getattr(entity, "title", None) or getattr(entity, "username", None) or str(channel),
            username=getattr(entity, "username", None),
        )

    async def _resolve_channel(self, channel: str | int) -> Channel:
        try:
            entity = await self._retry(lambda: self.client.get_entity(channel))
        except InaccessibleChannelError:
            raise
        except TelegramReadError:
            raise
        except (ValueError, TypeError) as exc:
            raise InaccessibleChannelError("Channel identifier is invalid or inaccessible") from exc
        if not isinstance(entity, Channel):
            raise InaccessibleChannelError("The selected identifier is not a Telegram channel")
        return entity

    async def _retry(self, operation: Any) -> Any:
        attempts = 0
        while True:
            try:
                return await operation()
            except (
                AuthKeyInvalidError,
                AuthKeyUnregisteredError,
                SessionExpiredError,
                SessionRevokedError,
            ) as exc:
                raise InvalidTelegramSessionError(
                    "Telegram session is invalid or revoked; create a new local session"
                ) from exc
            except ChannelPrivateError as exc:
                raise InaccessibleChannelError(
                    "Channel is private, deleted, or unavailable to this account"
                ) from exc
            except FloodWaitError as exc:
                wait_seconds = int(exc.seconds)
                if wait_seconds > self.max_flood_wait_seconds or attempts >= self.retries:
                    raise TelegramRateLimitError(
                        f"Telegram requested a {wait_seconds}s wait; retry limit reached"
                    ) from exc
                attempts += 1
                await self._sleep(wait_seconds)
            except (ConnectionError, TimeoutError, OSError) as exc:
                if attempts >= self.retries:
                    raise TelegramConnectionError(
                        "Telegram connection failed after bounded retries"
                    ) from exc
                attempts += 1
                await self._sleep(min(2**attempts, 8))
            except RPCError as exc:
                # Do not retry arbitrary RPC failures (permissions, invalid IDs, etc.).
                raise TelegramReadError(f"Telegram request failed: {type(exc).__name__}") from exc


def _extract_urls(text: str, entities: Any) -> tuple[str, ...]:
    urls: list[str] = re.findall(r"https?://[^\s<>\]\[()]+", text)
    for entity in entities or ():
        if isinstance(entity, MessageEntityTextUrl):
            urls.append(entity.url)
        elif isinstance(entity, MessageEntityUrl):
            candidate = text[entity.offset : entity.offset + entity.length]
            if candidate:
                urls.append(candidate)
    return tuple(dict.fromkeys(url.rstrip(".,;!?") for url in urls))


def _message_link(channel: Channel, message_id: int) -> str | None:
    username = getattr(channel, "username", None)
    if username:
        return f"https://t.me/{username}/{message_id}"
    if getattr(channel, "id", None) is not None:
        return f"https://t.me/c/{channel.id}/{message_id}"
    return None


def make_client() -> TelegramClient:
    """Build a Telethon client from environment configuration without logging secrets."""
    import os

    from telethon.sessions import StringSession

    from scanner.telegram_config import telegram_credentials

    api_id, api_hash = telegram_credentials()
    session = os.getenv("TELEGRAM_SESSION", "").strip()
    if not session:
        raise ValueError("TELEGRAM_SESSION is required; create it with the local setup script")
    return TelegramClient(StringSession(session), api_id, api_hash)
