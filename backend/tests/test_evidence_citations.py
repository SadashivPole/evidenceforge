from __future__ import annotations

import uuid

import pytest

from app.evidence.citations.types import EvidenceCitation


def _make_citation(
    *,
    section_label: str | None = "Access Control",
    page_number: int | None = 3,
) -> EvidenceCitation:
    return EvidenceCitation(
        workspace_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        document_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
        version_id=uuid.UUID("00000000-0000-0000-0000-000000000003"),
        version_number=2,
        chunk_id=uuid.UUID("00000000-0000-0000-0000-000000000004"),
        chunk_index=7,
        content_hash="a" * 64,
        normalized_start_byte=1024,
        normalized_end_byte=2048,
        section_label=section_label,
        page_number=page_number,
    )


def test_citation_preserves_immutable_provenance() -> None:
    citation = _make_citation()

    assert citation.workspace_id == uuid.UUID("00000000-0000-0000-0000-000000000001")
    assert citation.document_id == uuid.UUID("00000000-0000-0000-0000-000000000002")
    assert citation.version_id == uuid.UUID("00000000-0000-0000-0000-000000000003")
    assert citation.version_number == 2
    assert citation.chunk_id == uuid.UUID("00000000-0000-0000-0000-000000000004")
    assert citation.chunk_index == 7
    assert citation.content_hash == "a" * 64
    assert citation.normalized_start_byte == 1024
    assert citation.normalized_end_byte == 2048
    assert citation.section_label == "Access Control"
    assert citation.page_number == 3


def test_citation_is_immutable() -> None:
    citation = _make_citation()

    with pytest.raises(AttributeError):
        citation.chunk_index = 99  # type: ignore[misc]


def test_citation_allows_missing_optional_location_fields() -> None:
    citation = _make_citation(
        section_label=None,
        page_number=None,
    )

    assert citation.section_label is None
    assert citation.page_number is None


def test_citation_is_hashable() -> None:
    citation = _make_citation()

    assert hash(citation) == hash(citation)


def test_citation_equality_is_value_based() -> None:
    first = _make_citation()
    second = _make_citation()

    assert first == second
