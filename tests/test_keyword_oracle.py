from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_keyword_oracle.py"
SPEC = importlib.util.spec_from_file_location("run_keyword_oracle", SCRIPT)
assert SPEC and SPEC.loader
oracle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(oracle)


def test_manifest_is_deterministic_and_source_qualified() -> None:
    first = oracle.build_manifest()
    second = oracle.build_manifest()
    assert first == second
    assert first["fixture_count"] == 30
    assert len(first["fixtures"]) == 30
    assert len({item["token"] for item in first["fixtures"]}) == 30
    assert {item["source_id"] for item in first["fixtures"]} == set(oracle.SOURCE_IDS)
    assert all(item["negative_source_id"] != item["source_id"] for item in first["fixtures"])


def test_same_slugs_exist_across_sources_without_same_source_collision() -> None:
    manifest = oracle.build_manifest()
    pairs = [(item["source_id"], item["slug"]) for item in manifest["fixtures"]]
    assert len(pairs) == len(set(pairs))
    for index in range(10):
        slug = f"collision-{index:02d}"
        assert {item["source_id"] for item in manifest["fixtures"] if item["slug"] == slug} == set(oracle.SOURCE_IDS)


def test_cli_slug_parser_ignores_no_result_and_noise() -> None:
    output = "[0.2500] collision-00 -- Unique nonce oracle\nwarning\n[0.1000] collision-01 -- other\n"
    assert oracle.parse_cli_slugs(output) == ["collision-00", "collision-01"]
    assert oracle.parse_cli_slugs("No results.\n") == []
