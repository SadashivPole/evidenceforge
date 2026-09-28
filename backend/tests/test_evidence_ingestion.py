"""Tests for deterministic EvidenceForge evidence ingestion."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from hashlib import sha256

import pytest

from app.evidence.ingestion import IngestionInput, ingest
from app.evidence.ingestion.errors import (
    EmptyContentError,
    FileTooLargeError,
    InvalidFilenameError,
    InvalidInputTypeError,
    InvalidUtf8Error,
    UnsupportedExtensionError,
    UnsupportedMediaTypeError,
    WhitespaceOnlyContentError,
)
from app.evidence.ingestion.normalization import decode_and_normalize_text
from app.evidence.ingestion.policy import (
    MAX_RAW_BYTES,
    IngestionPolicy,
)

# ---------------------------------------------------------------------------
# Normalization tests
# ---------------------------------------------------------------------------


def test_decodes_ascii_text() -> None:
    result = decode_and_normalize_text(b"alpha\nbeta")

    assert result == "alpha\nbeta"


def test_decodes_multibyte_utf8() -> None:
    result = decode_and_normalize_text("Security policy — approved".encode())

    assert result == "Security policy — approved"


def test_decodes_emoji() -> None:
    result = decode_and_normalize_text("SOC alert 🚨".encode())

    assert result == "SOC alert 🚨"


def test_removes_one_leading_utf8_bom() -> None:
    raw = b"\xef\xbb\xbfalpha\nbeta"

    result = decode_and_normalize_text(raw)

    assert result == "alpha\nbeta"


def test_bom_only_content_is_empty() -> None:
    raw = b"\xef\xbb\xbf"

    with pytest.raises(EmptyContentError):
        decode_and_normalize_text(raw)


def test_rejects_empty_content() -> None:
    with pytest.raises(EmptyContentError):
        decode_and_normalize_text(b"")


def test_rejects_whitespace_only_content() -> None:
    with pytest.raises(WhitespaceOnlyContentError):
        decode_and_normalize_text(b" \t\n\r\n ")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (b"alpha\nbeta", "alpha\nbeta"),
        (b"alpha\r\nbeta", "alpha\nbeta"),
        (b"alpha\rbeta", "alpha\nbeta"),
        (
            b"alpha\r\nbeta\rgamma\ndelta",
            "alpha\nbeta\ngamma\ndelta",
        ),
    ],
)
def test_normalizes_line_endings(
    raw: bytes,
    expected: str,
) -> None:
    assert decode_and_normalize_text(raw) == expected


def test_preserves_at_most_two_consecutive_blank_lines() -> None:
    raw = b"alpha\n\n\n\n\nbeta"

    result = decode_and_normalize_text(raw)

    assert result == "alpha\n\n\nbeta"


def test_normalizes_whitespace_only_blank_lines() -> None:
    raw = b"alpha\n   \n\t\n \t \nbeta"

    result = decode_and_normalize_text(raw)

    assert result == "alpha\n\n\nbeta"


def test_preserves_substantive_trailing_spaces() -> None:
    raw = b"alpha   \nbeta\t"

    result = decode_and_normalize_text(raw)

    assert result == "alpha   \nbeta\t"


def test_preserves_leading_blank_lines_with_limit() -> None:
    raw = b"\n\n\nalpha"

    result = decode_and_normalize_text(raw)

    assert result == "\n\nalpha"


def test_preserves_trailing_blank_lines_with_limit() -> None:
    raw = b"alpha\n\n\n"

    result = decode_and_normalize_text(raw)

    assert result == "alpha\n\n"


def test_preserves_unicode_line_separator_as_content() -> None:
    raw = "alpha\u2028beta".encode()

    result = decode_and_normalize_text(raw)

    assert result == "alpha\u2028beta"


def test_rejects_invalid_utf8() -> None:
    raw = b"valid text \xff invalid utf8"

    with pytest.raises(InvalidUtf8Error) as exc_info:
        decode_and_normalize_text(raw)

    assert exc_info.value.byte_offset == 11


def test_rejects_invalid_utf8_without_replacement() -> None:
    raw = b"\xff\xfe\xfd"

    with pytest.raises(InvalidUtf8Error):
        decode_and_normalize_text(raw)


def test_normalization_is_deterministic() -> None:
    raw = b"alpha\r\n\n\nbeta"

    first = decode_and_normalize_text(raw)
    second = decode_and_normalize_text(raw)

    assert first == second


def test_lf_and_crlf_produce_identical_normalized_text() -> None:
    lf = decode_and_normalize_text(b"alpha\nbeta")
    crlf = decode_and_normalize_text(b"alpha\r\nbeta")

    assert lf == crlf
    assert lf == "alpha\nbeta"


@pytest.mark.parametrize(
    "raw",
    [
        b"   ",
        b"\t\t",
        b"\n\n",
        b"\r\r",
        b"\r\n\r\n",
        "\u2003\u2003".encode(),
        "\u00a0\u00a0".encode(),
    ],
)
def test_rejects_unicode_and_ascii_whitespace_only_content(
    raw: bytes,
) -> None:
    with pytest.raises(WhitespaceOnlyContentError):
        decode_and_normalize_text(raw)


# ---------------------------------------------------------------------------
# Service tests
# ---------------------------------------------------------------------------


def test_ingests_txt_file() -> None:
    input_data = IngestionInput(
        original_filename="security-policy.txt",
        media_type="text/plain",
        raw_bytes=b"approved security policy",
    )

    result = ingest(input_data)

    assert result.original_filename == "security-policy.txt"
    assert result.extension == ".txt"
    assert result.media_type == "text/plain"
    assert result.normalized_text == "approved security policy"
    assert result.raw_size_bytes == len(input_data.raw_bytes)
    assert result.normalized_size_bytes == len(result.normalized_text.encode("utf-8"))


def test_ingests_markdown_file() -> None:
    input_data = IngestionInput(
        original_filename="security-policy.md",
        media_type="text/markdown",
        raw_bytes=b"# Security Policy\n\nApproved.",
    )

    result = ingest(input_data)

    assert result.extension == ".md"
    assert result.media_type == "text/markdown"
    assert result.normalized_text == "# Security Policy\n\nApproved."


@pytest.mark.parametrize(
    ("filename", "media_type"),
    [
        ("SECURITY.TXT", "text/plain"),
        ("Security.TXT", "text/plain; charset=utf-8"),
        ("POLICY.MD", "text/markdown"),
        ("Policy.MD", 'text/markdown; charset="UTF-8"'),
    ],
)
def test_accepts_case_insensitive_extension_and_media_type(
    filename: str,
    media_type: str,
) -> None:
    result = ingest(
        IngestionInput(
            original_filename=filename,
            media_type=media_type,
            raw_bytes=b"security evidence",
        )
    )

    expected_extension = ".txt" if filename.lower().endswith(".txt") else ".md"

    assert result.extension == expected_extension


def test_normalizes_declared_media_type_to_canonical_value() -> None:
    result = ingest(
        IngestionInput(
            original_filename="policy.txt",
            media_type="TEXT/PLAIN; charset=UTF-8",
            raw_bytes=b"policy",
        )
    )

    assert result.media_type == "text/plain"


@pytest.mark.parametrize(
    "filename",
    [
        "notes.markdown",
        "notes.text",
        "notes.txt.exe",
        "notes.exe",
    ],
)
def test_rejects_unsupported_extension(filename: str) -> None:
    with pytest.raises(UnsupportedExtensionError):
        ingest(
            IngestionInput(
                original_filename=filename,
                media_type="text/plain",
                raw_bytes=b"content",
            )
        )


@pytest.mark.parametrize(
    ("filename", "media_type"),
    [
        ("notes.txt", "text/markdown"),
        ("notes.md", "text/plain"),
        ("notes.txt", "application/octet-stream"),
        ("notes.md", "application/octet-stream"),
    ],
)
def test_rejects_extension_media_type_mismatch(
    filename: str,
    media_type: str,
) -> None:
    with pytest.raises(UnsupportedMediaTypeError):
        ingest(
            IngestionInput(
                original_filename=filename,
                media_type=media_type,
                raw_bytes=b"content",
            )
        )


@pytest.mark.parametrize(
    "media_type",
    [
        None,
        "",
        "application/json",
        "text/plain; charset=iso-8859-1",
        "text/plain; charset=windows-1252",
        "text/plain; format=flowed",
        "text/plain; charset=utf-16",
    ],
)
def test_rejects_unsupported_media_type(
    media_type: str | None,
) -> None:
    with pytest.raises(UnsupportedMediaTypeError):
        ingest(
            IngestionInput(
                original_filename="notes.txt",
                media_type=media_type,
                raw_bytes=b"content",
            )
        )


def test_accepts_exact_maximum_file_size() -> None:
    raw_bytes = b"a" * MAX_RAW_BYTES

    result = ingest(
        IngestionInput(
            original_filename="large.txt",
            media_type="text/plain",
            raw_bytes=raw_bytes,
        )
    )

    assert result.raw_size_bytes == MAX_RAW_BYTES
    assert result.normalized_size_bytes == MAX_RAW_BYTES


def test_rejects_file_one_byte_over_maximum_size() -> None:
    raw_bytes = b"a" * (MAX_RAW_BYTES + 1)

    with pytest.raises(FileTooLargeError) as exc_info:
        ingest(
            IngestionInput(
                original_filename="too-large.txt",
                media_type="text/plain",
                raw_bytes=raw_bytes,
            )
        )

    assert exc_info.value.actual_bytes == MAX_RAW_BYTES + 1
    assert exc_info.value.maximum_bytes == MAX_RAW_BYTES


def test_custom_policy_controls_maximum_size() -> None:
    policy = IngestionPolicy(max_raw_bytes=10)

    result = ingest(
        IngestionInput(
            original_filename="small.txt",
            media_type="text/plain",
            raw_bytes=b"0123456789",
        ),
        policy=policy,
    )

    assert result.raw_size_bytes == 10


def test_custom_policy_rejects_content_above_limit() -> None:
    policy = IngestionPolicy(max_raw_bytes=10)

    with pytest.raises(FileTooLargeError):
        ingest(
            IngestionInput(
                original_filename="small.txt",
                media_type="text/plain",
                raw_bytes=b"01234567890",
            ),
            policy=policy,
        )


@pytest.mark.parametrize(
    "filename",
    [
        "",
        ".",
        ".hidden",
        "folder/",
        "folder\\",
    ],
)
def test_rejects_invalid_filename(filename: str) -> None:
    with pytest.raises(InvalidFilenameError):
        ingest(
            IngestionInput(
                original_filename=filename,
                media_type="text/plain",
                raw_bytes=b"content",
            )
        )


def test_rejects_filename_longer_than_policy_limit() -> None:
    policy = IngestionPolicy(max_filename_length=10)

    with pytest.raises(InvalidFilenameError):
        ingest(
            IngestionInput(
                original_filename="very-long-name.txt",
                media_type="text/plain",
                raw_bytes=b"content",
            ),
            policy=policy,
        )


@pytest.mark.parametrize(
    "filename",
    [
        "notes\x00.txt",
        "notes\n.txt",
        "notes\r.txt",
    ],
)
def test_rejects_control_characters_in_filename(
    filename: str,
) -> None:
    with pytest.raises(InvalidFilenameError):
        ingest(
            IngestionInput(
                original_filename=filename,
                media_type="text/plain",
                raw_bytes=b"content",
            )
        )


@pytest.mark.parametrize(
    ("filename", "media_type"),
    [
        (r"..\..\secret.txt", "text/plain"),
        ("../../secret.txt", "text/plain"),
        (r"C:\Windows\System32\secret.txt", "text/plain"),
        ("folder/subfolder/evidence.md", "text/markdown"),
    ],
)
def test_path_like_filename_is_metadata_only(
    filename: str,
    media_type: str,
) -> None:
    result = ingest(
        IngestionInput(
            original_filename=filename,
            media_type=media_type,
            raw_bytes=b"confidential evidence",
        )
    )

    assert result.original_filename == filename
    assert result.extension in {".txt", ".md"}
    assert result.normalized_text == "confidential evidence"


def test_raw_hash_is_hash_of_exact_input_bytes() -> None:
    raw_bytes = b"alpha\r\nbeta"

    result = ingest(
        IngestionInput(
            original_filename="evidence.txt",
            media_type="text/plain",
            raw_bytes=raw_bytes,
        )
    )

    assert result.raw_sha256 == sha256(raw_bytes).hexdigest()


def test_normalized_hash_is_hash_of_normalized_utf8() -> None:
    raw_bytes = b"alpha\r\nbeta"

    result = ingest(
        IngestionInput(
            original_filename="evidence.txt",
            media_type="text/plain",
            raw_bytes=raw_bytes,
        )
    )

    expected = sha256(b"alpha\nbeta").hexdigest()

    assert result.normalized_sha256 == expected


def test_lf_and_crlf_have_same_normalized_hash() -> None:
    lf_result = ingest(
        IngestionInput(
            original_filename="evidence.txt",
            media_type="text/plain",
            raw_bytes=b"alpha\nbeta",
        )
    )

    crlf_result = ingest(
        IngestionInput(
            original_filename="evidence.txt",
            media_type="text/plain",
            raw_bytes=b"alpha\r\nbeta",
        )
    )

    assert lf_result.normalized_sha256 == crlf_result.normalized_sha256
    assert lf_result.raw_sha256 != crlf_result.raw_sha256


def test_filename_change_does_not_change_content_hashes() -> None:
    first = ingest(
        IngestionInput(
            original_filename="first.txt",
            media_type="text/plain",
            raw_bytes=b"same evidence",
        )
    )

    second = ingest(
        IngestionInput(
            original_filename="second.txt",
            media_type="text/plain",
            raw_bytes=b"same evidence",
        )
    )

    assert first.raw_sha256 == second.raw_sha256
    assert first.normalized_sha256 == second.normalized_sha256


def test_normalization_version_is_present() -> None:
    result = ingest(
        IngestionInput(
            original_filename="evidence.txt",
            media_type="text/plain",
            raw_bytes=b"evidence",
        )
    )

    assert result.normalization_version == "text-v1"


def test_custom_normalization_version_is_preserved() -> None:
    policy = IngestionPolicy(normalization_version="text-v1")

    result = ingest(
        IngestionInput(
            original_filename="evidence.txt",
            media_type="text/plain",
            raw_bytes=b"evidence",
        ),
        policy=policy,
    )

    assert result.normalization_version == "text-v1"


def test_result_is_immutable() -> None:
    result = ingest(
        IngestionInput(
            original_filename="evidence.txt",
            media_type="text/plain",
            raw_bytes=b"evidence",
        )
    )

    with pytest.raises(FrozenInstanceError):
        result.original_filename = "changed.txt"  # type: ignore[misc]


def test_ingestion_is_deterministic() -> None:
    input_data = IngestionInput(
        original_filename="policy.txt",
        media_type="text/plain; charset=UTF-8",
        raw_bytes=b"alpha\r\n\n\nbeta",
    )

    first = ingest(input_data)
    second = ingest(input_data)

    assert first == second


def test_rejects_non_ingestion_input_object() -> None:
    with pytest.raises(InvalidInputTypeError):
        ingest("not-an-ingestion-input")  # type: ignore[arg-type]


def test_rejects_non_bytes_raw_content() -> None:
    input_data = IngestionInput(
        original_filename="policy.txt",
        media_type="text/plain",
        raw_bytes=b"content",
    )

    object.__setattr__(input_data, "raw_bytes", "not-bytes")

    with pytest.raises(InvalidInputTypeError):
        ingest(input_data)
