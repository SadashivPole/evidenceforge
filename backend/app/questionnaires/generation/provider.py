"""Provider-neutral abstract adapter boundary for future generation integration.

Isolates model SDK, credentials, networking, retry, and latency telemetry
from core EvidenceForge domain logic, authorization, and response persistence.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.questionnaires.generation.projection import ModelGenerationInput
from app.questionnaires.generation.schemas import GeneratedDraftPayload
from app.questionnaires.types import ResponseStatus


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    """Neutral configuration contract for future generation providers."""

    provider_id: str
    model_id: str
    model_version: str
    temperature: float = 0.0
    max_output_tokens: int = 1000
    timeout_seconds: float = 30.0
    max_retries: int = 2


class GenerationProvider(ABC):
    """Abstract provider adapter boundary."""

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    @abstractmethod
    async def generate(self, model_input: ModelGenerationInput) -> GeneratedDraftPayload:
        """Execute model generation and return untrusted structured payload.

        Must NOT access database, persist response, or resolve internal UUIDs.
        """
        ...


class MockEvaluationProvider(GenerationProvider):
    """Deterministic mock provider used strictly for evaluation benchmark testing.

    Does NOT make network calls, execute prompts, or use API keys.
    """

    def __init__(
        self,
        config: ProviderConfig,
        *,
        default_status: ResponseStatus = ResponseStatus.PROPOSED,
        default_answer: str = "Mock answer generated from evidence.",
        default_handles: tuple[str, ...] = ("EVIDENCE-1",),
        default_uncertainty: str | None = None,
    ) -> None:
        super().__init__(config)
        self.default_status = default_status
        self.default_answer = default_answer
        self.default_handles = default_handles
        self.default_uncertainty = default_uncertainty

    async def generate(self, model_input: ModelGenerationInput) -> GeneratedDraftPayload:
        """Return deterministic mock payload based on input context."""
        if not model_input.evidence_items or model_input.has_stale_or_conflicting_evidence:
            return GeneratedDraftPayload(
                answer="Insufficient or conflicting evidence to provide a verified answer.",
                status=ResponseStatus.INSUFFICIENT_EVIDENCE,
                citation_handles=[],
                uncertainty_notes="Evidence is insufficient or conflicting.",
            )

        handles = [
            item.citation_handle
            for item in model_input.evidence_items
            if item.citation_handle in self.default_handles
        ]
        if not handles and model_input.evidence_items:
            handles = [model_input.evidence_items[0].citation_handle]

        return GeneratedDraftPayload(
            answer=self.default_answer,
            status=self.default_status,
            citation_handles=handles,
            uncertainty_notes=self.default_uncertainty,
        )
