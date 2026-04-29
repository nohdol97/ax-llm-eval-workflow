"""Reviewer-curated dataset helper 단위 테스트."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.core.errors import LangfuseError
from app.services.dataset_service import (
    REVIEWER_CURATED_SUFFIX,
    add_reviewer_curated_item,
    reviewer_curated_dataset_name,
)


@pytest.mark.unit
class TestReviewerCuratedDatasetName:
    @pytest.mark.parametrize(
        ("agent_name", "expected"),
        [
            ("qa-agent-v3", f"qa-agent-v3{REVIEWER_CURATED_SUFFIX}"),
            ("qa agent v3", f"qa-agent-v3{REVIEWER_CURATED_SUFFIX}"),
            ("", f"unknown-agent{REVIEWER_CURATED_SUFFIX}"),
            (None, f"unknown-agent{REVIEWER_CURATED_SUFFIX}"),
            ("  ", f"unknown-agent{REVIEWER_CURATED_SUFFIX}"),
            ("--evil--", f"evil{REVIEWER_CURATED_SUFFIX}"),
            ("a__b", f"a__b{REVIEWER_CURATED_SUFFIX}"),
            ("a@b", f"a-b{REVIEWER_CURATED_SUFFIX}"),
            ("a---b", f"a-b{REVIEWER_CURATED_SUFFIX}"),
            (" qa@agent__v3 ", f"qa-agent__v3{REVIEWER_CURATED_SUFFIX}"),
        ],
    )
    def test_normalizes_dataset_name(self, agent_name: str | None, expected: str) -> None:
        assert reviewer_curated_dataset_name(agent_name) == expected


@pytest.mark.unit
class TestAddReviewerCuratedItem:
    def test_creates_dataset_when_missing(self) -> None:
        langfuse = MagicMock()

        result = add_reviewer_curated_item(
            langfuse,
            agent_name="qa-agent-v3",
            trace_input={"question": "hello"},
            expected_output={"answer": "world"},
            metadata={"review_id": "review-1"},
        )

        assert result == "qa-agent-v3-reviewer-curated"
        langfuse.create_dataset.assert_called_once()

    def test_create_dataset_item_receives_expected_arguments(self) -> None:
        langfuse = MagicMock()

        add_reviewer_curated_item(
            langfuse,
            agent_name="qa-agent-v3",
            trace_input={"input": "x"},
            expected_output={"output": "y"},
            metadata={"review_id": "review-1"},
        )

        langfuse.create_dataset_item.assert_called_once_with(
            dataset_name="qa-agent-v3-reviewer-curated",
            input={"input": "x"},
            expected_output={"output": "y"},
            metadata={"review_id": "review-1", "source": "reviewer_curated"},
        )

    def test_metadata_source_is_added_automatically(self) -> None:
        langfuse = MagicMock()

        add_reviewer_curated_item(
            langfuse,
            agent_name="qa-agent-v3",
            trace_input={"input": "x"},
            expected_output=None,
            metadata={"review_id": "review-1"},
        )

        assert (
            langfuse.create_dataset_item.call_args.kwargs["metadata"]["source"]
            == "reviewer_curated"
        )

    def test_explicit_metadata_source_is_preserved(self) -> None:
        langfuse = MagicMock()

        add_reviewer_curated_item(
            langfuse,
            agent_name="qa-agent-v3",
            trace_input={"input": "x"},
            expected_output=None,
            metadata={"source": "custom-source", "review_id": "review-1"},
        )

        assert (
            langfuse.create_dataset_item.call_args.kwargs["metadata"]["source"] == "custom-source"
        )

    def test_returns_dataset_name(self) -> None:
        langfuse = MagicMock()

        result = add_reviewer_curated_item(
            langfuse,
            agent_name="qa agent v3",
            trace_input={"input": "x"},
            expected_output="gold",
            metadata=None,
        )

        assert result == "qa-agent-v3-reviewer-curated"

    def test_langfuse_error_on_create_dataset_is_swallowed_and_item_is_still_added(self) -> None:
        langfuse = MagicMock()
        langfuse.create_dataset.side_effect = LangfuseError(detail="dataset already exists")

        result = add_reviewer_curated_item(
            langfuse,
            agent_name="qa-agent-v3",
            trace_input={"input": "x"},
            expected_output="gold",
            metadata={"review_id": "review-1"},
        )

        assert result == "qa-agent-v3-reviewer-curated"
        langfuse.create_dataset.assert_called_once()
        langfuse.create_dataset_item.assert_called_once()

    def test_create_dataset_receives_expected_description_and_metadata(self) -> None:
        langfuse = MagicMock()

        add_reviewer_curated_item(
            langfuse,
            agent_name="qa-agent-v3",
            trace_input={"input": "x"},
            expected_output="gold",
            metadata=None,
        )

        langfuse.create_dataset.assert_called_once_with(
            name="qa-agent-v3-reviewer-curated",
            description="Reviewer 가 add_to_dataset 결정한 trace 누적 (agent=qa-agent-v3)",
            metadata={"source": "reviewer-curated"},
        )
