from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path
from typing import Any, Mapping

from .contracts import validate_contract

ENV_PATTERN = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")


def _expand(value: Any, env: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in env:
                raise ValueError(f"required environment variable is unset: {name}")
            return env[name]
        result = ENV_PATTERN.sub(replace, value)
        if "${" in result:
            raise ValueError(f"invalid environment expansion: {value}")
        return result
    if isinstance(value, list):
        return [_expand(item, env) for item in value]
    if isinstance(value, dict):
        return {key: _expand(item, env) for key, item in value.items()}
    return value


def load_config(path: Path, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    config = _expand(raw, env if env is not None else os.environ)
    validate_contract("config", config)
    source_ids = [source["id"] for source in config["sources"]]
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("source ids must be unique")
    return config
