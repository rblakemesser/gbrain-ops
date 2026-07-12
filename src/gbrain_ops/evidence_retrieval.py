from __future__ import annotations

import asyncio
import json
import re
import stat
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from .owner_client import OwnerClient, OwnerClientError, OwnerResourceNotFoundError

RELATIONSHIP_SCHEMA = "gbrain-ops-relationships/v1"
EVIDENCE_SCHEMA = "gbrain-ops-personal-evidence/v1"
_PRIVATE_HANDLE = re.compile(
    r"(?:\+?1[-.\s]?)?(?:\(\d{3}\)|\d{3})[-.\s]?\d{3}[-.\s]?\d{4}|"
    r"\+\d{3}\*{4}\d{4}|[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}",
    re.IGNORECASE,
)
_CONTENT_HASH = re.compile(r"^[a-f0-9]{64}$", re.IGNORECASE)
_DATE_SLUG = re.compile(r"/(\d{4}-\d{2}-\d{2})$")

DEFAULT_CONCEPT_GROUPS: dict[str, tuple[str, ...]] = {
    "travel": (
        "travel",
        "trip",
        "away",
        "dates",
        "calendar",
        "schedule",
        "airplane",
        "convention",
        "out of town",
    ),
    "care": (
        "leo",
        "kids",
        "childcare",
        "car seat",
        "pick them up",
        "pick up",
        "watch",
        "coverage",
        "time away",
    ),
}


@dataclass(frozen=True)
class Relationship:
    alias: str
    display: str
    source_id: str
    markers: tuple[str, ...]


@dataclass(frozen=True)
class EvidencePage:
    source_id: str
    slug: str
    effective_date: str
    content_hash: str
    sections: tuple[str, ...]
    concept_groups: tuple[str, ...] = ()
    retrieval_score: float | None = None


@dataclass(frozen=True)
class EvidencePacket:
    schema: str
    relationship: str
    source_id: str
    start_date: str
    end_date: str
    brain_id: str
    window_evidence: tuple[EvidencePage, ...]
    prior_similar: EvidencePage | None
    candidate_queries: tuple[str, ...]
    candidate_count: int
    gaps: tuple[str, ...]
    disposition: str
    answerable: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class EvidenceRetrievalError(RuntimeError):
    """Secret-free failure while constructing a personal evidence packet."""


def load_relationship(path: Path, alias: str) -> Relationship:
    try:
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise EvidenceRetrievalError("relationship file must be mode 0600")
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("schema") != RELATIONSHIP_SCHEMA:
            raise EvidenceRetrievalError("relationship file schema is invalid")
        aliases = raw.get("aliases")
        value = aliases.get(alias.casefold()) if isinstance(aliases, dict) else None
        if not isinstance(value, dict):
            raise EvidenceRetrievalError("relationship alias is not configured")
        markers = tuple(str(item) for item in value.get("markers", ()) if str(item))
        relationship = Relationship(
            alias=alias.casefold(),
            display=str(value.get("display", "Relationship")),
            source_id=str(value.get("source_id", "")),
            markers=markers,
        )
        if not relationship.source_id or not relationship.markers:
            raise EvidenceRetrievalError("relationship alias is incomplete")
        return relationship
    except EvidenceRetrievalError:
        raise
    except Exception as exc:
        raise EvidenceRetrievalError("relationship file is invalid") from exc


def split_markdown_sections(content: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in re.split(r"(?m)(?=^## )", content) if part.strip().startswith("## "))


def relationship_sections(content: str, relationship: Relationship) -> tuple[str, ...]:
    selected = []
    expected = {f"## {marker}" for marker in relationship.markers}
    for section in split_markdown_sections(content):
        heading = section.splitlines()[0].strip()
        if heading in expected:
            selected.append(_privacy_safe(section, relationship))
    return tuple(selected)


def _privacy_safe(content: str, relationship: Relationship) -> str:
    for marker in relationship.markers:
        content = content.replace(marker, relationship.display)
    content = _PRIVATE_HANDLE.sub("Family member", content)
    content = re.sub(r"(?m)^\s*-\s*Attachment:.*(?:\n|$)", "", content)
    content = re.sub(r"(?mi)^\s*-?.*\[redacted:\s*likely verification/security code\].*(?:\n|$)", "", content)
    content = re.sub(r"(?m)^## (?:chat\d+|[a-f0-9]{32})$", "## Family conversation", content)
    return content.strip()


def slug_date(slug: str) -> date | None:
    match = _DATE_SLUG.search(slug)
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def concept_matches(content: str, groups: Mapping[str, Sequence[str]]) -> tuple[str, ...]:
    def matches(term: str) -> bool:
        pieces = [re.escape(piece) for piece in term.casefold().split()]
        pattern = r"(?<!\w)" + r"\s+".join(pieces) + r"(?!\w)"
        return re.search(pattern, content.casefold()) is not None

    return tuple(name for name, terms in groups.items() if any(matches(term) for term in terms))


