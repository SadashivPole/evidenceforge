"""SQLAlchemy models for the EvidenceForge persistence layer."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all persisted application models."""


class WorkspaceRole(enum.StrEnum):
    """Roles supported by a workspace membership."""

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class User(Base):
    """Local representation of an authenticated application user."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    external_subject: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
    )
    email: Mapped[str] = mapped_column(
        String(320),
        unique=True,
        index=True,
    )
    display_name: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(
        default=True,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class ApiToken(Base):
    """Opaque bearer token record provisioned outside the public API."""

    __tablename__ = "api_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255))
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class Workspace(Base):
    """Tenant boundary for all workspace-scoped data."""

    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(255))
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class WorkspaceMembership(Base):
    """A user's role inside a workspace."""

    __tablename__ = "workspace_memberships"

    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "user_id",
            name="uq_workspace_memberships_workspace_user",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    role: Mapped[WorkspaceRole] = mapped_column(
        String(16),
        default=WorkspaceRole.MEMBER,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class AuditEvent(Base):
    """Immutable security audit record."""

    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    action: Mapped[str] = mapped_column(
        String(128),
        index=True,
    )
    resource_type: Mapped[str] = mapped_column(String(128))
    resource_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )


class EvidenceDocument(Base):
    """Workspace-scoped source document."""

    __tablename__ = "evidence_documents"

    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "name",
            name="uq_evidence_documents_workspace_name",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255))
    source_type: Mapped[str] = mapped_column(
        String(32),
        default="file",
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default="active",
        nullable=False,
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class EvidenceDocumentVersion(Base):
    """Immutable version of an evidence document."""

    __tablename__ = "evidence_document_versions"

    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "version_number",
            name="uq_evidence_document_versions_document_version",
        ),
        UniqueConstraint(
            "document_id",
            "normalized_sha256",
            "normalization_version",
            "chunking_version",
            name="uq_evidence_document_versions_representation",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("evidence_documents.id", ondelete="CASCADE"),
        index=True,
    )
    version_number: Mapped[int] = mapped_column(nullable=False)

    normalized_sha256: Mapped[str] = mapped_column(
        String(64),
        index=True,
        nullable=False,
    )
    raw_sha256: Mapped[str] = mapped_column(
        String(64),
        index=True,
        nullable=False,
    )
    normalization_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    original_filename: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(127))

    raw_size_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    normalized_size_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    extracted_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    chunking_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    chunk_target_bytes: Mapped[int] = mapped_column(
        nullable=False,
    )
    chunk_overlap_bytes: Mapped[int] = mapped_column(
        nullable=False,
    )

    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class EvidenceChunk(Base):
    """Deterministic retrieval unit derived from an immutable document version."""

    __tablename__ = "evidence_chunks"

    __table_args__ = (
        UniqueConstraint(
            "document_version_id",
            "chunk_index",
            name="uq_evidence_chunks_version_index",
        ),
        CheckConstraint(
            "normalized_end_byte > normalized_start_byte",
            name="ck_evidence_chunks_valid_byte_range",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "evidence_document_versions.id",
            ondelete="CASCADE",
        ),
        index=True,
    )
    chunk_index: Mapped[int] = mapped_column(nullable=False)
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    content_hash: Mapped[str] = mapped_column(
        String(64),
        index=True,
        nullable=False,
    )
    normalized_start_byte: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    normalized_end_byte: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    section_label: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    page_number: Mapped[int | None] = mapped_column(
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class EvidenceChunkEmbedding(Base):
    """Workspace-scoped persisted embedding for one immutable evidence chunk."""

    __tablename__ = "evidence_chunk_embeddings"

    __table_args__ = (
        CheckConstraint(
            "embedding_dimension = 384",
            name="ck_evidence_chunk_embeddings_dimension",
        ),
        UniqueConstraint(
            "evidence_chunk_id",
            "evidence_content_hash",
            "workspace_id",
            "model_id",
            "model_version",
            "configuration_hash",
            name="uq_evidence_chunk_embeddings_generation",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    evidence_chunk_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "evidence_chunks.id",
            ondelete="CASCADE",
            name="fk_evidence_chunk_embeddings_chunk",
        ),
        index=True,
    )
    evidence_content_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "workspaces.id",
            ondelete="CASCADE",
            name="fk_evidence_chunk_embeddings_workspace",
        ),
        nullable=False,
        index=True,
    )
    model_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    model_version: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    configuration_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    embedding_dimension: Mapped[int] = mapped_column(
        nullable=False,
    )
    embedding: Mapped[list[float]] = mapped_column(
        Vector(384),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class EvidenceIngestionAttempt(Base):
    """Immutable provenance record for an evidence ingestion attempt."""

    __tablename__ = "evidence_ingestion_attempts"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        index=True,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("evidence_documents.id", ondelete="CASCADE"),
        index=True,
    )
    version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "evidence_document_versions.id",
            ondelete="CASCADE",
        ),
        index=True,
        nullable=True,
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )

    outcome: Mapped[str] = mapped_column(
        String(32),
        index=True,
        nullable=False,
    )
    error_code: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    original_filename: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(127))

    raw_sha256: Mapped[str] = mapped_column(
        String(64),
        index=True,
        nullable=False,
    )
    normalized_sha256: Mapped[str] = mapped_column(
        String(64),
        index=True,
        nullable=False,
    )
    normalization_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    raw_size_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    normalized_size_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
