from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

from gbrain_ops.config import load_config
from gbrain_ops.contracts import validate_contract
from gbrain_ops.recovery import build_source_inventory, recovery_readiness
from gbrain_ops.service import render_launchd_service


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
