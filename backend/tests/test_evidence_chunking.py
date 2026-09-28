"""Tests for deterministic EvidenceForge chunking."""

from __future__ import annotations

from hashlib import sha256

import pytest

from app.evidence.chunking import (
    DEFAULT_CHUNKING_POLICY,
    ChunkingPolicy,
    chunk_ingestion_result,
    chunk_text,
)
from app.evidence.ingestion.types import IngestionResult


def _make_ingestion_result(text: str) -> IngestionResult:
    encoded = text.encode("utf-8")

    return IngestionResult(
        original_filename="evidence.md",
        extension=".md",
        media_type="text/markdown",
        raw_size_bytes=len(encoded),
        normalized_size_bytes=len(encoded),
        raw_sha256=sha256(encoded).hexdigest(),
        normalized_sha256=sha256(encoded).hexdigest(),
        normalization_version="text-v1",
        normalized_text=text,
    )


def test_empty_text_produces_no_chunks() -> None:
    assert chunk_text("") == ()


def test_short_text_produces_one_chunk() -> None:
    text = "Security policy"

    chunks = chunk_text(text)

    assert len(chunks) == 1
    assert chunks[0].chunk_index == 0
    assert chunks[0].content == text
    assert chunks[0].normalized_start_byte == 0
    assert chunks[0].normalized_end_byte == len(text.encode("utf-8"))


def test_exact_target_size_produces_one_chunk() -> None:
    text = "a" * DEFAULT_CHUNKING_POLICY.target_bytes

    chunks = chunk_text(text)

    assert len(chunks) == 1
    assert chunks[0].content == text
    assert chunks[0].normalized_start_byte == 0
    assert chunks[0].normalized_end_byte == len(text)


def test_content_hash_uses_exact_utf8_bytes() -> None:
    text = "Security policy — approved 🚨"

    chunks = chunk_text(text)

    assert len(chunks) == 1
    assert chunks[0].content_hash == sha256(text.encode("utf-8")).hexdigest()


def test_chunk_offsets_use_utf8_bytes() -> None:
    text = "A🚨B"

    chunks = chunk_text(text)

    assert len(chunks) == 1
    assert chunks[0].normalized_start_byte == 0
    assert chunks[0].normalized_end_byte == len(text.encode("utf-8"))


def test_no_chunk_exceeds_target_bytes() -> None:
    text = ("Security control evidence. " * 1000).strip()

    chunks = chunk_text(text)

    assert len(chunks) > 1

    for chunk in chunks:
        assert len(chunk.content.encode("utf-8")) <= DEFAULT_CHUNKING_POLICY.target_bytes


def test_chunk_indexes_are_contiguous() -> None:
    text = ("Security control evidence. " * 1000).strip()

    chunks = chunk_text(text)

    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))


def test_chunks_are_exact_substrings_of_source() -> None:
    text = ("Security control evidence. " * 1000).strip()

    chunks = chunk_text(text)
    source_bytes = text.encode("utf-8")

    for chunk in chunks:
        assert source_bytes[
            chunk.normalized_start_byte : chunk.normalized_end_byte
        ] == chunk.content.encode("utf-8")


def test_byte_offsets_are_monotonic() -> None:
    text = ("Security control evidence. " * 1000).strip()

    chunks = chunk_text(text)

    for previous, current in zip(
        chunks,
        chunks[1:],
        strict=False,
    ):
        assert current.normalized_start_byte >= previous.normalized_start_byte
        assert current.normalized_end_byte > current.normalized_start_byte


def test_ordinary_chunks_have_512_byte_overlap() -> None:
    text = "A" * 9000

    chunks = chunk_text(text)

    assert len(chunks) >= 2

    first = chunks[0]
    second = chunks[1]

    overlap_bytes = first.normalized_end_byte - second.normalized_start_byte

    assert overlap_bytes == DEFAULT_CHUNKING_POLICY.overlap_bytes
    assert overlap_bytes == 512

    first_suffix = first.content.encode("utf-8")[-512:]
    second_prefix = second.content.encode("utf-8")[:512]

    assert first_suffix == second_prefix


