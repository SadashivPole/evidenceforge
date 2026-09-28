"""Pure deterministic search service for EvidenceForge evidence chunks."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

from app.evidence.search.policy import DEFAULT_SEARCH_POLICY, SearchPolicy
from app.evidence.search.types import SearchChunkCandidate, SearchResult

_WHITESPACE_PATTERN = re.compile(r"\s+")
_TOKEN_PATTERN = re.compile(r"\w+", re.UNICODE)


def normalize_query(
    query: str,
    *,
    policy: SearchPolicy = DEFAULT_SEARCH_POLICY,
) -> str:
    """Normalize and validate a search query deterministically."""

    if not isinstance(query, str):
        raise TypeError("Search query must be a string")

    normalized = unicodedata.normalize("NFKC", query)
    normalized = _WHITESPACE_PATTERN.sub(" ", normalized).strip()

    if len(normalized) < policy.min_query_length:
        raise ValueError("Search query is too short")

    if len(normalized) > policy.max_query_length:
        raise ValueError("Search query is too long")

    return normalized


def search_chunks(
    candidates: tuple[SearchChunkCandidate, ...],
    query: str,
    *,
    policy: SearchPolicy = DEFAULT_SEARCH_POLICY,
    limit: int | None = None,
) -> tuple[SearchResult, ...]:
    """Search chunks using deterministic phrase and term matching."""

    normalized_query = normalize_query(query, policy=policy)

    effective_limit = policy.default_limit if limit is None else limit

    if effective_limit < 1 or effective_limit > policy.max_limit:
        raise ValueError("Search result limit is outside the permitted range")

    query_folded = normalized_query.casefold()
    query_tokens = tuple(
        dict.fromkeys(token.casefold() for token in _TOKEN_PATTERN.findall(normalized_query))
    )

    results: list[SearchResult] = []

    for candidate in candidates:
        result = _score_candidate(
            candidate,
            query_folded=query_folded,
            query_tokens=query_tokens,
        )

        if result is not None:
            results.append(result)

    results.sort(key=_result_sort_key)

    return tuple(results[:effective_limit])


def _score_candidate(
    candidate: SearchChunkCandidate,
    *,
    query_folded: str,
    query_tokens: tuple[str, ...],
) -> SearchResult | None:
    """Calculate a deterministic score for one candidate chunk."""

    content_folded = candidate.content.casefold()
    section_folded = (candidate.section_label or "").casefold()

    content_token_counts = Counter(
        token.casefold() for token in _TOKEN_PATTERN.findall(content_folded)
    )
    section_token_set = {token.casefold() for token in _TOKEN_PATTERN.findall(section_folded)}

    exact_phrase_match = _contains_whole_phrase(content_folded, query_folded)

    matched_terms: list[str] = []
    occurrence_count = 0

    for token in query_tokens:
        content_occurrences = content_token_counts[token]

        if content_occurrences > 0 or token in section_token_set:
            matched_terms.append(token)

        occurrence_count += content_occurrences

    if not exact_phrase_match and not matched_terms:
        return None

    coverage_score = len(matched_terms) * 100
    phrase_score = 1000 if exact_phrase_match else 0
    occurrence_score = min(occurrence_count, 50) * 10
    section_score = (
        25 if query_tokens and all(token in section_token_set for token in query_tokens) else 0
    )

    score = phrase_score + coverage_score + occurrence_score + section_score

    return SearchResult(
        candidate=candidate,
        score=score,
        matched_terms=tuple(matched_terms),
        exact_phrase_match=exact_phrase_match,
        occurrence_count=occurrence_count,
    )


def _contains_whole_phrase(text: str, phrase: str) -> bool:
    """Return whether a phrase occurs without matching inside a word."""

    pattern = rf"(?<!\w){re.escape(phrase)}(?!\w)"
    return re.search(pattern, text, re.UNICODE) is not None


def _result_sort_key(result: SearchResult) -> tuple[int, int, int, str, int, str]:
    """Return a stable total ordering for deterministic results."""

    candidate = result.candidate

    return (
        -result.score,
        -len(result.matched_terms),
        -result.occurrence_count,
        candidate.document_id.hex,
        candidate.version_number,
        candidate.chunk_index,
    )
