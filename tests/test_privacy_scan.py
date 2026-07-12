from pathlib import Path
import subprocess
from gbrain_ops.privacy_scan import scan_repository

def init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)

def test_clean_synthetic_fixture_passes(tmp_path: Path) -> None:
    init_repo(tmp_path)
    (tmp_path / "fixture.txt").write_text("person@example.test\n")
    subprocess.run(["git", "add", "fixture.txt"], cwd=tmp_path, check=True)
    assert scan_repository(tmp_path) == []

def test_private_material_fails(tmp_path: Path) -> None:
    init_repo(tmp_path)
    (tmp_path / "bad.txt").write_text("/Users/alice/private\nalice@example.com\n~/workspace/private\n")  # privacy-scan: allow
    subprocess.run(["git", "add", "bad.txt"], cwd=tmp_path, check=True)
    rules = {item.rule for item in scan_repository(tmp_path)}
    assert {"private-absolute-path", "home-workspace-path", "email-address"} <= rules


def test_untracked_private_material_is_scanned(tmp_path: Path) -> None:
    init_repo(tmp_path)
    (tmp_path / "untracked.txt").write_text("alice@example.com\n", encoding="utf-8")  # privacy-scan: allow
    findings = scan_repository(tmp_path)
    assert [(item.path, item.rule) for item in findings] == [("untracked.txt", "email-address")]
