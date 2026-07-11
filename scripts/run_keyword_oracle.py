#!/usr/bin/env python3
"""Run the isolated GBrain unique-nonce keyword oracle.

The oracle uses a fresh GBRAIN_HOME, deterministic synthetic fixtures, and
keyword-only CLI mode. It never opens or mutates the configured personal brain.
Artifacts are written outside the repository unless --artifact-dir says otherwise.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ORACLE_VERSION = "gbrain-keyword-oracle-v1"
SOURCE_IDS = ("oracle-a", "oracle-b", "oracle-c")
FIXTURE_COUNT = 30
CLI_TIMEOUT_SECONDS = 4

RAW_SQL_JS = r"""
import { PGlite } from '@electric-sql/pglite';
import { vector } from '@electric-sql/pglite/vector';
import { pg_trgm } from '@electric-sql/pglite/contrib/pg_trgm';
const [path, query, source] = process.argv.slice(1);
const db = await PGlite.create({ dataDir: path, extensions: { vector, pg_trgm } });
const result = await db.query(
  `SELECT p.slug, p.source_id, cc.chunk_text
     FROM content_chunks cc
     JOIN pages p ON p.id = cc.page_id
     JOIN sources s ON s.id = p.source_id
    WHERE cc.search_vector @@ websearch_to_tsquery('english', $1)
      AND p.source_id = $2
      AND p.deleted_at IS NULL
      AND cc.modality = 'text'
      AND s.archived_at IS NULL
    ORDER BY p.slug, cc.chunk_index`,
  [query, source],
);
console.log(JSON.stringify(result.rows));
await db.close();
"""

HOLDER_JS = r"""
import { PGLiteEngine } from './src/core/pglite-engine.ts';
const [path, readyPath, stopPath] = process.argv.slice(1);
const engine = new PGLiteEngine();
await engine.connect({ engine: 'pglite', database_path: path });
await engine.setConfig('oracle.controlled_writer', new Date().toISOString());
await Bun.write(readyPath, 'ready\n');
const deadline = Date.now() + 45_000;
while (Date.now() < deadline && !(await Bun.file(stopPath).exists())) {
  await Bun.sleep(50);
}
await engine.disconnect();
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def token_for(index: int) -> str:
    digest = hashlib.sha256(f"{ORACLE_VERSION}:{index}".encode()).hexdigest()[:20]
    letters = "abcdefghijklmnop"
    return "oraclex" + "".join(letters[int(ch, 16)] for ch in digest)


def build_manifest() -> dict[str, Any]:
    fixtures: list[dict[str, Any]] = []
    for index in range(FIXTURE_COUNT):
        source_id = SOURCE_IDS[index % len(SOURCE_IDS)]
        source_index = index // len(SOURCE_IDS)
        fixtures.append(
            {
                "index": index,
                "source_id": source_id,
                "slug": f"collision-{source_index:02d}",
                "token": token_for(index),
                "collision_token": f"oraclecollisionword{source_index:02d}",
                "negative_source_id": SOURCE_IDS[(index + 1) % len(SOURCE_IDS)],
                "tags": ["oracle", source_id, f"bucket-{source_index:02d}"],
            }
        )
    return {
        "oracle_version": ORACLE_VERSION,
        "fixture_count": FIXTURE_COUNT,
        "sources": list(SOURCE_IDS),
        "decision_rules": {
            "any_idle_miss": "FAIL",
            "any_idle_timeout": "FAIL",
            "any_cross_source_result": "FAIL",
            "any_cold_warm_disagreement": "FAIL",
            "idle_pass_writer_fail": "ownership_or_isolation_defect",
            "idle_raw_pass_cli_fail": "query_adapter_or_filter_defect",
            "idle_raw_fail_with_chunks": "schema_index_or_migration_defect",
            "all_idle_and_writer_pass": "current_corpus_or_replacement_state_primary",
        },
        "fixtures": fixtures,
    }


