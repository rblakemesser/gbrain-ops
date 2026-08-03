from __future__ import annotations

import importlib.util
import stat
from pathlib import Path

from gbrain_ops.sync_monitor import DEFAULT_JOBS

REPO = Path(__file__).resolve().parents[3]


def load_runtime_installer():
    path = REPO / "scripts" / "install_runtime_links.py"
    spec = importlib.util.spec_from_file_location("install_runtime_links", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runtime_installer_exposes_first_class_plaud_adapter() -> None:
    module = load_runtime_installer()

    assert module.MAPPING["plaud-to-brain"] == {
        "plaud_collector.py": "adapters/plaud/plaud_collector.py",
        "run_fresh_sync.sh": "adapters/plaud/run_fresh_sync.sh",
        "README.md": "adapters/plaud/README.md",
    }


def test_fresh_sync_uses_acknowledged_owner_and_overlap_lock() -> None:
    wrapper = (REPO / "adapters" / "plaud" / "run_fresh_sync.sh").read_text(encoding="utf-8")

    assert "runtime.env" in wrapper
    assert "set -a" in wrapper
    assert 'source "$HERE/runtime.env"' in wrapper
    assert "set +a" in wrapper
    assert "reconcile_archive.py" in wrapper
    assert "--credentials" in wrapper
    assert "--summary-only" in wrapper
    assert "--glob 'plaud/**/*.md'" in wrapper
    assert "status=acknowledged" in wrapper
    assert "status=overlap-denied" in wrapper
    assert "gbrain import" not in wrapper
    assert "gbrain embed" not in wrapper


def test_scheduler_uses_shared_timeout_runner_and_monitor_covers_plaud() -> None:
    script = (REPO / "scripts" / "plaud_fresh_sync_cron.sh").read_text(encoding="utf-8")

    assert "sync_runner.py" in script
    assert "--job plaud-fresh-sync" in script
    assert "--timeout 1800" in script
    assert "plaud-fresh-sync" in DEFAULT_JOBS


def test_executable_surfaces_are_marked_executable() -> None:
    paths = [
        REPO / "adapters" / "plaud" / "plaud_collector.py",
        REPO / "adapters" / "plaud" / "run_fresh_sync.sh",
        REPO / "scripts" / "plaud_fresh_sync_cron.sh",
    ]

    assert all(stat.S_IMODE(path.stat().st_mode) & stat.S_IXUSR for path in paths)
