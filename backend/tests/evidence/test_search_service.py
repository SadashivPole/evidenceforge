from __future__ import annotations

import uuid

import pytest

from app.evidence.search.service import normalize_query, search_chunks
from app.evidence.search.types import SearchChunkCandidate


def make_candidate(
    *,
    document_id: str = "00000000-0000-0000-0000-000000000001",
    version_id: str = "00000000-0000-0000-0000-000000000011",
    chunk_id: str = "00000000-0000-0000-0000-000000000021",
    version_number: int = 1,
    chunk_index: int = 0,
    content: str = "Admin access requires MFA.",
    section_label: str | None = "Access Control",
) -> SearchChunkCandidate:
    return SearchChunkCandidate(
        chunk_id=uuid.UUID(chunk_id),
        document_id=uuid.UUID(document_id),
        version_id=uuid.UUID(version_id),
        version_number=version_number,
        chunk_index=chunk_index,
        content=content,
        content_hash="a" * 64,
        normalized_start_byte=0,
        normalized_end_byte=len(content.encode("utf-8")),
        section_label=section_label,
        page_number=None,
    )


def test_normalize_query_applies_nfkc_and_whitespace_normalization() -> None:
    assert normalize_query("  Ａdmin   access \n control  ") == "Admin access control"


def test_normalize_query_rejects_too_short_query() -> None:
    with pytest.raises(ValueError, match="too short"):
        normalize_query("a")


def test_normalize_query_rejects_too_long_query() -> None:
    with pytest.raises(ValueError, match="too long"):
        normalize_query("a" * 257)


def test_search_is_case_insensitive() -> None:
    candidate = make_candidate(content="ADMIN access requires MFA.")

    results = search_chunks((candidate,), "admin access")

    assert len(results) == 1
    assert results[0].exact_phrase_match is True
    assert results[0].matched_terms == ("admin", "access")


def test_exact_phrase_match_scores_above_term_only_match() -> None:
    exact = make_candidate(
        chunk_id="00000000-0000-0000-0000-000000000021",
        content="Admin access requires MFA.",
    )
    term_only = make_candidate(
        chunk_id="00000000-0000-0000-0000-000000000022",
        content="MFA is required for administrator accounts.",
    )

    results = search_chunks((term_only, exact), "admin access")

    assert results[0].candidate.chunk_id == exact.chunk_id
    assert results[0].exact_phrase_match is True


def test_search_does_not_match_substrings_inside_words() -> None:
    candidate = make_candidate(
        content="Administrator accounts require MFA.",
        section_label="Administrator Controls",
    )

    results = search_chunks((candidate,), "admin")

    assert results == ()


def test_duplicate_query_terms_are_deduplicated() -> None:
    candidate = make_candidate(
        content="Admin access requires MFA for admin access.",
    )

    results = search_chunks((candidate,), "admin admin access")

    assert len(results) == 1
    assert results[0].matched_terms == ("admin", "access")


def test_result_limit_is_enforced() -> None:
    candidates = tuple(
        make_candidate(
            chunk_id=f"00000000-0000-0000-0000-{index:012d}",
            content=f"Admin access policy {index}",
        )
        for index in range(1, 6)
    )

    results = search_chunks(candidates, "admin access", limit=2)

    assert len(results) == 2


def test_result_order_is_deterministic_for_equal_scores() -> None:
    first = make_candidate(
        chunk_id="00000000-0000-0000-0000-000000000030",
        document_id="00000000-0000-0000-0000-000000000003",
        content="MFA enabled.",
    )
    second = make_candidate(
        chunk_id="00000000-0000-0000-0000-000000000031",
        document_id="00000000-0000-0000-0000-000000000002",
        content="MFA enabled.",
    )

    results = search_chunks((first, second), "MFA")

    assert [result.candidate.document_id.hex for result in results] == [
        second.document_id.hex,
        first.document_id.hex,
    ]


def test_invalid_result_limit_is_rejected() -> None:
    candidate = make_candidate()

    with pytest.raises(ValueError, match="outside the permitted range"):
        search_chunks((candidate,), "admin", limit=0)
