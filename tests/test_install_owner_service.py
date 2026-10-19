from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install_owner_service.py"
SPEC = importlib.util.spec_from_file_location("install_owner_service", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def owner_args(root: Path, gbrain_home: Path) -> list[str]:
    runtime = root / "runtime"
    (runtime / "src").mkdir(parents=True)
    (runtime / "src" / "cli.ts").write_text("", encoding="utf-8")
    bun = root / "bun"
    bun.write_text("", encoding="utf-8")
    env_file = root / "runtime.env"
    env_file.write_text("", encoding="utf-8")
    return [
        "install_owner_service.py",
        "--runtime", str(runtime),
        "--bun", str(bun),
        "--gbrain-home", str(gbrain_home),
        "--env-file", str(env_file),
        "--launcher", str(root / "owner" / "bin" / "gbrain-owner"),
        "--plist", str(root / "LaunchAgents" / "com.gbrain.owner.plist"),
        "--stdout", str(root / "logs" / "owner.stdout.log"),
        "--stderr", str(root / "logs" / "owner.stderr.log"),
    ]


def test_installer_rejects_user_home_as_gbrain_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path.resolve()
    (root / "home" / ".gbrain").mkdir(parents=True)
    (root / "home" / ".gbrain" / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", owner_args(root, root / "home"))
    with pytest.raises(SystemExit, match="containing config.json"):
        MODULE.main()
    assert not (root / "owner" / "bin" / "gbrain-owner").exists()


def test_installer_exports_parent_of_gbrain_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path.resolve()
    (root / "home" / ".gbrain").mkdir(parents=True)
    (root / "home" / ".gbrain" / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", owner_args(root, root / "home" / ".gbrain"))
    assert MODULE.main() == 0
    launcher = (root / "owner" / "bin" / "gbrain-owner").read_text(encoding="utf-8")
    assert f"export GBRAIN_HOME={root / 'home'}\n" in launcher
    assert (root / "LaunchAgents" / "com.gbrain.owner.plist").is_file()
