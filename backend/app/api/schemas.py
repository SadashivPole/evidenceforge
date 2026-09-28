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