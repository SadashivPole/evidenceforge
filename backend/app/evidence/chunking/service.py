"""Deterministic UTF-8 byte-aware EvidenceForge chunking service."""

from __future__ import annotations

from bisect import bisect_right
from hashlib import sha256

from app.evidence.chunking.markdown import MarkdownLine, analyze_markdown
from app.evidence.chunking.policy import (
    DEFAULT_CHUNKING_POLICY,
    ChunkingPolicy,
)
from app.evidence.chunking.types import ChunkResult
from app.evidence.ingestion.types import IngestionResult


def chunk_ingestion_result(
    ingestion_result: IngestionResult,
    *,
    policy: ChunkingPolicy = DEFAULT_CHUNKING_POLICY,
) -> tuple[ChunkResult, ...]:
    """Create deterministic chunks from normalized ingestion text."""
    return chunk_text(
        ingestion_result.normalized_text,
        policy=policy,
    )


def chunk_text(
    normalized_text: str,
    *,
    policy: ChunkingPolicy = DEFAULT_CHUNKING_POLICY,
) -> tuple[ChunkResult, ...]:
    """Chunk normalized text deterministically using UTF-8 byte boundaries."""
    if not normalized_text:
        return ()

    byte_offsets = _build_utf8_byte_offsets(normalized_text)
    markdown_lines = analyze_markdown(normalized_text)

    heading_starts = {line.start_char for line in markdown_lines if line.section_label is not None}

    preferred_boundaries = _build_preferred_boundaries(
        normalized_text,
        markdown_lines,
    )

    line_boundaries = _build_line_boundaries(
        normalized_text,
        markdown_lines,
    )

    chunks: list[ChunkResult] = []
    start_char = 0

    while start_char < len(normalized_text):
        start_byte = byte_offsets[start_char]
        remaining_bytes = byte_offsets[-1] - start_byte

        if remaining_bytes <= policy.target_bytes:
            end_char = len(normalized_text)
        else:
            maximum_end_byte = start_byte + policy.target_bytes
            maximum_end_char = bisect_right(byte_offsets, maximum_end_byte) - 1

            end_char = _choose_chunk_end(
                normalized_text=normalized_text,
                start_char=start_char,
                maximum_end_char=maximum_end_char,
                byte_offsets=byte_offsets,
                preferred_boundaries=preferred_boundaries,
                line_boundaries=line_boundaries,
                minimum_preferred_break_bytes=(policy.minimum_preferred_break_bytes),
            )

        if end_char <= start_char:
            raise RuntimeError("Chunker failed to make forward progress")

        content = normalized_text[start_char:end_char]
        content_bytes = content.encode("utf-8")

        section_label = _section_label_at(
            markdown_lines,
            start_char,
        )

        chunks.append(
            ChunkResult(
                chunk_index=len(chunks),
                content=content,
                content_hash=sha256(content_bytes).hexdigest(),
                normalized_start_byte=start_byte,
                normalized_end_byte=byte_offsets[end_char],
                section_label=section_label,
                page_number=None,
            )
        )

        if end_char >= len(normalized_text):
            break

        if end_char in heading_starts:
            next_start_char = end_char
        else:
            next_start_char = _calculate_overlap_start(
                start_char=start_char,
                end_char=end_char,
                byte_offsets=byte_offsets,
                overlap_bytes=policy.overlap_bytes,
            )

        if next_start_char <= start_char:
            next_start_char = end_char

        start_char = next_start_char

    return tuple(chunks)


def _build_utf8_byte_offsets(text: str) -> list[int]:
    """Return UTF-8 byte offsets for every Python string boundary."""
    offsets = [0]
    current_offset = 0

    for character in text:
        current_offset += len(character.encode("utf-8"))
        offsets.append(current_offset)

    return offsets


def _build_preferred_boundaries(
    text: str,
    lines: tuple[MarkdownLine, ...],
) -> set[int]:
    """Build paragraph and Markdown-heading boundary positions."""
    boundaries: set[int] = set()

    for line in lines:
        if line.section_label is not None and line.start_char > 0:
            boundaries.add(line.start_char)

    for index, line in enumerate(lines):
        if line.content.strip() != "":
            continue

        next_nonblank_index = index + 1

        while next_nonblank_index < len(lines):
            next_line = lines[next_nonblank_index]

            if next_line.content.strip() != "":
                boundaries.add(next_line.start_char)
                break

            next_nonblank_index += 1

    return boundaries


def _build_line_boundaries(
    text: str,
    lines: tuple[MarkdownLine, ...],
) -> set[int]:
    """Build all valid line-end boundaries."""
    boundaries: set[int] = {len(text)}

    for line in lines:
        if line.end_char >= len(text):
            boundaries.add(len(text))
            continue

        if text[line.end_char] == "\n":
            boundaries.add(line.end_char + 1)
        else:
            boundaries.add(line.end_char)

    boundaries.discard(0)

    return boundaries


def _choose_chunk_end(
    *,
    normalized_text: str,
    start_char: int,
    maximum_end_char: int,
    byte_offsets: list[int],
    preferred_boundaries: set[int],
    line_boundaries: set[int],
    minimum_preferred_break_bytes: int,
) -> int:
    """Choose the best deterministic chunk end within the byte budget."""
    candidates = [
        boundary for boundary in preferred_boundaries if start_char < boundary <= maximum_end_char
    ]

    minimum_preferred_bytes = byte_offsets[start_char] + minimum_preferred_break_bytes

    preferred_candidates = [
        boundary for boundary in candidates if byte_offsets[boundary] >= minimum_preferred_bytes
    ]

    if preferred_candidates:
        return max(preferred_candidates)

    line_candidates = [
        boundary for boundary in line_boundaries if start_char < boundary <= maximum_end_char
    ]

    if line_candidates:
        return max(line_candidates)

    whitespace_boundary = _find_latest_whitespace_boundary(
        normalized_text,
        start_char,
        maximum_end_char,
    )

    if whitespace_boundary is not None:
        return whitespace_boundary

    return maximum_end_char


def _find_latest_whitespace_boundary(
    text: str,
    start_char: int,
    maximum_end_char: int,
) -> int | None:
    """Find the latest Unicode-whitespace boundary before the hard limit."""
    for character_index in range(maximum_end_char, start_char, -1):
        if text[character_index - 1].isspace():
            return character_index

    return None


def _calculate_overlap_start(
    *,
    start_char: int,
    end_char: int,
    byte_offsets: list[int],
    overlap_bytes: int,
) -> int:
    """Calculate the next chunk start approximately overlap_bytes backwards."""
    chunk_start_byte = byte_offsets[start_char]
    chunk_end_byte = byte_offsets[end_char]
    chunk_size_bytes = chunk_end_byte - chunk_start_byte

    if chunk_size_bytes <= overlap_bytes:
        return end_char

    desired_start_byte = chunk_end_byte - overlap_bytes

    next_start_char = bisect_right(byte_offsets, desired_start_byte) - 1

    return max(start_char + 1, next_start_char)


def _section_label_at(
    lines: tuple[MarkdownLine, ...],
    start_char: int,
) -> str | None:
    """Return the active Markdown section at the given character offset."""
    active_label: str | None = None

    for line in lines:
        if line.start_char > start_char:
            break

        if line.section_label is not None:
            active_label = line.section_label

    return active_label
