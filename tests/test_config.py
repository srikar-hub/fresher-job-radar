from scanner.config import Settings


def test_defaults_are_safe_and_ai_is_disabled(monkeypatch):
    for key in ("APP_ENV", "LOG_LEVEL", "AI_ENABLED"):
        monkeypatch.delenv(key, raising=False)

    assert Settings.from_env() == Settings()
    assert Settings.from_env().ai_enabled is False


def test_reads_valid_environment_values(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("AI_ENABLED", "yes")

    assert Settings.from_env() == Settings("production", "DEBUG", True)


def test_rejects_invalid_boolean(monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "sometimes")

    try:
        Settings.from_env()
    except ValueError as exc:
        assert "AI_ENABLED" in str(exc)
    else:
        raise AssertionError("Invalid boolean should be rejected")


def test_rejects_invalid_log_level(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "verbose")

    try:
        Settings.from_env()
    except ValueError as exc:
        assert "LOG_LEVEL" in str(exc)
    else:
        raise AssertionError("Invalid log level should be rejected")


def test_ai_request_limits_are_bounded(monkeypatch):
    monkeypatch.setenv("AI_MAX_REQUESTS_PER_SCAN", "21")
    try:
        Settings.from_env()
    except ValueError as exc:
        assert "AI_MAX_REQUESTS_PER_SCAN" in str(exc)
    else:
        raise AssertionError("AI request cap must be bounded")

    monkeypatch.setenv("AI_MAX_REQUESTS_PER_SCAN", "3")
    monkeypatch.setenv("AI_MAX_INPUT_CHARS", "200")
    try:
        Settings.from_env()
    except ValueError as exc:
        assert "AI_MAX_INPUT_CHARS" in str(exc)
    else:
        raise AssertionError("AI input cap must be bounded")