def _rows(value: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    for key in ("results", "items", "pages"):
        candidate = value.get(key)
        if isinstance(candidate, list):
            return [row for row in candidate if isinstance(row, dict)]
    return []


class PersonalEvidenceRetriever:
    def __init__(self, client: OwnerClient, relationship: Relationship) -> None:
        if client.credentials.source_id != relationship.source_id:
            raise EvidenceRetrievalError("relationship source does not match owner client")
        self.client = client
        self.relationship = relationship

    async def _page(self, slug: str, *, score: float | None = None) -> EvidencePage | None:
        try:
            page = await self.client.get_page(slug)
        except OwnerResourceNotFoundError:
            return None
        except OwnerClientError as exc:
            raise EvidenceRetrievalError("owner page retrieval failed") from exc
        if page.get("error") == "page_not_found":
            return None
        if str(page.get("source_id", "")) != self.relationship.source_id or str(page.get("slug", "")) != slug:
            raise EvidenceRetrievalError("canonical evidence identity mismatch")
        content_hash = str(page.get("content_hash", ""))
        if _CONTENT_HASH.fullmatch(content_hash) is None:
            raise EvidenceRetrievalError("canonical evidence hash is missing or invalid")
        content = str(page.get("compiled_truth", ""))
        sections = relationship_sections(content, self.relationship)
        if not sections:
            return None
        effective = slug_date(slug)
        return EvidencePage(
            source_id=self.relationship.source_id,
            slug=slug,
            effective_date=effective.isoformat() if effective else "",
            content_hash=content_hash,
            sections=sections,
            retrieval_score=score,
        )

    async def retrieve(
        self,
        *,
        start: date,
        end: date,
        candidate_queries: Sequence[str],
        concept_groups: Mapping[str, Sequence[str]] = DEFAULT_CONCEPT_GROUPS,
        required_groups: Sequence[str] = ("travel", "care"),
        candidate_limit: int = 50,
    ) -> EvidencePacket:
        if end < start:
            raise EvidenceRetrievalError("date window is invalid")
        queries = tuple(dict.fromkeys(query.strip() for query in candidate_queries if query.strip()))
        if not queries:
            raise EvidenceRetrievalError("at least one candidate query is required")

        window_slugs = []
        cursor = start
        while cursor <= end:
            window_slugs.append(f"{self.relationship.source_id}/{cursor.year:04d}/{cursor.isoformat()}")
            cursor += timedelta(days=1)
        window_results = await asyncio.gather(*(self._page(slug) for slug in window_slugs))
        window_evidence = tuple(page for page in window_results if page is not None)

        query_results = await asyncio.gather(
            *(self.client.call_tool("query", {"query": query, "limit": candidate_limit}) for query in queries)
        )
        candidates: dict[str, float] = {}
        for result in query_results:
            for row in _rows(result):
                slug = str(row.get("slug", ""))
                observed = slug_date(slug)
                if not slug or observed is None or observed >= start:
                    continue
                score = float(row.get("score", 0.0) or 0.0)
                candidates[slug] = max(score, candidates.get(slug, 0.0))

        prior: EvidencePage | None = None
        for slug in sorted(candidates, key=lambda value: (slug_date(value) or date.min, value), reverse=True):
            page = await self._page(slug, score=candidates[slug])
            if page is None:
                continue
            groups = concept_matches("\n".join(page.sections), concept_groups)
            if not all(required in groups for required in required_groups):
                continue
            prior = EvidencePage(
                source_id=page.source_id,
                slug=page.slug,
                effective_date=page.effective_date,
                content_hash=page.content_hash,
                sections=page.sections,
                concept_groups=groups,
                retrieval_score=page.retrieval_score,
            )
            break

        identity = await self.client.get_brain_identity()
        brain_id = str(identity.get("brain_id", ""))
        if not brain_id:
            raise EvidenceRetrievalError("owner brain identity is missing")
        gaps: list[str] = []
        expected_days = (end - start).days + 1
        if len(window_evidence) != expected_days:
            gaps.append("relationship evidence is missing for one or more requested dates")
        if prior is None:
            gaps.append("no earlier relationship-and-topic-qualified conversation was found")
        return EvidencePacket(
            schema=EVIDENCE_SCHEMA,
            relationship=self.relationship.display,
            source_id=self.relationship.source_id,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            brain_id=brain_id,
            window_evidence=window_evidence,
            prior_similar=prior,
            candidate_queries=queries,
            candidate_count=len(candidates),
            gaps=tuple(gaps),
            disposition="answerable" if not gaps else "abstain",
            answerable=not gaps,
        )
