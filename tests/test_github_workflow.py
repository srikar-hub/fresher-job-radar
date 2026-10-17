from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "scan.yml"


def load_workflow():
    # BaseLoader follows GitHub's string-keyed YAML structure (`on` stays a string).
    return yaml.load(WORKFLOW_PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_workflow_has_schedule_and_manual_dry_run_default():
    workflow = load_workflow()
    triggers = workflow["on"]
    assert triggers["schedule"][0]["cron"] == "7,22,37,52 * * * *"
    assert triggers["workflow_dispatch"]["inputs"]["mode"]["default"] == "dry-run"
    assert triggers["workflow_dispatch"]["inputs"]["mode"]["options"] == ["dry-run", "live"]


def test_workflow_uses_bounded_existing_scanner_and_fails_on_error():
    workflow = load_workflow()
    steps = workflow["jobs"]["scan"]["steps"]
    run_step = next(step for step in steps if step.get("name") == "Run bounded scanner")
    assert "python -m scanner.main --live --timeout-seconds 900" in run_step["run"]
    assert "python -m scanner.main --dry-run --timeout-seconds 900" in run_step["run"]
    assert "set -euo pipefail" in run_step["run"]
    assert workflow["jobs"]["scan"]["timeout-minutes"] == "20"


def test_workflow_has_minimal_permissions_concurrency_and_no_artifacts():
    workflow = load_workflow()
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"]["group"]
    assert workflow["concurrency"]["cancel-in-progress"] == "false"
    assert all("upload-artifact" not in str(step) for step in workflow["jobs"]["scan"]["steps"])


def test_workflow_secrets_are_environment_only_and_ai_stays_off():
    workflow = load_workflow()
    env = workflow["jobs"]["scan"]["env"]
    assert env["AI_ENABLED"] == "false"
    assert env["TELEGRAM_SESSION"] == "${{ secrets.TELEGRAM_SESSION }}"
    assert env["SCANNER_API_SECRET"] == "${{ secrets.SCANNER_API_SECRET }}"
    source = WORKFLOW_PATH.read_text(encoding="utf-8").lower()
    assert "printenv" not in source
    assert "tojson(secrets)" not in source
    assert "echo $" not in source


def test_failed_preflight_has_nonzero_status_and_does_not_print_values(capsys, monkeypatch):
    from scanner import ci_preflight

    monkeypatch.setattr(ci_preflight, "validate_environment", lambda _mode, _env: ["missing secret names"])
    assert ci_preflight.main(["live"]) == 1
    output = capsys.readouterr()
    assert "missing secret names" in output.err
