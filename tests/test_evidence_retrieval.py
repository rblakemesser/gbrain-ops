from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from gbrain_ops.owner_client import OwnerClientError, OwnerResourceNotFoundError
import scripts.retrieve_personal_evidence as retrieval_cli
from scripts.retrieve_personal_evidence import privacy_safe_summary

from gbrain_ops.evidence_retrieval import (
    EvidenceRetrievalError,
    PersonalEvidenceRetriever,
    Relationship,
    concept_matches,
    load_relationship,
    relationship_sections,
    slug_date,
)


MARKER = "+171****0683"


class FakeClient:
    def __init__(
        self,
        pages: dict[str, str | dict[str, object]],
        query_rows: list[dict[str, object]],
        *,
        owner_failure: bool = False,
    ) -> None:
        self.credentials = SimpleNamespace(source_id="messages")
        self.pages = pages
        self.query_rows = query_rows
        self.owner_failure = owner_failure

    async def get_page(self, slug: str) -> dict[str, object]:
        if self.owner_failure:
            raise OwnerClientError("secret upstream failure")
        if slug not in self.pages:
            return {"error": "page_not_found"}
        value = self.pages[slug]
        if isinstance(value, dict):
            return value
        return {
            "source_id": "messages",
            "slug": slug,
            "content_hash": "c" * 64,
            "compiled_truth": value,
        }

    async def call_tool(self, name: str, arguments: dict[str, object]) -> list[dict[str, object]]:
        assert name == "query"
        return self.query_rows

    async def get_brain_identity(self) -> dict[str, object]:
        return {"brain_id": "brain-fixture"}


def relation() -> Relationship:
    return Relationship(alias="my mom", display="Mom", source_id="messages", markers=(MARKER,))


def page(body: str) -> str:
    return f"# Daily messages\n\n## {MARKER}\n\n{body}\n\n## +140****1234\n\nUnrelated text"


def test_relationship_mapping_requires_private_file_and_valid_alias(tmp_path: Path) -> None:
    path = tmp_path / "relationships.json"
    path.write_text(
        json.dumps(
            {
                "schema": "gbrain-ops-relationships/v1",
                "aliases": {"my mom": {"display": "Mom", "source_id": "messages", "markers": [MARKER]}},
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o644)
    with pytest.raises(EvidenceRetrievalError, match="0600"):
        load_relationship(path, "my mom")
    path.chmod(0o600)
    assert load_relationship(path, "MY MOM").markers == (MARKER,)
    with pytest.raises(EvidenceRetrievalError, match="not configured"):
        load_relationship(path, "my dad")


def test_relationship_sections_are_filtered_and_privacy_safe() -> None:
    selected = relationship_sections(page("Mom text with +140****9999"), relation())
    assert len(selected) == 1
    assert MARKER not in selected[0]
    assert "+140****9999" not in selected[0]
    assert "Mom" in selected[0]
    assert "Family member" in selected[0]


def test_slug_dates_and_concept_groups() -> None:
    assert slug_date("messages/2026/2026-07-08") == date(2026, 7, 8)
    assert slug_date("bad") is None
    assert concept_matches("Travel dates and a car seat for Leo", {"travel": ("travel",), "care": ("car seat",)}) == (
        "travel",
        "care",
    )


def test_retrieval_uses_exact_window_and_latest_qualified_prior() -> None:
    pages = {
        "messages/2026/2026-07-10": page("We coordinated kids pickup and dinner."),
        "messages/2026/2026-07-11": page("We coordinated the family travel calendar."),
        "messages/2026/2026-07-09": page("We discussed a recipe."),
        "messages/2026/2026-07-08": page("Travel dates overlapped with care for Leo, kids pickup, and a car seat."),
        "messages/2026/2026-07-04": page("A trip with Leo was planned."),
    }
    rows = [
        {"slug": "messages/2026/2026-07-04", "score": 0.95},
        {"slug": "messages/2026/2026-07-08", "score": 0.75},
        {"slug": "messages/2026/2026-07-09", "score": 0.99},
        {"slug": "messages/2026/2026-07-11", "score": 1.0},
    ]
    packet = asyncio.run(
        PersonalEvidenceRetriever(FakeClient(pages, rows), relation()).retrieve(
            start=date(2026, 7, 10),
            end=date(2026, 7, 11),
            candidate_queries=("childcare travel dates", "away from Leo dates"),
        )
    )
    assert [item.effective_date for item in packet.window_evidence] == ["2026-07-10", "2026-07-11"]
    assert packet.prior_similar is not None
    assert packet.prior_similar.effective_date == "2026-07-08"
    assert packet.prior_similar.concept_groups == ("travel", "care")
    assert packet.gaps == ()
    assert packet.answerable is True
    assert packet.disposition == "answerable"
    assert packet.brain_id == "brain-fixture"


def test_retrieval_reports_missing_evidence_instead_of_inventing() -> None:
    packet = asyncio.run(
        PersonalEvidenceRetriever(FakeClient({}, []), relation()).retrieve(
            start=date(2026, 7, 10),
            end=date(2026, 7, 11),
            candidate_queries=("childcare travel dates",),
        )
    )
    assert packet.window_evidence == ()
    assert packet.prior_similar is None
    assert len(packet.gaps) == 2
    assert packet.answerable is False
    assert packet.disposition == "abstain"


def test_relationship_identity_comes_only_from_section_heading() -> None:
    injected = f"# Daily\n\n## +140****1234\n\nForwarded marker {MARKER} with travel and childcare."
    assert relationship_sections(injected, relation()) == ()


def test_privacy_filter_removes_common_pii_and_sensitive_lines() -> None:
    body = "\n".join(
        (
            "Call " + "(404) " + "555-1212 or " + "404-" + "555-3434 or " + "404 " + "555 5656.",
            "Email " + "private.person" + "@" + "example.test.",
            "    - Attachment: secret-photo.jpg",
            "  - [redacted: likely verification/security code] 123456",
        )
    )
    serialized = "\n".join(relationship_sections(page(body), relation()))
    for secret in ("404", "1212", "3434", "5656", "private.person", "secret-photo", "123456"):
        assert secret not in serialized


def test_concept_matching_rejects_substring_false_positives() -> None:
    adversarial = "giveaway updates stopwatch leopard"
    assert concept_matches(adversarial, {"travel": ("away", "dates"), "care": ("watch", "leo")}) == ()


def test_owner_failure_propagates_as_sanitized_retrieval_failure() -> None:
    with pytest.raises(EvidenceRetrievalError, match="owner page retrieval failed") as caught:
        asyncio.run(
            PersonalEvidenceRetriever(FakeClient({}, [], owner_failure=True), relation()).retrieve(
                start=date(2026, 7, 10),
                end=date(2026, 7, 11),
                candidate_queries=("childcare travel dates",),
            )
        )
    assert "secret upstream failure" not in str(caught.value)


def test_typed_owner_not_found_becomes_absent_evidence() -> None:
    client = FakeClient({}, [])

    async def missing(_slug: str) -> dict[str, object]:
        raise OwnerResourceNotFoundError("not found")

    client.get_page = missing  # type: ignore[method-assign]
    retriever = PersonalEvidenceRetriever(client, relation())
    assert asyncio.run(retriever._page("messages/2026/2026-07-10")) is None


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        ({"source_id": "gmail", "slug": "messages/2026/2026-07-10", "content_hash": "c" * 64}, "identity mismatch"),
        ({"source_id": "messages", "slug": "messages/2026/2026-07-10", "content_hash": "bad"}, "hash"),
        ({"source_id": "messages", "slug": "messages/2026/2026-07-10"}, "hash"),
    ),
)
def test_canonical_identity_and_hash_are_required(overrides: dict[str, object], message: str) -> None:
    value = {**overrides, "compiled_truth": page("Travel dates and childcare coverage.")}
    with pytest.raises(EvidenceRetrievalError, match=message):
        asyncio.run(
            PersonalEvidenceRetriever(FakeClient({"messages/2026/2026-07-10": value}, []), relation()).retrieve(
                start=date(2026, 7, 10),
                end=date(2026, 7, 10),
                candidate_queries=("travel childcare",),
            )
        )


