"""Markdown structure helpers for deterministic EvidenceForge chunking."""

from __future__ import annotations

import re
from dataclasses import dataclass

_ATX_HEADING_PATTERN = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+|$)(.*)$")
_FENCE_PATTERN = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


@dataclass(frozen=True, slots=True)
class MarkdownLine:
    """Structural information for one normalized text line."""

    start_char: int
    end_char: int
    content: str
    is_fenced_code: bool
    section_label: str | None


def analyze_markdown(text: str) -> tuple[MarkdownLine, ...]:
    """Analyze normalized Markdown text without modifying its content."""
    lines: list[MarkdownLine] = []

    char_offset = 0
    in_fenced_code = False
    fence_marker: str | None = None

    for raw_line in text.splitlines(keepends=True):
        line_content = raw_line.removesuffix("\n")

        line_start = char_offset
        line_end = line_start + len(line_content)

        fence_match = _FENCE_PATTERN.match(line_content)

        if in_fenced_code:
            lines.append(
                MarkdownLine(
                    start_char=line_start,
                    end_char=line_end,
                    content=line_content,
                    is_fenced_code=True,
                    section_label=None,
                )
            )

            if fence_match and fence_marker is not None:
                marker = fence_match.group(1)

                if marker[0] == fence_marker[0] and len(marker) >= len(fence_marker):
                    in_fenced_code = False
                    fence_marker = None

            char_offset += len(raw_line)
            continue

        if fence_match:
            marker = fence_match.group(1)

            lines.append(
                MarkdownLine(
                    start_char=line_start,
                    end_char=line_end,
                    content=line_content,
                    is_fenced_code=True,
                    section_label=None,
                )
            )

            in_fenced_code = True
            fence_marker = marker

            char_offset += len(raw_line)
            continue

        heading_match = _ATX_HEADING_PATTERN.match(line_content)

        section_label: str | None = None

        if heading_match:
            section_label = _clean_heading_label(heading_match.group(2))

        lines.append(
            MarkdownLine(
                start_char=line_start,
                end_char=line_end,
                content=line_content,
                is_fenced_code=False,
                section_label=section_label,
            )
        )

        char_offset += len(raw_line)

    return tuple(lines)


def _clean_heading_label(raw_label: str) -> str | None:
    """Return a canonical display label for an ATX heading."""
    label = raw_label.strip()

    if not label:
        return None

    label = re.sub(r"[ \t]+#+[ \t]*$", "", label).strip()

    return label or None
