from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
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
    assert '${GBRAIN_OPS_RECENT_DAYS:-3}' in wrapper
    assert '${GBRAIN_OPS_MAX_RECORDINGS:-20}' in wrapper
    assert "--max-recordings" in wrapper
    assert "--collection-timeout-seconds" in wrapper
    assert "failure_classes" in wrapper
    assert "RUN_STARTED_AT_NS" in wrapper


def test_failed_collector_ignores_stale_summary_and_still_reconciles_partial_pages(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "plaud"
    runtime.mkdir()
    wrapper = runtime / "run_fresh_sync.sh"
    wrapper.write_text((REPO / "adapters" / "plaud" / "run_fresh_sync.sh").read_text(encoding="utf-8"))
    (runtime / "plaud_collector.py").write_text("raise SystemExit(1)\n", encoding="utf-8")
    summary = runtime / "sync-summary.json"
    summary.write_text(
        json.dumps({"failure_count": 9, "failure_classes": {"rate_limited": 9}}),
        encoding="utf-8",
    )
    os.utime(summary, (1, 1))

    fake_repo = tmp_path / "repo"
    fake_script = fake_repo / "scripts" / "reconcile_archive.py"
    fake_script.parent.mkdir(parents=True)
    fake_script.write_text(
        "import os\nfrom pathlib import Path\nPath(os.environ['RECONCILE_MARKER']).write_text('ran')\n",
        encoding="utf-8",
    )
    marker = tmp_path / "reconciled"
    environment = os.environ.copy()
    environment.update(
        {
            "GBRAIN_OPS_PLAUD_PYTHON": sys.executable,
            "GBRAIN_OPS_OWNER_PYTHON": sys.executable,
            "GBRAIN_OPS_REPO": str(fake_repo),
            "GBRAIN_OWNER_CREDENTIALS": str(tmp_path / "credentials.json"),
            "GBRAIN_OWNER_RECEIPT_ROOT": str(tmp_path / "receipts"),
            "RECONCILE_MARKER": str(marker),
        }
    )

    result = subprocess.run(
        ["bash", str(wrapper)],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode == 1
    assert "failure_classes=unknown:1" in result.stderr
    assert "rate_limited" not in result.stderr
    assert marker.read_text(encoding="utf-8") == "ran"


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
