"""Public interface for evidence context budgeting and window selection."""

from app.evidence.context.config import (
    DEFAULT_CONTEXT_BUDGET_CONFIG,
    DEFAULT_SELECTION_VERSION,
    MAX_CONTEXT_CHUNKS,
    MAX_CONTEXT_TOKENS,
    ContextBudgetConfig,
)
from app.evidence.context.errors import (
    ContextSelectionAuthorizationError,
    ContextSelectionConfigurationError,
    ContextSelectionError,
    ContextSelectionWorkspaceMismatchError,
)
from app.evidence.context.selector import (
    EvidenceContextSelector,
    select_evidence_context,
)
from app.evidence.context.token_counter import (
    DEFAULT_TOKEN_COUNTER,
    ExactModelTokenCounter,
    HeuristicWhitespacePunctuationTokenCounter,
    TokenCounter,
)
from app.evidence.context.types import (
    ContextSelectionDiagnostics,
    ContextSelectionResult,
    SelectedEvidenceCandidate,
)

__all__ = [
    "ContextBudgetConfig",
    "ContextSelectionAuthorizationError",
    "ContextSelectionConfigurationError",
    "ContextSelectionDiagnostics",
    "ContextSelectionError",
    "ContextSelectionResult",
    "ContextSelectionWorkspaceMismatchError",
    "DEFAULT_CONTEXT_BUDGET_CONFIG",
    "DEFAULT_SELECTION_VERSION",
    "DEFAULT_TOKEN_COUNTER",
    "EvidenceContextSelector",
    "ExactModelTokenCounter",
    "HeuristicWhitespacePunctuationTokenCounter",
    "MAX_CONTEXT_CHUNKS",
    "MAX_CONTEXT_TOKENS",
    "SelectedEvidenceCandidate",
    "TokenCounter",
    "select_evidence_context",
]
