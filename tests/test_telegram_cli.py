import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from scanner import telegram_cli


def test_cli_loads_local_environment_before_building_telegram_client(monkeypatch):
    events = []
    client = SimpleNamespace(
        connect=AsyncMock(),
        disconnect=AsyncMock(),
        is_user_authorized=AsyncMock(return_value=True),
        get_dialogs=AsyncMock(return_value=[]),
    )

    def load_env():
        events.append("env")

    def make_client():
        assert events == ["env"]
        events.append("client")
        return client

    monkeypatch.setattr(telegram_cli, "load_local_env", load_env)
    monkeypatch.setattr(telegram_cli, "make_client", make_client)
    result = asyncio.run(telegram_cli._run(SimpleNamespace(command="list")))

    assert result == 0
    assert events == ["env", "client"]
    client.connect.assert_awaited_once()
    client.disconnect.assert_awaited_once()
