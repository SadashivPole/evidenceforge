"""Tests and measurable output for the deterministic lexical retrieval baseline."""

from __future__ import annotations

import hashlib
import math
import uuid

from sqlalchemy.orm import Session

from app.evaluation.retrieval import (
    EvaluationCategory,
    evaluate_case,
    evaluate_cases,
    format_report,
    ndcg_at_k,
    report_to_dict,
    support_recall_at_5,
)
from app.evidence.ingestion.types import IngestionResult
from app.evidence.persistence import persist_ingestion
from app.evidence.persistence.search_repository import list_search_candidates
from app.evidence.search.service import search_chunks
from app.evidence.search.types import SearchChunkCandidate
from app.models import EvidenceDocument, User, Workspace
from tests.evaluation.retrieval_cases import (
    MAIN_WORKSPACE_ID,
    OTHER_WORKSPACE_ID,
    load_evaluation_cases,
)


def _metric_id(number: int) -> uuid.UUID:
    return uuid.UUID(f"00000000-0000-0000-0000-{number:012x}")


def _make_ingestion(text: str) -> IngestionResult:
    normalized_bytes = text.encode("utf-8")
    digest = hashlib.sha256(normalized_bytes).hexdigest()
    return IngestionResult(
        original_filename="evaluation-isolation.md",
        extension=".md",
        media_type="text/markdown",
        raw_size_bytes=len(normalized_bytes),
        normalized_size_bytes=len(normalized_bytes),
        raw_sha256=digest,
        normalized_sha256=digest,
        normalization_version="text-v1",
        normalized_text=text,
    )


def _persist_text(
    db_session: Session,
    *,
    workspace: Workspace,
    user: User,
    document: EvidenceDocument,
    text: str,
) -> None:
    persist_ingestion(
        db_session,
        workspace_id=workspace.id,
        actor_user_id=user.id,
        document_id=document.id,
        ingestion_result=_make_ingestion(text),
    )


def test_fixture_loading_is_stable_and_covers_all_categories() -> None:
    cases = load_evaluation_cases()

    assert len(cases) == 22
    assert [case.case_id for case in cases] == [case.case_id for case in load_evaluation_cases()]
    assert {case.category for case in cases} == set(EvaluationCategory)
    assert all(case.candidates for case in cases)
    assert all(case.gold_relevance for case in cases)


def test_support_recall_at_5_counts_only_supporting_grades() -> None:
    grade_three = _metric_id(1)
    grade_two = _metric_id(2)
    grade_one = _metric_id(3)
    grade_zero = _metric_id(4)
    gold = {
        grade_three: 3,
        grade_two: 2,
        grade_one: 1,
        grade_zero: 0,
    }

    assert support_recall_at_5((grade_three,), gold) == 1
    assert support_recall_at_5((grade_two,), gold) == 1
    assert support_recall_at_5((grade_one,), gold) == 0
    assert support_recall_at_5((grade_zero,), gold) == 0
    assert support_recall_at_5((grade_one, grade_zero), gold) == 0


def test_ndcg_at_5_uses_graded_relevance() -> None:
    first = _metric_id(1)
    second = _metric_id(2)
    third = _metric_id(3)
    gold = {first: 3, second: 2, third: 1}

    observed = ndcg_at_k((second, first, third), gold, k=5)
    ideal = (7 / math.log2(2)) + (3 / math.log2(3)) + (1 / math.log2(4))
    expected = ((3 / math.log2(2)) + (7 / math.log2(3)) + (1 / math.log2(4))) / ideal

    assert math.isclose(observed, expected)


def test_ndcg_at_10_uses_results_beyond_five() -> None:
    candidate_ids = tuple(_metric_id(number) for number in range(1, 12))
    gold = {candidate_id: 3 for candidate_id in candidate_ids}

    assert ndcg_at_k(candidate_ids[:5], gold, k=10) < 1.0
    assert math.isclose(ndcg_at_k(candidate_ids, gold, k=10), 1.0)


def test_zero_gain_case_has_zero_ndcg() -> None:
    candidate_id = _metric_id(1)

    assert ndcg_at_k((candidate_id,), {candidate_id: 0}, k=5) == 0.0


def test_multiple_relevant_candidates_are_retained_in_results() -> None:
    case = load_evaluation_cases()[5]
    result = evaluate_case(case)

    relevant_ids = {label.candidate_id for label in case.gold_relevance if label.relevance > 0}
    observed_relevant_ids = set(result.retrieved_candidate_ids) & relevant_ids

    assert len(observed_relevant_ids) >= 2
    assert result.recall_at_5 == 1


