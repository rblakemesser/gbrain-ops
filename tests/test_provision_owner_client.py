from __future__ import annotations

import importlib.util
import json
import stat
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "provision_owner_client.py"
SPEC = importlib.util.spec_from_file_location("provision_owner_client", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_parse_registration() -> None:
    client_id, secret = MODULE.parse_registration("Client ID: client-123\nClient Secret: secret-456\n")
    assert client_id == "client-123"
    assert secret == "secret-456"


def test_parse_registration_rejects_incomplete_output() -> None:
    with pytest.raises(RuntimeError, match="did not return credentials"):
        MODULE.parse_registration("Client ID: only\n")


def test_atomic_private_json(tmp_path: Path) -> None:
    path = tmp_path / "private" / "client.json"
    MODULE.atomic_private_json(path, {"client_secret": "secret"})
    assert json.loads(path.read_text()) == {"client_secret": "secret"}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
