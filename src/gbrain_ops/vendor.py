from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PatchManifest:
    upstream: str
    branch: str
    base_commit: str
    patches: tuple[str, ...]


def load_manifest(path: Path) -> PatchManifest:
    raw = json.loads(path.read_text(encoding="utf-8"))
    base = raw.get("base_commit")
    if not isinstance(base, str) or len(base) < 7:
        raise ValueError("manifest base_commit must be pinned")
    patches = raw.get("patches")
    if not isinstance(patches, list) or not all(isinstance(item, str) for item in patches):
        raise ValueError("manifest patches must be a string array")
    return PatchManifest(
        upstream=str(raw["upstream"]),
        branch=str(raw.get("branch", "master")),
        base_commit=base,
        patches=tuple(patches),
    )


def _run(args: list[str], cwd: Path) -> str:
    result = subprocess.run(args, cwd=cwd, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "no diagnostic output")[-2000:]
        raise RuntimeError(f"{args[0]} failed with exit code {result.returncode}: {detail}")
    return result.stdout.strip()


def apply_patch_series(vendor_repo: Path, manifest_path: Path, output: Path) -> dict[str, object]:
    manifest = load_manifest(manifest_path)
    if _run(["git", "status", "--porcelain"], vendor_repo):
        raise RuntimeError("vendor checkout must be clean before promotion")
    if output.exists():
        raise RuntimeError(f"output already exists: {output}")
    _run(["git", "fetch", "origin", "--prune"], vendor_repo)
    _run(["git", "cat-file", "-e", f"{manifest.base_commit}^{{commit}}"], vendor_repo)
    _run(["git", "worktree", "add", "--detach", str(output), manifest.base_commit], vendor_repo)
    try:
        for relative in manifest.patches:
            patch = (manifest_path.parent / relative).resolve()
            if not patch.is_file() or manifest_path.parent.resolve() not in patch.parents:
                raise RuntimeError(f"invalid patch path: {relative}")
            _run(
                [
                    "git",
                    "-c",
                    "user.name=gbrain-ops",
                    "-c",
                    "user.email=gbrain-ops@users.noreply.github.com",
                    "am",
                    "--3way",
                    str(patch),
                ],
                output,
            )
        head = _run(["git", "rev-parse", "HEAD"], output)
        diff = _run(["git", "status", "--porcelain"], output)
        if diff:
            raise RuntimeError("promoted worktree is dirty")
        receipt = {
            "schema": "gbrain-ops-vendor-promotion/v1",
            "base_commit": manifest.base_commit,
            "head_commit": head,
            "patches": [
                {
                    "path": relative,
                    "sha256": hashlib.sha256((manifest_path.parent / relative).read_bytes()).hexdigest(),
                }
                for relative in manifest.patches
            ],
        }
        receipt_path = output.parent / f"{output.name}.promotion.json"
        receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
        return receipt
    except Exception:
        subprocess.run(["git", "am", "--abort"], cwd=output, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        raise