def test_deterministic_across_repeated_runs() -> None:
    text = (
        "# Access Control\n\n"
        "Authentication and authorization requirements.\n\n"
        "## Logging\n\n"
        "Security events are centrally monitored.\n"
    )

    first = chunk_text(text)
    second = chunk_text(text)

    assert first == second


def test_heading_creates_section_boundary() -> None:
    text = (
        "A" * 3000 + "\n\n" + "# Access Control\n" + "Authentication requirements.\n" + "B" * 2000
    )

    chunks = chunk_text(text)

    assert len(chunks) >= 2

    access_chunk = next(chunk for chunk in chunks if chunk.section_label == "Access Control")

    expected_start = len(("A" * 3002).encode("utf-8"))

    assert access_chunk.normalized_start_byte == expected_start
    assert access_chunk.content.startswith("# Access Control")


def test_heading_is_not_split_by_overlap() -> None:
    text = "A" * 3800 + "\n\n" + "# Access Control\n" + "Authentication requirements." + "B" * 1000

    chunks = chunk_text(text)

    assert len(chunks) >= 2

    access_chunk = next(chunk for chunk in chunks if chunk.section_label == "Access Control")

    expected_start = len(("A" * 3802).encode("utf-8"))

    assert access_chunk.normalized_start_byte == expected_start
    assert access_chunk.content.startswith("# Access Control")


def test_fenced_code_does_not_create_heading_section() -> None:
    text = "# Main\n\n```markdown\n# Not a heading\n```\n\nNormal text."

    chunks = chunk_text(text)

    assert all(chunk.section_label in {None, "Main"} for chunk in chunks)


def test_setext_heading_is_not_recognized() -> None:
    text = "Heading\n=======\n\nBody text."

    chunks = chunk_text(text)

    assert len(chunks) == 1
    assert chunks[0].section_label is None


def test_page_number_is_none_for_text_chunking() -> None:
    text = "Security evidence"

    chunks = chunk_text(text)

    assert chunks[0].page_number is None


def test_lf_text_is_deterministic() -> None:
    text = "alpha\nbeta\n\ngamma"

    first = chunk_text(text)
    second = chunk_text(text)

    assert first == second


def test_unicode_text_never_splits_code_points() -> None:
    text = "🚨" * 5000

    chunks = chunk_text(text)
    source_bytes = text.encode("utf-8")

    for chunk in chunks:
        chunk_bytes = source_bytes[chunk.normalized_start_byte : chunk.normalized_end_byte]

        assert chunk.content.encode("utf-8") == chunk_bytes


def test_custom_policy_is_respected() -> None:
    policy = ChunkingPolicy(
        target_bytes=100,
        overlap_bytes=20,
        minimum_preferred_break_bytes=40,
    )

    text = ("Security evidence. " * 30).strip()

    chunks = chunk_text(
        text,
        policy=policy,
    )

    assert len(chunks) > 1

    for chunk in chunks:
        assert len(chunk.content.encode("utf-8")) <= 100


def test_chunk_ingestion_result_uses_normalized_text() -> None:
    ingestion_result = _make_ingestion_result("# Access Control\n\nAuthentication requirements.")

    chunks = chunk_ingestion_result(ingestion_result)

    assert len(chunks) == 1
    assert chunks[0].content == ingestion_result.normalized_text
    assert chunks[0].section_label == "Access Control"


@pytest.mark.parametrize(
    "text",
    [
        "alpha\n\nbeta",
        "alpha\n\n\nbeta",
        "alpha\nbeta",
        "é" * 5000,
        "🚨" * 3000,
    ],
)
def test_repeated_chunking_produces_identical_results(
    text: str,
) -> None:
    first = chunk_text(text)
    second = chunk_text(text)

    assert first == second