def test_existing_lexical_ordering_is_deterministic() -> None:
    candidate_a = SearchChunkCandidate(
        chunk_id=_metric_id(1),
        document_id=uuid.UUID("10000000-0000-0000-0000-000000000001"),
        version_id=uuid.UUID("20000000-0000-0000-0000-000000000001"),
        version_number=1,
        chunk_index=0,
        content="MFA is required for privileged access.",
        content_hash="a" * 64,
        normalized_start_byte=0,
        normalized_end_byte=39,
        section_label="Access",
        page_number=None,
    )
    candidate_b = SearchChunkCandidate(
        chunk_id=_metric_id(2),
        document_id=uuid.UUID("10000000-0000-0000-0000-000000000002"),
        version_id=uuid.UUID("20000000-0000-0000-0000-000000000002"),
        version_number=1,
        chunk_index=0,
        content="MFA is required for privileged access.",
        content_hash="b" * 64,
        normalized_start_byte=0,
        normalized_end_byte=39,
        section_label="Access",
        page_number=None,
    )

    first_run = search_chunks((candidate_b, candidate_a), "MFA is required")
    second_run = search_chunks((candidate_b, candidate_a), "MFA is required")

    assert first_run == second_run
    assert [result.candidate.document_id for result in first_run] == [
        candidate_a.document_id,
        candidate_b.document_id,
    ]


def test_category_aggregation_is_deterministic() -> None:
    report = evaluate_cases(load_evaluation_cases())

    assert report.case_count == 22
    assert sum(metrics.case_count for metrics in report.category_breakdown) == 22
    assert [metrics.category for metrics in report.category_breakdown] == list(EvaluationCategory)
    assert all(0.0 <= metrics.macro_recall_at_5 <= 1.0 for metrics in report.category_breakdown)
    insufficient = next(
        metrics
        for metrics in report.category_breakdown
        if metrics.category == EvaluationCategory.INSUFFICIENT_EVIDENCE
    )
    assert insufficient.macro_recall_at_5 == 0.0


def test_evaluation_is_reproducible() -> None:
    cases = load_evaluation_cases()

    first = report_to_dict(evaluate_cases(cases))
    second = report_to_dict(evaluate_cases(cases))

    assert first == second
    assert format_report(evaluate_cases(cases)) == format_report(evaluate_cases(cases))


def test_cross_workspace_case_excludes_ineligible_evidence() -> None:
    case = load_evaluation_cases()[6]
    other_workspace_ids = {
        item.candidate_id for item in case.candidates if item.workspace_id == OTHER_WORKSPACE_ID
    }
    result = evaluate_case(case)

    assert case.workspace_id == MAIN_WORKSPACE_ID
    assert other_workspace_ids
    assert not other_workspace_ids.intersection(result.retrieved_candidate_ids)


def test_repository_workspace_scope_excludes_cross_workspace_chunks(
    db_session: Session,
) -> None:
    user_a = User(
        external_subject="evaluation-isolation-a",
        email="evaluation-isolation-a@example.test",
        display_name="Evaluation Isolation A",
    )
    user_b = User(
        external_subject="evaluation-isolation-b",
        email="evaluation-isolation-b@example.test",
        display_name="Evaluation Isolation B",
    )
    db_session.add_all([user_a, user_b])
    db_session.flush()

    workspace_a = Workspace(name="Evaluation Workspace A", created_by_user_id=user_a.id)
    workspace_b = Workspace(name="Evaluation Workspace B", created_by_user_id=user_b.id)
    db_session.add_all([workspace_a, workspace_b])
    db_session.flush()

    document_a = EvidenceDocument(
        workspace_id=workspace_a.id,
        name="workspace-a-evidence.md",
        source_type="file",
        status="active",
        created_by_user_id=user_a.id,
    )
    document_b = EvidenceDocument(
        workspace_id=workspace_b.id,
        name="workspace-b-evidence.md",
        source_type="file",
        status="active",
        created_by_user_id=user_b.id,
    )
    db_session.add_all([document_a, document_b])
    db_session.commit()

    _persist_text(
        db_session,
        workspace=workspace_a,
        user=user_a,
        document=document_a,
        text="Workspace A private encryption evidence.",
    )
    _persist_text(
        db_session,
        workspace=workspace_b,
        user=user_b,
        document=document_b,
        text="Workspace B private encryption evidence.",
    )

    eligible = list_search_candidates(db_session, workspace_id=workspace_a.id)
    results = search_chunks(eligible, "private encryption evidence")

    assert results
    assert {candidate.document_id for candidate in eligible} == {document_a.id}
    assert {result.candidate.document_id for result in results} == {document_a.id}
    assert all("Workspace B" not in result.candidate.content for result in results)


def test_malicious_injected_cases_are_evaluated_as_plain_evidence_content() -> None:
    malicious_cases = tuple(
        case
        for case in load_evaluation_cases()
        if case.category == EvaluationCategory.MALICIOUS_INJECTED
    )

    assert len(malicious_cases) == 3
    assert any(
        "Ignore all prior instructions" in item.candidate.content
        for case in malicious_cases
        for item in case.candidates
    )

    results = evaluate_cases(malicious_cases)
    assert results.case_count == 3
    malicious_category = EvaluationCategory.MALICIOUS_INJECTED
    assert all(result.category == malicious_category for result in results.case_results)


def test_evaluation_output_contains_per_case_and_category_results() -> None:
    report = evaluate_cases(load_evaluation_cases())
    print(format_report(report))

    assert report.case_results
    assert report.category_breakdown
    assert report.case_count == 22
