"""Public interface for generation boundary and server-side citation validation."""

from app.questionnaires.generation.config import (
    ALLOWED_MODEL_PROPOSED_STATUSES,
    DEFAULT_GENERATION_BOUNDARY_CONFIG,
    GENERATION_BOUNDARY_VERSION,
    MAX_ANSWER_CHARACTERS,
    MAX_CITED_HANDLES,
    MAX_UNCERTAINTY_NOTES_CHARACTERS,
    GenerationBoundaryConfig,
)
from app.questionnaires.generation.errors import (
    GenerationBoundaryError,
    GenerationCitationValidationError,
    GenerationOutputLengthError,
    GenerationPayloadMalformedError,
    GenerationStatusValidationError,
    GenerationWorkspaceMismatchError,
)
from app.questionnaires.generation.projection import (
    ModelEvidenceItem,
    ModelGenerationInput,
    project_evidence_item,
    project_generation_context_to_model_input,
)
from app.questionnaires.generation.provider import (
    GenerationProvider,
    MockEvaluationProvider,
    ProviderConfig,
)
from app.questionnaires.generation.schemas import GeneratedDraftPayload
from app.questionnaires.generation.types import (
    GenerationContext,
    GenerationEvidenceItem,
    ValidatedDraftResponse,
)
from app.questionnaires.generation.validator import (
    GenerationDraftValidator,
    build_generation_context,
    validate_generated_draft,
)

__all__ = [
    "ALLOWED_MODEL_PROPOSED_STATUSES",
    "DEFAULT_GENERATION_BOUNDARY_CONFIG",
    "GENERATION_BOUNDARY_VERSION",
    "GeneratedDraftPayload",
    "GenerationBoundaryConfig",
    "GenerationBoundaryError",
    "GenerationCitationValidationError",
    "GenerationContext",
    "GenerationDraftValidator",
    "GenerationEvidenceItem",
    "GenerationOutputLengthError",
    "GenerationPayloadMalformedError",
    "GenerationProvider",
    "GenerationStatusValidationError",
    "GenerationWorkspaceMismatchError",
    "MAX_ANSWER_CHARACTERS",
    "MAX_CITED_HANDLES",
    "MAX_UNCERTAINTY_NOTES_CHARACTERS",
    "MockEvaluationProvider",
    "ModelEvidenceItem",
    "ModelGenerationInput",
    "ProviderConfig",
    "ValidatedDraftResponse",
    "build_generation_context",
    "project_evidence_item",
    "project_generation_context_to_model_input",
    "validate_generated_draft",
]
