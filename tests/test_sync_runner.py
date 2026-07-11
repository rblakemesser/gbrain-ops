from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from gbrain_ops.sync_runner import parse_command, run_command


def test_parse_command_preserves_quoted_arguments_without_a_shell() -> None:
    assert parse_command("printf '%s' 'hello world'") == ["printf", "%s", "hello world"]
    assert parse_command("printf safe ';' touch never") == ["printf", "safe", ";", "touch", "never"]


def test_run_command_executes_argv() -> None:
    code, output = run_command([sys.executable, "-c", "print('ok')"], timeout=5)
    assert code == 0
    assert output.strip() == "ok"


def test_timeout_terminates_the_entire_process_group(tmp_path: Path) -> None:
    marker = tmp_path / "escaped-child"
    program = (
        "import subprocess,time,sys; "
        "subprocess.Popen([sys.executable,'-c',"
        f"\"import time,pathlib; time.sleep(0.8); pathlib.Path({str(marker)!r}).write_text('bad')\"]); "
        "time.sleep(30)"
    )
    with pytest.raises(Exception) as exc_info:
        run_command([sys.executable, "-c", program], timeout=0.1)
    assert exc_info.type.__name__ == "TimeoutExpired"
    time.sleep(1)
    assert not marker.exists()
