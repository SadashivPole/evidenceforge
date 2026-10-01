"""Configuration constants and bounds for generation boundary and citation validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from app.questionnaires.types import ResponseStatus

MAX_ANSWER_CHARACTERS: Final[int] = 4000
MAX_UNCERTAINTY_NOTES_CHARACTERS: Final[int] = 1000
MAX_CITED_HANDLES: Final[int] = 5
GENERATION_BOUNDARY_VERSION: Final[str] = "generation-boundary-v1"

# Allowed statuses that an untrusted model may propose.
# Strictly limited to PROPOSED and INSUFFICIENT_EVIDENCE.
# All workflow, review, approval, stale, conflict, and disclosure states
# are exclusively server/human-controlled.
ALLOWED_MODEL_PROPOSED_STATUSES: Final[frozenset[ResponseStatus]] = frozenset(
    {
        ResponseStatus.PROPOSED,
        ResponseStatus.INSUFFICIENT_EVIDENCE,
    }
)


@dataclass(frozen=True, slots=True)
class GenerationBoundaryConfig:
    """Immutable bounds controlling generation input/output validation."""

    max_answer_characters: int = MAX_ANSWER_CHARACTERS
    max_uncertainty_notes_characters: int = MAX_UNCERTAINTY_NOTES_CHARACTERS
    max_cited_handles: int = MAX_CITED_HANDLES
    generation_boundary_version: str = GENERATION_BOUNDARY_VERSION
    strict_schema_validation: bool = True

    def __post_init__(self) -> None:
        if self.max_answer_characters < 1:
            raise ValueError("max_answer_characters must be positive")
        if self.max_answer_characters > MAX_ANSWER_CHARACTERS:
            raise ValueError(f"max_answer_characters must not exceed {MAX_ANSWER_CHARACTERS}")
        if self.max_uncertainty_notes_characters < 1:
            raise ValueError("max_uncertainty_notes_characters must be positive")
        if self.max_cited_handles < 1 or self.max_cited_handles > MAX_CITED_HANDLES:
            raise ValueError(f"max_cited_handles must be between 1 and {MAX_CITED_HANDLES}")


DEFAULT_GENERATION_BOUNDARY_CONFIG: Final[GenerationBoundaryConfig] = GenerationBoundaryConfig()
