from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telethon.tl.types import Channel, ChatPhotoEmpty, MessageEntityTextUrl

from scanner.telegram_client import (
    InaccessibleChannelError,
    TelegramChannelReader,
    TelegramConnectionError,
    _extract_urls,
)


def channel(*, broadcast=True, username="devjobs"):
    return Channel(
        id=12345,
        title="Developer Jobs",
        photo=ChatPhotoEmpty(),
        date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        broadcast=broadcast,
        megagroup=not broadcast,
        access_hash=987,
        username=username,
    )


class MockClient:
    def __init__(self, entity=None, dialogs=None, messages=None):
        self.entity = entity or channel()
        self.dialogs = dialogs or []
        self.messages = messages or []
        self.get_dialogs = AsyncMock(return_value=self.dialogs)
        self.get_entity = AsyncMock(return_value=self.entity)
        self.iter_args = None

    def iter_messages(self, entity, **kwargs):
        self.iter_args = kwargs
        async def iterator():
            for message in self.messages:
                yield message
        return iterator()


@pytest.mark.asyncio
async def test_channel_listing_excludes_private_chats_and_groups():
    broadcast = channel()
    group = channel(broadcast=False, username=None)
    dialogs = [
        SimpleNamespace(is_channel=True, entity=broadcast, name="Developer Jobs"),
        SimpleNamespace(is_channel=True, entity=group, name="A group"),
        SimpleNamespace(is_user=True, is_channel=False, entity=object(), name="A person"),
    ]
    reader = TelegramChannelReader(MockClient(dialogs=dialogs))

    result = await reader.list_channels()

    assert len(result) == 1
    assert result[0].name == "Developer Jobs"
    assert result[0].username == "devjobs"
    assert result[0].channel_id == -1000000012345


@pytest.mark.asyncio
async def test_fetch_messages_uses_exclusive_checkpoint_and_returns_oldest_first():
    client = MockClient(messages=[
        SimpleNamespace(id=12, message="new https://example.com/new", date=None, entities=[]),
        SimpleNamespace(id=11, message="old", date=None, entities=[]),
    ])
    reader = TelegramChannelReader(client)

    messages = await reader.fetch_messages("@devjobs", limit=5, after_id=10)

    assert [message.message_id for message in messages] == [11, 12]
    assert messages[1].channel_id == -1000000012345
    assert messages[1].channel_name == "Developer Jobs"
    assert messages[1].urls == ("https://example.com/new",)
    assert messages[1].message_link == "https://t.me/devjobs/12"
    assert client.iter_args == {"limit": 5, "min_id": 10, "reverse": True}


@pytest.mark.asyncio
async def test_fetch_without_checkpoint_reads_latest_posts_and_orders_them_oldest_first():
    client = MockClient(messages=[
        SimpleNamespace(id=15, message="latest", date=None, entities=[]),
        SimpleNamespace(id=13, message="older", date=None, entities=[]),
        SimpleNamespace(id=14, message="middle", date=None, entities=[]),
    ])
    reader = TelegramChannelReader(client)

    messages = await reader.fetch_messages("@devjobs", limit=3)

    assert [message.message_id for message in messages] == [13, 14, 15]
    assert client.iter_args == {"limit": 3, "min_id": 0, "reverse": False}


@pytest.mark.asyncio
async def test_invalid_channel_type_is_rejected():
    reader = TelegramChannelReader(MockClient(entity=object()))

    with pytest.raises(InaccessibleChannelError):
        await reader.fetch_messages("@not_a_channel")


@pytest.mark.asyncio
async def test_transient_network_error_retries_then_succeeds():
    client = MockClient()
    client.get_dialogs = AsyncMock(side_effect=[ConnectionError(), []])
    sleeps = []
    reader = TelegramChannelReader(client, retries=1, sleep=lambda n: _record_sleep(sleeps, n))

    assert await reader.list_channels() == []
    assert sleeps == [2]


async def _record_sleep(sleeps, duration):
    sleeps.append(duration)


@pytest.mark.asyncio
async def test_network_retries_are_bounded():
    client = MockClient()
    client.get_dialogs = AsyncMock(side_effect=ConnectionError())
    reader = TelegramChannelReader(client, retries=1, sleep=lambda _: _record_sleep([], 0))

    with pytest.raises(TelegramConnectionError):
        await reader.list_channels()


def test_url_extraction_combines_plain_and_hyperlink_urls_without_duplicates():
    text = "Apply at https://jobs.example/apply"
    hidden_link = MessageEntityTextUrl(offset=0, length=5, url="https://jobs.example/hidden")

    assert _extract_urls(text, [hidden_link]) == (
        "https://jobs.example/apply",
        "https://jobs.example/hidden",
    )
