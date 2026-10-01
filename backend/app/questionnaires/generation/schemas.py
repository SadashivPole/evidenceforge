"""Pydantic schemas for untrusted future model output validation."""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.questionnaires.generation.config import (
    ALLOWED_MODEL_PROPOSED_STATUSES,
    MAX_ANSWER_CHARACTERS,
    MAX_CITED_HANDLES,
    MAX_UNCERTAINTY_NOTES_CHARACTERS,
)
from app.questionnaires.types import ResponseStatus

_HANDLE_PATTERN = re.compile(r"^EVIDENCE-[1-9]\d*$")


class GeneratedDraftPayload(BaseModel):
    """Strict schema for untrusted model output.

    Enforces:
    - Extra fields forbidden (strict mode, no tool requests or arbitrary JSON).
    - Status restricted to allowed proposed statuses (PROPOSED, INSUFFICIENT_EVIDENCE only).
    - Bounded answer text and uncertainty notes.
    - Opaque citation handle format ("EVIDENCE-1", etc.).
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    answer: Annotated[str | None, Field(max_length=MAX_ANSWER_CHARACTERS)] = None
    status: ResponseStatus
    citation_handles: list[str] = Field(
        default_factory=list,
        max_length=MAX_CITED_HANDLES,
    )
    uncertainty_notes: Annotated[str | None, Field(max_length=MAX_UNCERTAINTY_NOTES_CHARACTERS)] = (
        None
    )

    @field_validator("status")
    @classmethod
    def validate_allowed_status(cls, value: ResponseStatus) -> ResponseStatus:
        if value not in ALLOWED_MODEL_PROPOSED_STATUSES:
            raise ValueError(
                f"Status '{value.value}' is not allowed for model output. "
                "Untrusted model outputs may only propose PROPOSED or INSUFFICIENT_EVIDENCE. "
                "Reviewer, approval, stale, conflict, and workflow states are exclusively "
                "server-controlled."
            )
        return value

    @field_validator("citation_handles")
    @classmethod
    def validate_handle_format_and_uniqueness(cls, handles: list[str]) -> list[str]:
        seen = set()
        for h in handles:
            if not _HANDLE_PATTERN.match(h):
                raise ValueError(
                    f"Invalid citation handle format '{h}'. Must match 'EVIDENCE-N' "
                    "(e.g. 'EVIDENCE-1'). Raw UUIDs or database IDs are strictly forbidden."
                )
            if h in seen:
                raise ValueError(f"Duplicate citation handle '{h}' in model output.")
            seen.add(h)
        return handles
