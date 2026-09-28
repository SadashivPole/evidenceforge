"""Pydantic contracts for the EvidenceForge API."""

from __future__ import annotations

import unicodedata
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import WorkspaceRole


class WorkspaceCreate(BaseModel):
    """Payload for creating a workspace."""

    name: str = Field(min_length=1, max_length=255)


class WorkspaceUpdate(BaseModel):
    """Payload for updating workspace metadata."""

    name: str = Field(min_length=1, max_length=255)


class WorkspaceResponse(BaseModel):
    """Workspace representation."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    created_by_user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class MembershipCreate(BaseModel):
    """Payload for adding an existing user to a workspace."""

    user_id: uuid.UUID
    role: WorkspaceRole = WorkspaceRole.MEMBER


class MembershipUpdate(BaseModel):
    """Payload for changing a member's role."""

    role: WorkspaceRole


class MembershipResponse(BaseModel):
    """Membership representation."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    user_id: uuid.UUID
    role: WorkspaceRole
    created_at: datetime
    updated_at: datetime


class AuditEventResponse(BaseModel):
    """Audit event representation with metadata retained for reviewers."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID | None
    actor_user_id: uuid.UUID | None
    action: str
    resource_type: str
    resource_id: str | None
    event_metadata: dict[str, Any]
    created_at: datetime


class EvidenceDocumentCreate(BaseModel):
    """Payload for creating a workspace-scoped evidence document."""

    name: str = Field(min_length=1, max_length=255)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        """Reject blank or control-character document names."""

        normalized = value.strip()

        if not normalized:
            raise ValueError("Document name cannot be blank")

        if any(unicodedata.category(character) == "Cc" for character in normalized):
            raise ValueError("Document name contains control characters")

        return normalized


class EvidenceDocumentResponse(BaseModel):
    """Workspace-scoped evidence document metadata."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    source_type: str
    status: str
    created_by_user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class EvidenceVersionIngestionResponse(BaseModel):
    """Result of uploading and persisting one evidence version."""

    outcome: str
    document_id: uuid.UUID
    version_id: uuid.UUID
    version_number: int
    chunk_count: int
    ingestion_attempt_id: uuid.UUID


class EvidenceDocumentVersionResponse(BaseModel):
    """Metadata for an immutable evidence document version."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    version_number: int
    normalized_sha256: str
    raw_sha256: str
    normalization_version: str
    original_filename: str
    media_type: str
    raw_size_bytes: int
    normalized_size_bytes: int
    chunking_version: str
    chunk_target_bytes: int
    chunk_overlap_bytes: int
    created_by_user_id: uuid.UUID
    created_at: datetime


class EvidenceChunkResponse(BaseModel):
    """One immutable evidence retrieval chunk."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_version_id: uuid.UUID
    chunk_index: int
    content: str
    content_hash: str
    normalized_start_byte: int
    normalized_end_byte: int
    section_label: str | None
    page_number: int | None
    created_at: datetime

class QuestionnaireQuestionResponse(BaseModel):
    """One normalized questionnaire question returned by import preview."""

    question_id: str
    ordinal: int
    sheet_name: str
    source_row: int
    question_text: str
    section_path: list[str]


class QuestionnaireImportedSheetResponse(BaseModel):
    """Metadata for one worksheet that produced questions."""

    sheet_name: str
    header_row: int
    question_header: str
    section_header: str | None
    question_count: int


class QuestionnaireIgnoredSheetResponse(BaseModel):
    """Metadata for one worksheet that was intentionally ignored."""

    sheet_name: str
    reason: str


class QuestionnaireImportResponse(BaseModel):
    """Deterministic XLSX questionnaire import preview."""

    questionnaire_id: uuid.UUID
    name: str
    source_filename: str
    status: str
    parser_version: str
    questions: list[QuestionnaireQuestionResponse]
    imported_sheets: list[QuestionnaireImportedSheetResponse]
    ignored_sheets: list[QuestionnaireIgnoredSheetResponse]