def test_equal_date_candidates_use_stable_slug_tiebreak() -> None:
    slugs = ("messages/a/2026-07-08", "messages/z/2026-07-08")
    pages = {slug: page(f"Travel dates and childcare coverage for {slug}.") for slug in slugs}

    def selected(rows: list[dict[str, object]]) -> str:
        packet = asyncio.run(
            PersonalEvidenceRetriever(FakeClient(pages, rows), relation()).retrieve(
                start=date(2026, 7, 10),
                end=date(2026, 7, 10),
                candidate_queries=("travel childcare",),
            )
        )
        assert packet.prior_similar is not None
        return packet.prior_similar.slug

    forward = [{"slug": slug, "score": 0.5} for slug in slugs]
    assert selected(forward) == selected(list(reversed(forward))) == "messages/z/2026-07-08"


def test_summary_output_drops_queries_and_sections() -> None:
    secret = "private.person" + "@" + "example.test " + "+1404" + "5551212"
    payload: dict[str, object] = {
        "candidate_queries": [secret],
        "window_evidence": [{"sections": [secret], "slug": "messages/2026/2026-07-10"}],
        "prior_similar": {"sections": [secret], "slug": "messages/2026/2026-07-08"},
    }
    serialized = json.dumps(privacy_safe_summary(payload))
    assert secret not in serialized
    assert "candidate_queries" not in serialized
    assert '"candidate_query_count": 1' in serialized


@pytest.mark.parametrize(("answerable", "expected_exit"), ((True, 0), (False, 3)))
def test_cli_main_enforces_answerable_exit_contract(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    answerable: bool,
    expected_exit: int,
) -> None:
    async def fake_run(_args: object) -> dict[str, object]:
        return {
            "answerable": answerable,
            "disposition": "answerable" if answerable else "abstain",
            "gaps": [] if answerable else ["missing evidence"],
        }

    monkeypatch.setattr(retrieval_cli, "run", fake_run)
    exit_code = retrieval_cli.main(
        [
            "--credentials",
            "/private/owner.json",
            "--relationships",
            "/private/relationships.json",
            "--relationship",
            "my mom",
            "--start",
            "2026-07-10",
            "--end",
            "2026-07-11",
            "--candidate-query",
            "childcare travel coverage",
        ]
    )
    assert exit_code == expected_exit
    emitted = json.loads(capsys.readouterr().out)
    assert emitted["answerable"] is answerable
