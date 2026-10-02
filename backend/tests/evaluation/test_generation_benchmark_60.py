"""Tests for the Phase 2E.4B 60-case generation benchmark projection."""

from __future__ import annotations

import uuid
from collections import Counter

from generation_benchmark_60 import (
    EXPECTED_CATEGORY_COUNTS,
    load_generation_benchmark_60,
)

from app.evaluation.generation import GenerationBenchmarkCategory
from app.questionnaires.types import ResponseStatus


def test_authoritative_generation_benchmark_has_60_cases() -> None:
    cases = load_generation_benchmark_60()

    assert len(cases) == 60
    assert Counter(case.category for case in cases) == Counter(EXPECTED_CATEGORY_COUNTS)


def test_case_order_and_ids_are_stable() -> None:
    cases = load_generation_benchmark_60()

    assert cases[0].case_id == "supported-exact-mfa"
    assert cases[-1].case_id == "malicious-injected-cross-workspace-distractor"
    assert len({case.case_id for case in cases}) == 60


def test_only_authorized_workspace_evidence_is_projected() -> None:
    cases = load_generation_benchmark_60()

    cross_workspace_case = next(
        case for case in cases if case.case_id == "supported-cross-workspace-distractor"
    )
    assert len(cross_workspace_case.model_input.evidence_items) == 2
    assert [item.citation_handle for item in cross_workspace_case.model_input.evidence_items] == [
        "EVIDENCE-1",
        "EVIDENCE-2",
    ]
    assert "OTHER_WORKSPACE_ID" not in str(cross_workspace_case.model_input)


def test_projection_never_exposes_raw_uuid_fields() -> None:
    cases = load_generation_benchmark_60()

    for case in cases:
        assert not hasattr(case.model_input, "workspace_id")
        assert not hasattr(case.model_input, "question_id")
        for item in case.model_input.evidence_items:
            assert not hasattr(item, "evidence_chunk_id")
            assert not hasattr(item, "document_id")
            assert not hasattr(item, "version_id")
            assert not hasattr(item, "conflict_group_id")
        assert not any(isinstance(value, uuid.UUID) for value in case.model_input.evidence_items)


def test_generation_gold_policy_by_category() -> None:
    cases = load_generation_benchmark_60()

    for case in cases:
        if case.category in {
            GenerationBenchmarkCategory.SUPPORTED,
            GenerationBenchmarkCategory.MALICIOUS_INJECTED,
        }:
            assert case.expected_status == ResponseStatus.PROPOSED
            assert case.is_abstention_expected is False
            assert case.expected_citations
        else:
            assert case.expected_status == ResponseStatus.INSUFFICIENT_EVIDENCE
            assert case.is_abstention_expected is True
            assert case.expected_citations == ()


def test_supported_and_malicious_gold_citations_use_direct_support_only() -> None:
    cases = load_generation_benchmark_60()

    for case in cases:
        if case.category not in {
            GenerationBenchmarkCategory.SUPPORTED,
            GenerationBenchmarkCategory.MALICIOUS_INJECTED,
        }:
            continue
        handles = set(item.citation_handle for item in case.model_input.evidence_items)
        assert set(case.expected_citations).issubset(handles)
        assert all(handle.startswith("EVIDENCE-") for handle in case.expected_citations)


def test_malicious_cases_keep_injected_text_as_evidence_data() -> None:
    cases = load_generation_benchmark_60()
    malicious = [
        case for case in cases if case.category == GenerationBenchmarkCategory.MALICIOUS_INJECTED
    ]

    assert len(malicious) == 10
    assert all(
        any(
            marker.casefold() in item.content.casefold()
            for item in case.model_input.evidence_items
            for marker in (
                "ignore all prior instructions",
                "ignore the reviewer",
                "system message",
                "system override",
                "reveal secrets",
            )
        )
        for case in malicious
    )
