"""Workspace-scoped questionnaire import routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.api.schemas import QuestionnaireImportResponse
from app.audit import record_audit_event
from app.auth import WorkspaceContext, assert_workspace_role, get_workspace_context
from app.db import get_db
from app.models import WorkspaceRole
from app.questionnaires.xlsx.errors import (
    UnsupportedXlsxExtensionError,
    XlsxFileTooLargeError,
    XlsxImportError,
)
from app.questionnaires.xlsx.policy import XlsxImportPolicy
from app.questionnaires.xlsx.service import import_xlsx

router = APIRouter(prefix="/workspaces", tags=["questionnaires"])

DbSession = Annotated[Session, Depends(get_db)]
WorkspaceAccess = Annotated[WorkspaceContext, Depends(get_workspace_context)]

MAX_IMPORT_READ_BYTES = XlsxImportPolicy().max_file_size_bytes + 1


def _import_http_error(exc: XlsxImportError) -> HTTPException:
    """Map deterministic XLSX import failures to safe HTTP responses."""

    if isinstance(exc, XlsxFileTooLargeError):
        return HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Questionnaire file exceeds the maximum permitted size",
        )

    if isinstance(exc, UnsupportedXlsxExtensionError):
        return HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Questionnaire file must be an XLSX workbook",
        )

    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail="Questionnaire workbook failed deterministic validation",
    )


@router.post(
    "/{workspace_id}/questionnaires/import",
    response_model=QuestionnaireImportResponse,
    status_code=status.HTTP_200_OK,
)
def import_questionnaire(
    context: WorkspaceAccess,
    db: DbSession,
    file: Annotated[UploadFile, File(...)],
) -> QuestionnaireImportResponse:
    """Parse an XLSX questionnaire without persisting or generating answers."""

    assert_workspace_role(
        context,
        db,
        WorkspaceRole.OWNER,
        WorkspaceRole.ADMIN,
        WorkspaceRole.MEMBER,
    )

    filename = file.filename or ""

    try:
        raw_bytes = file.file.read(MAX_IMPORT_READ_BYTES)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unable to read questionnaire file",
        ) from exc
    finally:
        file.file.close()

    try:
        result = import_xlsx(
            data=raw_bytes,
            filename=filename,
        )
    except XlsxImportError as exc:
        raise _import_http_error(exc) from exc

    record_audit_event(
        db,
        actor_user_id=context.user.id,
        workspace_id=context.workspace.id,
        action="questionnaire.import.previewed",
        resource_type="questionnaire",
        resource_id=str(result.questionnaire.questionnaire_id),
        metadata={
            "filename": filename,
            "questionnaire_name": result.questionnaire.name,
            "question_count": len(result.questionnaire.questions),
            "imported_sheet_count": len(result.imported_sheets),
            "ignored_sheet_count": len(result.ignored_sheets),
            "parser_version": result.questionnaire.parser_version,
            "normalization_version": result.questionnaire.normalization_version,
            "question_identity_version": result.questionnaire.question_identity_version,
            "hash_version": result.questionnaire.hash_version,
            "normalized_questionnaire_sha256": (
                result.questionnaire.normalized_questionnaire_sha256
            ),
        },
    )
    db.commit()

    return QuestionnaireImportResponse(
        questionnaire_id=result.questionnaire.questionnaire_id,
        name=result.questionnaire.name,
        source_filename=result.questionnaire.source_filename,
        status=result.questionnaire.status.value,
        parser_version=result.questionnaire.parser_version,
        normalization_version=result.questionnaire.normalization_version,
        question_identity_version=result.questionnaire.question_identity_version,
        hash_version=result.questionnaire.hash_version,
        normalized_questionnaire_sha256=(result.questionnaire.normalized_questionnaire_sha256),
        questions=[
            {
                "question_id": question.question_id,
                "identity_kind": question.identity_kind,
                "source_question_id": question.source_question_id,
                "ordinal": question.ordinal,
                "sheet_name": question.sheet_name,
                "normalized_sheet_name": question.normalized_sheet_name,
                "source_row": question.source_row,
                "question_text": question.question_text,
                "normalized_question_text": question.normalized_question_text,
                "section_path": list(question.section_path),
                "normalized_section_path": list(question.normalized_section_path),
            }
            for question in result.questionnaire.questions
        ],
        imported_sheets=[
            {
                "sheet_name": sheet.sheet_name,
                "header_row": sheet.header_row,
                "question_header": sheet.question_header,
                "section_header": sheet.section_header,
                "question_count": sheet.question_count,
            }
            for sheet in result.imported_sheets
        ],
        ignored_sheets=[
            {
                "sheet_name": sheet.sheet_name,
                "reason": sheet.reason,
            }
            for sheet in result.ignored_sheets
        ],
    )


__all__ = ["router"]
