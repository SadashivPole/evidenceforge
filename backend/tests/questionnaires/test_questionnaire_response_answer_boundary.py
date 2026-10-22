"""Boundary tests: the response PUT/upsert endpoint enforces the 4,000-char answer limit.

Covers the gap where ``QuestionnaireResponseUpsert.answer`` accepted answers of any
length even though the generation-input schemas cap answers at
``MAX_ANSWER_CHARACTERS`` (4000). These tests reach the real route-level schema,
not just the generation-input model or a helper.
"""

from __future__ import annotations

from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from app.questionnaires.generation.config import MAX_ANSWER_CHARACTERS
from tests.conftest import auth_headers, create_principal, create_workspace_with_owner
from tests.questionnaires.test_questionnaire_responses import _import_one_question

_STATUS = "NEEDS_REVIEW"


def _response_path(workspace_id: object, version: object, version_question: object) -> str:
    return (
        f"/workspaces/{workspace_id}/questionnaires/{version.questionnaire_id}"
        f"/versions/{version.id}/questions/{version_question.id}/response"
    )


def _setup(db_session: Session):
    owner = create_principal(
        db_session,
        email="answer-boundary@example.invalid",
        display_name="Answer Boundary Owner",
    )
    workspace = create_workspace_with_owner(db_session, owner, name="Answer Boundary WS")
    version, version_question = _import_one_question(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=owner.user.id,
    )
    db_session.commit()
    return owner, workspace, version, version_question


def test_answer_limits_accept_3999_and_4000_chars(client: TestClient, db_session: Session) -> None:
    owner, workspace, version, version_question = _setup(db_session)
    path = _response_path(workspace.id, version, version_question)

    first = client.put(
        path,
        headers=auth_headers(owner),
        json={
            "answer": "A" * (MAX_ANSWER_CHARACTERS - 1),
            "status": _STATUS,
            "citation_chunk_ids": [],
        },
    )
    assert first.status_code == 200
    assert first.json()["current_revision"]["revision_number"] == 1

    exact = client.put(
        path,
        headers=auth_headers(owner),
        json={
            "answer": "B" * MAX_ANSWER_CHARACTERS,
            "status": _STATUS,
            "citation_chunk_ids": [],
        },
    )
    assert exact.status_code == 200
    assert exact.json()["current_revision"]["revision_number"] == 2


def test_answer_over_limit_rejected_and_persists_nothing(
    client: TestClient, db_session: Session
) -> None:
    owner, workspace, version, version_question = _setup(db_session)
    path = _response_path(workspace.id, version, version_question)

    rejected = client.put(
        path,
        headers=auth_headers(owner),
        json={
            "answer": "C" * (MAX_ANSWER_CHARACTERS + 1),
            "status": _STATUS,
            "citation_chunk_ids": [],
        },
    )
    assert rejected.status_code == 422
    assert "answer" in rejected.text

    # The rejection is a validation failure: no response row, no revision, no history.
    head = client.get(path, headers=auth_headers(owner))
    assert head.status_code == 404
    history = client.get(f"{path}/history", headers=auth_headers(owner))
    assert history.status_code == 404


def test_oversized_update_leaves_existing_response_unchanged(
    client: TestClient, db_session: Session
) -> None:
    owner, workspace, version, version_question = _setup(db_session)
    path = _response_path(workspace.id, version, version_question)

    valid = client.put(
        path,
        headers=auth_headers(owner),
        json={"answer": "Valid synthetic draft.", "status": _STATUS, "citation_chunk_ids": []},
    )
    assert valid.status_code == 200
    assert valid.json()["current_revision"]["revision_number"] == 1

    # Distinct oversized string so idempotency cannot mask a persisted revision.
    rejected = client.put(
        path,
        headers=auth_headers(owner),
        json={
            "answer": "D" * (MAX_ANSWER_CHARACTERS + 1),
            "status": _STATUS,
            "citation_chunk_ids": [],
        },
    )
    assert rejected.status_code == 422

    current = client.get(path, headers=auth_headers(owner))
    assert current.status_code == 200
    assert current.json()["current_revision"]["revision_number"] == 1
    assert current.json()["current_revision"]["answer"] == "Valid synthetic draft."

    history = client.get(f"{path}/history", headers=auth_headers(owner))
    assert history.status_code == 200
    assert [item["revision_number"] for item in history.json()] == [1]


def test_null_and_omitted_answer_preserve_existing_contract(
    client: TestClient, db_session: Session
) -> None:
    # Existing contract: null answer is only valid for the null-answer statuses
    # (INSUFFICIENT_EVIDENCE / NOT_APPLICABLE / DO_NOT_DISCLOSE); other statuses
    # reject null at the service layer. Our schema change must not alter either side.
    owner, workspace, version, version_question = _setup(db_session)
    path = _response_path(workspace.id, version, version_question)

    omitted = client.put(
        path,
        headers=auth_headers(owner),
        json={"status": "NOT_APPLICABLE", "citation_chunk_ids": []},
    )
    assert omitted.status_code == 200
    assert omitted.json()["current_revision"]["revision_number"] == 1

    explicit_none = client.put(
        path,
        headers=auth_headers(owner),
        json={"answer": None, "status": "DO_NOT_DISCLOSE", "citation_chunk_ids": []},
    )
    assert explicit_none.status_code == 200
    assert explicit_none.json()["current_revision"]["revision_number"] == 2

    null_not_allowed = client.put(
        path,
        headers=auth_headers(owner),
        json={"answer": None, "status": _STATUS, "citation_chunk_ids": []},
    )
    assert null_not_allowed.status_code == 422


def test_identical_repeat_update_remains_idempotent(
    client: TestClient, db_session: Session
) -> None:
    owner, workspace, version, version_question = _setup(db_session)
    path = _response_path(workspace.id, version, version_question)
    payload = {
        "answer": "E" * MAX_ANSWER_CHARACTERS,
        "status": _STATUS,
        "citation_chunk_ids": [],
    }

    first = client.put(path, headers=auth_headers(owner), json=payload)
    assert first.status_code == 200
    repeat = client.put(path, headers=auth_headers(owner), json=payload)
    assert repeat.status_code == 200
    assert repeat.json()["current_revision"]["revision_number"] == 1

    history = client.get(f"{path}/history", headers=auth_headers(owner))
    assert [item["revision_number"] for item in history.json()] == [1]
