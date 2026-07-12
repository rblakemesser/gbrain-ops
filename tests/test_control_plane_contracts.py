from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

from gbrain_ops.config import load_config
from gbrain_ops.contracts import validate_contract
from gbrain_ops.recovery import build_source_inventory, recovery_readiness
from gbrain_ops.service import render_launchd_service, render_owner_launcher


def example_env(tmp_path: Path) -> dict[str, str]:
    return {
        "GBRAIN_VENDOR_CHECKOUT": str(tmp_path / "vendor"),
        "GBRAIN_OPS_STATE_ROOT": str(tmp_path / "state"),
        "GBRAIN_OPS_ARCHIVE_ROOT": str(tmp_path / "archives"),
        "GBRAIN_OPS_RECEIPT_ROOT": str(tmp_path / "receipts"),
        "GBRAIN_OPS_REPO": str(tmp_path / "repo"),
        "PYTHON": "/usr/bin/python3",
        "GBRAIN_GMAIL_ARCHIVE": str(tmp_path / "gmail"),
        "GBRAIN_CALENDAR_ARCHIVE": str(tmp_path / "calendar"),
        "GBRAIN_MESSAGES_ARCHIVE": str(tmp_path / "messages"),
        "GBRAIN_GRANOLA_ARCHIVE": str(tmp_path / "granola"),
        "GBRAIN_TELEGRAM_ARCHIVE": str(tmp_path / "telegram"),
    }


def test_example_config_expands_and_validates(tmp_path: Path) -> None:
    path = Path(__file__).resolve().parents[1] / "config" / "example.toml"
    value = load_config(path, example_env(tmp_path))
    assert [source["id"] for source in value["sources"]] == ["mail", "calendar", "messages", "meetings", "telegram"]
    assert value["sources"][0]["command_argv"][0] == "/usr/bin/python3"


def test_config_rejects_missing_environment(tmp_path: Path) -> None:
    path = Path(__file__).resolve().parents[1] / "config" / "example.toml"
    with pytest.raises(ValueError, match="environment variable is unset"):
        load_config(path, {})


def test_contract_rejects_absolute_inventory_paths() -> None:
    with pytest.raises(ValueError, match="invalid source-inventory"):
        validate_contract(
            "source-inventory",
            {"schema": "gbrain-ops/source-inventory/v1", "source_id": "x", "root": "/private", "items": [], "digest": "0" * 64},
        )


def test_inventory_is_deterministic_and_readiness_is_lossless(tmp_path: Path) -> None:
    (tmp_path / "nested").mkdir()
    (tmp_path / "b.txt").write_text("b")
    (tmp_path / "nested" / "a.txt").write_text("a")
    first = build_source_inventory("mail", tmp_path)
    second = build_source_inventory("mail", tmp_path)
    assert first == second
    assert [item["path"] for item in first["items"]] == ["b.txt", "nested/a.txt"]
    assert recovery_readiness([first], required_sources=["mail"], imported=2, replayed=2)["status"] == "ready"
    blocked = recovery_readiness([first], required_sources=["mail", "calendar"], imported=2, replayed=1)
    assert blocked["status"] == "blocked"
    assert len(blocked["blockers"]) == 2


def test_launchd_renderer_uses_program_arguments_not_shell_commands() -> None:
    rendered = render_launchd_service(
        label="org.example.gbrain.mail",
        argv=["/usr/bin/python3", "/opt/gbrain/run.py", "--source", "mail"],
        stdout_path="/var/tmp/gbrain-mail.out",
        stderr_path="/var/tmp/gbrain-mail.err",
        interval_seconds=900,
    )
    value = plistlib.loads(rendered)
    assert value["ProgramArguments"] == ["/usr/bin/python3", "/opt/gbrain/run.py", "--source", "mail"]
    assert "Program" not in value
    assert value["StartInterval"] == 900


def test_launchd_renderer_supports_one_long_lived_owner() -> None:
    rendered = render_launchd_service(
        label="org.example.gbrain.owner",
        argv=["/opt/bun", "/opt/gbrain/src/cli.ts", "serve", "--http", "--with-ingestion"],
        stdout_path="/var/tmp/gbrain-owner.out",
        stderr_path="/var/tmp/gbrain-owner.err",
        keep_alive=True,
        throttle_interval=15,
        working_directory="/opt/gbrain",
        environment={"GBRAIN_HOME": "/private/gbrain"},
    )
    value = plistlib.loads(rendered)
    assert value["KeepAlive"] is True
    assert value["RunAtLoad"] is True
    assert value["ThrottleInterval"] == 15
    assert value["WorkingDirectory"] == "/opt/gbrain"
    assert value["EnvironmentVariables"] == {"GBRAIN_HOME": "/private/gbrain"}
    assert "StartInterval" not in value


def test_owner_launcher_execs_fixed_runtime_with_ingestion(tmp_path: Path) -> None:
    launcher = render_owner_launcher(
        bun=tmp_path / "bin" / "bun",
        runtime=tmp_path / "runtime with spaces",
        gbrain_home=tmp_path / "brain",
        env_file=tmp_path / "private env",
        port=3131,
    )
    assert "exec " in launcher
    assert "serve --http --with-ingestion" in launcher
    assert "--bind 127.0.0.1" in launcher
    assert "--suppress-bootstrap-token" in launcher
    assert f"export HOME={tmp_path}" in launcher
    assert f"export GBRAIN_HOME={tmp_path}" in launcher
    assert "runtime with spaces" in launcher
    assert "source " in launcher
