from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"


def load_schema(name: str) -> dict[str, Any]:
    path = SCHEMA_DIR / f"{name}.schema.json"
    if not path.is_file():
        raise ValueError(f"unknown contract schema: {name}")
    return json.loads(path.read_text(encoding="utf-8"))


def validate_contract(name: str, value: Any) -> None:
    validator = Draft202012Validator(load_schema(name), format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(value), key=lambda item: list(item.absolute_path))
    if errors:
        details = "; ".join(f"{'.'.join(map(str, error.absolute_path)) or '<root>'}: {error.message}" for error in errors)
        raise ValueError(f"invalid {name}: {details}")
