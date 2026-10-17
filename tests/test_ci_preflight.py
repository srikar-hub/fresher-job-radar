from scanner.ci_preflight import validate_environment
from scanner.main import main, validate_api_target


BASE_ENV = {
    "TELEGRAM_API_ID": "12345",
    "TELEGRAM_API_HASH": "secret-api-hash",
    "TELEGRAM_SESSION": "secret-session-string",
    "SCANNER_API_BASE_URL": "https://scanner.example.workers.dev",
    "SCANNER_API_SECRET": "secret-api-key",
    "AI_ENABLED": "false",
}


def test_dry_run_needs_no_bot_credentials_or_ai_credentials():
    assert validate_environment("dry-run", BASE_ENV) == []


def test_live_mode_requires_bot_configuration():
    errors = validate_environment("live", BASE_ENV)
    assert len(errors) == 1
    assert "BOT_TOKEN, OWNER_TELEGRAM_ID" in errors[0]
    assert "secret" not in errors[0]


def test_missing_secret_values_are_never_in_diagnostics():
    env = {**BASE_ENV, "TELEGRAM_SESSION": ""}
    errors = validate_environment("dry-run", env)
    assert errors == ["Required GitHub configuration is missing: TELEGRAM_SESSION."]
    assert "secret-session-string" not in " ".join(errors)


def test_production_worker_url_must_be_https():
    env = {**BASE_ENV, "SCANNER_API_BASE_URL": "http://127.0.0.1:8787"}
    assert any("HTTPS Worker URL" in error for error in validate_environment("dry-run", env))


def test_ai_is_rejected_when_enabled():
    env = {**BASE_ENV, "AI_ENABLED": "true"}
    assert validate_environment("dry-run", env) == ["AI_ENABLED must remain false for the scheduled scanner."]


def test_local_scanner_cannot_silently_target_remote_worker():
    validate_api_target("http://127.0.0.1:8787", "development")
    validate_api_target("https://scanner.example.workers.dev", "production")
    try:
        validate_api_target("https://scanner.example.workers.dev", "development")
    except ValueError as exc:
        assert "local Worker URL" in str(exc)
    else:
        raise AssertionError("non-production configuration accepted a remote Worker")


def test_live_owner_id_must_be_positive_numeric():
    env = {**BASE_ENV, "BOT_TOKEN": "bot-secret", "OWNER_TELEGRAM_ID": "0"}
    assert validate_environment("live", env) == [
        "OWNER_TELEGRAM_ID must be a positive numeric Telegram user ID."
    ]


def test_scanner_startup_failure_returns_nonzero_without_credentials(caplog):
    assert main(["--timeout-seconds", "1"]) == 1
    assert "Scanner startup/run failed: ValueError" in caplog.text
