import pytest

from scanner.telegram_config import telegram_credentials


def test_reads_telegram_credentials_from_environment(monkeypatch):
    monkeypatch.setenv("TELEGRAM_API_ID", "123456")
    monkeypatch.setenv("TELEGRAM_API_HASH", "secret-hash")

    assert telegram_credentials() == (123456, "secret-hash")


def test_requires_both_credentials(monkeypatch):
    monkeypatch.delenv("TELEGRAM_API_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_API_HASH", raising=False)

    with pytest.raises(ValueError, match="TELEGRAM_API_ID"):
        telegram_credentials()


def test_rejects_non_integer_api_id(monkeypatch):
    monkeypatch.setenv("TELEGRAM_API_ID", "not-an-id")
    monkeypatch.setenv("TELEGRAM_API_HASH", "hash")

    with pytest.raises(ValueError, match="integer"):
        telegram_credentials()
