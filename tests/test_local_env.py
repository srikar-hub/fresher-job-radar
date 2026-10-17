from scanner.local_env import load_local_env


def test_local_env_loads_without_printing_and_does_not_override_shell(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text(
        "# secret configuration\nBOT_TOKEN='file-token'\nOWNER_TELEGRAM_ID=123\nEXISTING=file-value\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("EXISTING", "shell-value")

    load_local_env(path)

    import os
    assert os.environ["BOT_TOKEN"] == "file-token"
    assert os.environ["OWNER_TELEGRAM_ID"] == "123"
    assert os.environ["EXISTING"] == "shell-value"
