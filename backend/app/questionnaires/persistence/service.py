"""Transactional persistence orchestration for questionnaire imports."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.audit import record_audit_event
from app.questionnaires.persistence.errors import (
    QuestionnaireNotFoundError,
    QuestionnairePersistenceError,
    QuestionnairePersistenceValidationError,
)
from app.questionnaires.persistence.models import (
    Questionnaire,
    QuestionnaireImportAttempt,
    QuestionnaireQuestion,
    QuestionnaireVersion,
    QuestionnaireVersionQuestion,
)
from app.questionnaires.persistence.repositories import (
    find_duplicate_version,
    find_question,
    find_questionnaire_by_name,
    lock_questionnaire,
    next_version_number,
)
from app.questionnaires.service import hash_questionnaire, normalize_source_question_id
from app.questionnaires.xlsx.types import XlsxImportResult


class ImportOutcome:
    """Stable persistence outcomes for questionnaire imports."""

    CREATED = "CREATED"
    IDENTICAL_DUPLICATE = "IDENTICAL_DUPLICATE"
    CANONICAL_DUPLICATE = "CANONICAL_DUPLICATE"


@dataclass(frozen=True, slots=True)
class QuestionnairePersistenceResult:
    """Result returned after the persistence transaction commits."""

    outcome: str
    questionnaire_id: uuid.UUID
    version_id: uuid.UUID
    version_number: int
    import_attempt_id: uuid.UUID


def _get_or_create_locked_questionnaire(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    name: str,
    actor_user_id: uuid.UUID,
) -> Questionnaire:
    """Resolve the questionnaire parent and serialize imports on its row."""

    existing = find_questionnaire_by_name(
        db,
        workspace_id=workspace_id,
        name=name,
    )
    if existing is not None:
        locked = lock_questionnaire(
            db,
            workspace_id=workspace_id,
            questionnaire_id=existing.id,
        )
        if locked is None:
            raise QuestionnaireNotFoundError("Questionnaire disappeared during lock")
        return locked

    try:
        with db.begin_nested():
            candidate = Questionnaire(
                workspace_id=workspace_id,
                name=name,
                created_by_user_id=actor_user_id,
            )
            db.add(candidate)
            db.flush()
    except IntegrityError:
        # Another concurrent transaction won the workspace+name race. The
        # outer transaction remains usable because only the savepoint rolls back.
        pass

    resolved = find_questionnaire_by_name(
        db,
        workspace_id=workspace_id,
        name=name,
    )
    if resolved is None:
        raise QuestionnairePersistenceError(
            "Unable to resolve questionnaire after concurrent creation race"
        )

    locked = lock_questionnaire(
        db,
        workspace_id=workspace_id,
        questionnaire_id=resolved.id,
    )
    if locked is None:
        raise QuestionnaireNotFoundError("Questionnaire disappeared during lock")
    return locked


def _validate_import_result(
    *,
    import_result: XlsxImportResult,
    raw_data: bytes,
) -> tuple[str, str]:
    """Validate that the parsed result still matches deterministic contracts."""

    questionnaire = import_result.questionnaire
    computed_canonical_hash = hash_questionnaire(questionnaire)

    if questionnaire.normalized_questionnaire_sha256 != computed_canonical_hash:
        raise QuestionnairePersistenceValidationError(
            "canonical questionnaire SHA-256 does not match the imported representation"
        )

    if not raw_data:
        raise QuestionnairePersistenceValidationError("raw questionnaire data must not be empty")

    raw_sha256 = hashlib.sha256(raw_data).hexdigest()

    if len(raw_sha256) != 64:
        raise QuestionnairePersistenceValidationError("raw questionnaire SHA-256 is invalid")

    if not questionnaire.parser_version:
        raise QuestionnairePersistenceValidationError(
            "questionnaire parser version must not be empty"
        )

    if not questionnaire.normalization_version:
        raise QuestionnairePersistenceValidationError(
            "questionnaire normalization version must not be empty"
        )

    if not questionnaire.question_identity_version:
        raise QuestionnairePersistenceValidationError(
            "questionnaire identity version must not be empty"
        )

    if not questionnaire.hash_version:
        raise QuestionnairePersistenceValidationError(
            "questionnaire canonical hash version must not be empty"
        )

    if len(questionnaire.questions) == 0:
        raise QuestionnairePersistenceValidationError("questionnaire must contain questions")

    return raw_sha256, computed_canonical_hash


def _get_or_create_question(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    questionnaire_id: uuid.UUID,
    question,
) -> QuestionnaireQuestion:
    """Resolve a logical question; its identity fields are immutable."""

    existing = find_question(
        db,
        workspace_id=workspace_id,
        questionnaire_id=questionnaire_id,
        question_id=question.question_id,
    )
    normalized_source_id = (
        normalize_source_question_id(question.source_question_id)
        if question.source_question_id is not None
        else None
    )

    if existing is not None:
        if (
            existing.identity_fingerprint != question.question_id
            or existing.identity_kind != question.identity_kind
            or existing.source_question_id != normalized_source_id
        ):
            raise QuestionnairePersistenceValidationError(
                f"question identity collision for question_id={question.question_id}"
            )
        return existing

    logical_question = QuestionnaireQuestion(
        workspace_id=workspace_id,
        questionnaire_id=questionnaire_id,
        question_id=question.question_id,
        identity_fingerprint=question.question_id,
        identity_kind=question.identity_kind,
        normalized_question_text=question.normalized_question_text,
        normalized_sheet_name=question.normalized_sheet_name,
        normalized_section_path=list(question.normalized_section_path),
        source_question_id=normalized_source_id,
    )
    db.add(logical_question)
    db.flush()
    return logical_question


def _build_attempt(
    *,
    workspace_id: uuid.UUID,
    questionnaire_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    outcome: str,
    questionnaire,
    raw_sha256: str,
    canonical_sha256: str,
    resulting_version_id: uuid.UUID | None,
    duplicate_of_version_id: uuid.UUID | None,
) -> QuestionnaireImportAttempt:
    """Build one immutable import provenance record."""

    return QuestionnaireImportAttempt(
        workspace_id=workspace_id,
        questionnaire_id=questionnaire_id,
        resulting_version_id=resulting_version_id,
        duplicate_of_version_id=duplicate_of_version_id,
        actor_user_id=actor_user_id,
        outcome=outcome,
        source_filename=questionnaire.source_filename,
        raw_sha256=raw_sha256,
        canonical_sha256=canonical_sha256,
        parser_version=questionnaire.parser_version,
        normalization_version=questionnaire.normalization_version,
        question_identity_version=questionnaire.question_identity_version,
        canonical_hash_version=questionnaire.hash_version,
        question_count=len(questionnaire.questions),
    )


def persist_import(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    import_result: XlsxImportResult,
    raw_data: bytes,
) -> QuestionnairePersistenceResult:
    """Persist one deterministic XLSX import atomically."""

    raw_sha256: str | None = None
    canonical_sha256: str | None = None

    try:
        raw_sha256, canonical_sha256 = _validate_import_result(
            import_result=import_result,
            raw_data=raw_data,
        )
        questionnaire = import_result.questionnaire

        parent = _get_or_create_locked_questionnaire(
            db,
            workspace_id=workspace_id,
            name=questionnaire.name,
            actor_user_id=actor_user_id,
        )

        duplicate = find_duplicate_version(
            db,
            workspace_id=workspace_id,
            questionnaire_id=parent.id,
            canonical_hash_version=questionnaire.hash_version,
            canonical_sha256=canonical_sha256,
        )

        if duplicate is not None:
            outcome = (
                ImportOutcome.IDENTICAL_DUPLICATE
                if duplicate.raw_sha256 == raw_sha256
                else ImportOutcome.CANONICAL_DUPLICATE
            )
            attempt = _build_attempt(
                workspace_id=workspace_id,
                questionnaire_id=parent.id,
                actor_user_id=actor_user_id,
                outcome=outcome,
                questionnaire=questionnaire,
                raw_sha256=raw_sha256,
                canonical_sha256=canonical_sha256,
                resulting_version_id=duplicate.id,
                duplicate_of_version_id=duplicate.id,
            )
            db.add(attempt)
            record_audit_event(
                db,
                actor_user_id=actor_user_id,
                workspace_id=workspace_id,
                action="questionnaire.import.duplicate",
                resource_type="questionnaire_version",
                resource_id=str(duplicate.id),
                metadata={
                    "questionnaire_id": str(parent.id),
                    "version_number": duplicate.version_number,
                    "outcome": outcome,
                    "raw_sha256": raw_sha256,
                    "canonical_sha256": canonical_sha256,
                    "canonical_hash_version": questionnaire.hash_version,
                },
            )
            db.commit()
            return QuestionnairePersistenceResult(
                outcome=outcome,
                questionnaire_id=parent.id,
                version_id=duplicate.id,
                version_number=duplicate.version_number,
                import_attempt_id=attempt.id,
            )

        version = QuestionnaireVersion(
            workspace_id=workspace_id,
            questionnaire_id=parent.id,
            version_number=next_version_number(
                db,
                workspace_id=workspace_id,
                questionnaire_id=parent.id,
            ),
            raw_sha256=raw_sha256,
            canonical_sha256=canonical_sha256,
            source_filename=questionnaire.source_filename,
            parser_version=questionnaire.parser_version,
            normalization_version=questionnaire.normalization_version,
            question_identity_version=questionnaire.question_identity_version,
            canonical_hash_version=questionnaire.hash_version,
            question_count=len(questionnaire.questions),
            created_by_user_id=actor_user_id,
        )
        db.add(version)
        db.flush()

        for question in questionnaire.questions:
            logical_question = _get_or_create_question(
                db,
                workspace_id=workspace_id,
                questionnaire_id=parent.id,
                question=question,
            )
            db.add(
                QuestionnaireVersionQuestion(
                    workspace_id=workspace_id,
                    questionnaire_id=parent.id,
                    questionnaire_version_id=version.id,
                    questionnaire_question_id=logical_question.id,
                    ordinal=question.ordinal,
                    source_row=question.source_row,
                    sheet_name=question.sheet_name,
                    section_path=list(question.section_path),
                    normalized_question_text=question.normalized_question_text,
                    source_question_id=(
                        normalize_source_question_id(question.source_question_id)
                        if question.source_question_id is not None
                        else None
                    ),
                )
            )

        attempt = _build_attempt(
            workspace_id=workspace_id,
            questionnaire_id=parent.id,
            actor_user_id=actor_user_id,
            outcome=ImportOutcome.CREATED,
            questionnaire=questionnaire,
            raw_sha256=raw_sha256,
            canonical_sha256=canonical_sha256,
            resulting_version_id=version.id,
            duplicate_of_version_id=None,
        )
        db.add(attempt)

        record_audit_event(
            db,
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            action="questionnaire.import.persisted",
            resource_type="questionnaire_version",
            resource_id=str(version.id),
            metadata={
                "questionnaire_id": str(parent.id),
                "version_number": version.version_number,
                "question_count": len(questionnaire.questions),
                "raw_sha256": raw_sha256,
                "canonical_sha256": canonical_sha256,
                "parser_version": questionnaire.parser_version,
                "normalization_version": questionnaire.normalization_version,
                "question_identity_version": questionnaire.question_identity_version,
                "canonical_hash_version": questionnaire.hash_version,
            },
        )

        db.commit()

        return QuestionnairePersistenceResult(
            outcome=ImportOutcome.CREATED,
            questionnaire_id=parent.id,
            version_id=version.id,
            version_number=version.version_number,
            import_attempt_id=attempt.id,
        )

    except QuestionnairePersistenceError:
        db.rollback()
        raise
    except SQLAlchemyError as exc:
        db.rollback()
        raise QuestionnairePersistenceError(
            "questionnaire import persistence failed"
        ) from exc
    except Exception:
        db.rollback()
        raise


def record_import_failure(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    source_filename: str,
    raw_sha256: str,
    parser_version: str = "xlsx-import-v1",
    normalization_version: str = "question-normalization-v1",
    question_identity_version: str = "question-identity-v2",
    canonical_hash_version: str = "questionnaire-hash-v1",
    question_count: int | None = None,
    error_code: str | None = None,
    error_detail: str | None = None,
) -> uuid.UUID:
    """Record a failed import attempt after its domain transaction has rolled back."""

    attempt = QuestionnaireImportAttempt(
        workspace_id=workspace_id,
        questionnaire_id=None,
        resulting_version_id=None,
        duplicate_of_version_id=None,
        actor_user_id=actor_user_id,
        outcome="FAILED",
        source_filename=source_filename,
        raw_sha256=raw_sha256,
        canonical_sha256=None,
        parser_version=parser_version,
        normalization_version=normalization_version,
        question_identity_version=question_identity_version,
        canonical_hash_version=canonical_hash_version,
        question_count=question_count,
        error_code=error_code,
        error_detail=error_detail,
    )
    db.add(attempt)
    db.commit()
    return attempt.id


__all__ = [
    "ImportOutcome",
    "QuestionnairePersistenceResult",
    "persist_import",
    "record_import_failure",
]