def write_fixtures(work_dir: Path, manifest: dict[str, Any]) -> dict[str, Path]:
    source_dirs = {source_id: work_dir / "sources" / source_id for source_id in SOURCE_IDS}
    for path in source_dirs.values():
        path.mkdir(parents=True, exist_ok=False)
    for item in manifest["fixtures"]:
        path = source_dirs[item["source_id"]] / f"{item['slug']}.md"
        tags = ", ".join(item["tags"])
        path.write_text(
            "---\n"
            f"title: Oracle fixture {item['index']:02d}\n"
            "type: note\n"
            f"tags: [{tags}]\n"
            "---\n\n"
            f"Unique nonce {item['token']} belongs only to {item['source_id']}.\n\n"
            f"Collision marker {item['collision_token']} is intentionally shared by slug across sources.\n",
            encoding="utf-8",
        )
    return source_dirs


def run_command(
    argv: list[str],
    *,
    env: dict[str, str],
    cwd: Path | None = None,
    timeout: float = 90,
) -> dict[str, Any]:
    started = time.monotonic()
    proc = subprocess.Popen(
        argv,
        env=env,
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return {
            "argv": argv,
            "exit_code": proc.returncode,
            "timed_out": False,
            "duration_ms": round((time.monotonic() - started) * 1000),
            "stdout": stdout,
            "stderr": stderr,
        }
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            stdout, stderr = proc.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            stdout, stderr = proc.communicate()
        return {
            "argv": argv,
            "exit_code": proc.returncode,
            "timed_out": True,
            "duration_ms": round((time.monotonic() - started) * 1000),
            "stdout": stdout,
            "stderr": stderr,
        }


def parse_cli_slugs(stdout: str) -> list[str]:
    slugs: list[str] = []
    for line in stdout.splitlines():
        match = re.match(r"^\[[^]]+\]\s+([^\s]+)\s+--", line)
        if match:
            slugs.append(match.group(1))
    return slugs


def raw_probe(
    *,
    bun: Path,
    runtime: Path,
    db_path: Path,
    query: str,
    source_id: str,
    env: dict[str, str],
) -> dict[str, Any]:
    result = run_command(
        [str(bun), "-e", RAW_SQL_JS, str(db_path), query, source_id],
        env=env,
        cwd=runtime,
        timeout=15,
    )
    rows: list[dict[str, Any]] = []
    if result["exit_code"] == 0:
        try:
            rows = json.loads(result["stdout"])
        except json.JSONDecodeError:
            pass
    return {**result, "rows": rows}


def cli_probe(
    *,
    gbrain: Path,
    token: str,
    source_id: str,
    env: dict[str, str],
    timeout: float = CLI_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    result = run_command(
        [str(gbrain), "search", token, "--source", source_id, "--limit", "5"],
        env=env,
        timeout=timeout,
    )
    return {**result, "slugs": parse_cli_slugs(result["stdout"])}


def summarize_idle(manifest: dict[str, Any], phases: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    failures: list[dict[str, Any]] = []
    by_key = {(item["index"], item["probe"]): item for values in phases.values() for item in values}
    for fixture in manifest["fixtures"]:
        expected_slug = fixture["slug"]
        for probe in ("raw_cold_positive", "raw_warm_positive", "cli_cold_positive", "cli_warm_positive"):
            item = by_key[(fixture["index"], probe)]
            actual = item.get("slugs") if probe.startswith("cli") else [row["slug"] for row in item.get("rows", [])]
            if item["timed_out"] or item["exit_code"] != 0 or actual != [expected_slug]:
                failures.append({"index": fixture["index"], "probe": probe, "expected": [expected_slug], "actual": actual, "timed_out": item["timed_out"], "exit_code": item["exit_code"]})
        for probe in ("raw_cold_negative", "raw_warm_negative", "cli_cold_negative", "cli_warm_negative"):
            item = by_key[(fixture["index"], probe)]
            actual = item.get("slugs") if probe.startswith("cli") else [row["slug"] for row in item.get("rows", [])]
            if item["timed_out"] or item["exit_code"] != 0 or actual:
                failures.append({"index": fixture["index"], "probe": probe, "expected": [], "actual": actual, "timed_out": item["timed_out"], "exit_code": item["exit_code"]})
    return {"passed": not failures, "failure_count": len(failures), "failures": failures}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gbrain", type=Path, required=True)
    parser.add_argument("--bun", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    args = parser.parse_args()

    for label, path in (("gbrain", args.gbrain), ("bun", args.bun), ("runtime", args.runtime)):
        if not path.exists():
            parser.error(f"{label} path does not exist: {path}")

    artifact_dir = args.artifact_dir.expanduser().resolve()
    artifact_dir.mkdir(parents=True, exist_ok=False)
    work_dir = artifact_dir / "work"
    work_dir.mkdir()
    gbrain_home = work_dir / "home"
    db_path = gbrain_home / ".gbrain" / "brain.pglite"
    manifest = build_manifest()
    manifest_path = artifact_dir / "fixture-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    source_dirs = write_fixtures(work_dir, manifest)

    env = os.environ.copy()
    env["GBRAIN_HOME"] = str(gbrain_home)
    env.pop("GBRAIN_SOURCE", None)
    setup: list[dict[str, Any]] = []
    setup.append(run_command([str(args.gbrain), "init", "--pglite", "--no-embedding", "--non-interactive", "--json"], env=env, timeout=120))
    for source_id, source_dir in source_dirs.items():
        setup.append(run_command([str(args.gbrain), "sources", "add", source_id, "--path", str(source_dir)], env=env))
        setup.append(run_command([str(args.gbrain), "import", str(source_dir), "--no-embed", "--source-id", source_id, "--json"], env=env, timeout=120))
    setup.append(run_command([str(args.gbrain), "config", "set", "search.mcp_keyword_only", "true"], env=env))
    setup.append(run_command([str(args.gbrain), "config", "get", "search.mcp_keyword_only"], env=env))
    setup.append(run_command([str(args.gbrain), "stats"], env=env))
    if any(item["exit_code"] != 0 or item["timed_out"] for item in setup):
        receipt = {"oracle_version": ORACLE_VERSION, "status": "setup_failed", "started_at": utc_now(), "setup": setup}
        (artifact_dir / "receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
        print(json.dumps(receipt, indent=2))
        return 1

    phases: dict[str, list[dict[str, Any]]] = {key: [] for key in (
        "raw_cold", "raw_warm", "cli_cold", "cli_warm", "writer_cli", "post_writer_cli"
    )}

    # Cold raw SQL: a fresh database process per positive and negative query.
    for fixture in manifest["fixtures"]:
        for suffix, source_id in (("positive", fixture["source_id"]), ("negative", fixture["negative_source_id"])):
            result = raw_probe(bun=args.bun, runtime=args.runtime, db_path=db_path, query=fixture["token"], source_id=source_id, env=env)
            phases["raw_cold"].append({"index": fixture["index"], "probe": f"raw_cold_{suffix}", **result})

    # Warm raw SQL contract: repeat immediately. PGlite still reopens per helper,
    # while the filesystem and OS caches are warm; this also checks close/reopen determinism.
    for fixture in manifest["fixtures"]:
        for suffix, source_id in (("positive", fixture["source_id"]), ("negative", fixture["negative_source_id"])):
            result = raw_probe(bun=args.bun, runtime=args.runtime, db_path=db_path, query=fixture["token"], source_id=source_id, env=env)
            phases["raw_warm"].append({"index": fixture["index"], "probe": f"raw_warm_{suffix}", **result})

    for wave in ("cold", "warm"):
        for fixture in manifest["fixtures"]:
            for suffix, source_id in (("positive", fixture["source_id"]), ("negative", fixture["negative_source_id"])):
                result = cli_probe(gbrain=args.gbrain, token=fixture["token"], source_id=source_id, env=env, timeout=15)
                phases[f"cli_{wave}"].append({"index": fixture["index"], "probe": f"cli_{wave}_{suffix}", **result})

    idle = summarize_idle(manifest, phases)

    # Controlled-writer wave. The owner acquires GBrain's lock, performs one
    # harmless config write, and holds the database. All CLI probes run in
    # parallel with hard deadlines so the test is bounded.
    ready_path = work_dir / "holder.ready"
    stop_path = work_dir / "holder.stop"
    holder = subprocess.Popen(
        [str(args.bun), "-e", HOLDER_JS, str(db_path), str(ready_path), str(stop_path)],
        env=env,
        cwd=str(args.runtime),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    holder_ready = False
    holder_started = time.monotonic()
    while time.monotonic() - holder_started < 10:
        if ready_path.exists():
            holder_ready = True
            break
        if holder.poll() is not None:
            break
        time.sleep(0.05)

    if holder_ready:
        def writer_probe(fixture: dict[str, Any]) -> dict[str, Any]:
            result = cli_probe(gbrain=args.gbrain, token=fixture["token"], source_id=fixture["source_id"], env=env)
            return {"index": fixture["index"], "probe": "writer_cli_positive", **result}

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            phases["writer_cli"] = list(pool.map(writer_probe, manifest["fixtures"]))
    stop_path.write_text("stop\n")
    try:
        holder_stdout, holder_stderr = holder.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(holder.pid, signal.SIGTERM)
        try:
            holder_stdout, holder_stderr = holder.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(holder.pid, signal.SIGKILL)
            holder_stdout, holder_stderr = holder.communicate()

    for fixture in manifest["fixtures"][:3]:
        result = cli_probe(gbrain=args.gbrain, token=fixture["token"], source_id=fixture["source_id"], env=env, timeout=15)
        phases["post_writer_cli"].append({"index": fixture["index"], "probe": "post_writer_cli_positive", **result})

    writer_failures = [
        item for item in phases["writer_cli"]
        if item["timed_out"] or item["exit_code"] != 0 or item.get("slugs") != [manifest["fixtures"][item["index"]]["slug"]]
    ]
    post_writer_passed = all(
        not item["timed_out"] and item["exit_code"] == 0 and item.get("slugs") == [manifest["fixtures"][item["index"]]["slug"]]
        for item in phases["post_writer_cli"]
    )

    if not idle["passed"]:
        classification = "idle_retrieval_defect"
        status = "failed"
    elif not holder_ready or holder.returncode != 0:
        classification = "controlled_writer_fixture_failed"
        status = "invalid"
    elif writer_failures and post_writer_passed:
        classification = "ownership_or_isolation_defect"
        status = "decision_reached"
    elif not writer_failures:
        classification = "current_corpus_or_replacement_state_primary"
        status = "decision_reached"
    else:
        classification = "inconclusive"
        status = "failed"

    receipt = {
        "oracle_version": ORACLE_VERSION,
        "status": status,
        "classification": classification,
        "started_at": datetime.fromtimestamp(artifact_dir.stat().st_ctime, timezone.utc).isoformat().replace("+00:00", "Z"),
        "completed_at": utc_now(),
        "fixture_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "fixture_count": FIXTURE_COUNT,
        "keyword_only_config_verified": setup[-2]["exit_code"] == 0 and "true" in setup[-2]["stdout"].lower(),
        "idle": idle,
        "controlled_writer": {
            "ready": holder_ready,
            "holder_exit_code": holder.returncode,
            "holder_stdout": holder_stdout,
            "holder_stderr": holder_stderr,
            "probe_count": len(phases["writer_cli"]),
            "failure_count": len(writer_failures),
            "timed_out_count": sum(1 for item in writer_failures if item["timed_out"]),
            "post_writer_recovery_passed": post_writer_passed,
        },
        "setup": setup,
        "phases": phases,
    }
    receipt_path = artifact_dir / "receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    summary = {
        "status": status,
        "classification": classification,
        "artifact_dir": str(artifact_dir),
        "fixture_count": FIXTURE_COUNT,
        "idle_passed": idle["passed"],
        "idle_failure_count": idle["failure_count"],
        "writer_probe_count": len(phases["writer_cli"]),
        "writer_failure_count": len(writer_failures),
        "writer_timeout_count": receipt["controlled_writer"]["timed_out_count"],
        "post_writer_recovery_passed": post_writer_passed,
        "receipt": str(receipt_path),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if status == "decision_reached" else 1


if __name__ == "__main__":
    raise SystemExit(main())
