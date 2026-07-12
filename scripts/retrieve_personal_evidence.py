#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date
from pathlib import Path

from gbrain_ops.evidence_retrieval import PersonalEvidenceRetriever, load_relationship
from gbrain_ops.owner_client import OwnerClient, OwnerCredentials


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Retrieve provenance-first personal conversation evidence through GBrain owner")
    value.add_argument("--credentials", type=Path, required=True)
    value.add_argument("--relationships", type=Path, required=True)
    value.add_argument("--relationship", required=True)
    value.add_argument("--start", type=date.fromisoformat, required=True)
    value.add_argument("--end", type=date.fromisoformat, required=True)
    value.add_argument("--candidate-query", action="append", required=True)
    value.add_argument("--candidate-limit", type=int, default=50)
    value.add_argument("--summary-only", action="store_true")
    return value


def privacy_safe_summary(payload: dict[str, object]) -> dict[str, object]:
    queries = payload.pop("candidate_queries", [])
    payload["candidate_query_count"] = len(queries) if isinstance(queries, list) else 0
    for page in payload["window_evidence"]:
        page.pop("sections", None)
    if payload["prior_similar"] is not None:
        payload["prior_similar"].pop("sections", None)
    return payload


async def run(args: argparse.Namespace) -> dict[str, object]:
    relationship = load_relationship(args.relationships, args.relationship)
    client = OwnerClient(OwnerCredentials.from_file(args.credentials))
    packet = await PersonalEvidenceRetriever(client, relationship).retrieve(
        start=args.start,
        end=args.end,
        candidate_queries=args.candidate_query,
        candidate_limit=args.candidate_limit,
    )
    payload = packet.as_dict()
    if args.summary_only:
        payload = privacy_safe_summary(payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.candidate_limit <= 0:
        raise SystemExit("--candidate-limit must be positive")
    payload = asyncio.run(run(args))
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload.get("answerable") is True else 3


if __name__ == "__main__":
    raise SystemExit(main())
