"""
Tests for retry_ingest functionality
"""

from unittest.mock import patch

import httpx
import pytest

from markdown_ingress import retry_ingest
from markdown_ingress.core.policy import PolicyBlockedError
from markdown_ingress.models import SafeDocument


@pytest.fixture
def mock_document():
    """Create a mock SafeDocument for testing"""
    return SafeDocument(
        markdown="# Test\n\nTest content",
        metadata={
            "url": "https://example.com",
            "title": "Test",
            "mode": "fast",
        },
        token_estimate=100,
        content_hash="abc123",
        injection_score=0.0,
        flags=[],
        removed_elements={},
    )


def test_retry_ingest_success_first_attempt(mock_document):
    """Test successful ingestion on first attempt"""
    with patch("markdown_ingress.api.ingest") as mock_ingest:
        mock_ingest.return_value = mock_document

        result = retry_ingest(
            url="https://example.com", mode="fast", max_retries=3, initial_timeout=60.0
        )

        # Verify metadata was added
        assert result.metadata["retry_attempts"] == 1
        assert not result.metadata["retry_enabled"]
        assert result.metadata["final_timeout"] == 60.0

        # Verify ingest was called once
        assert mock_ingest.call_count == 1


def test_retry_ingest_with_retry(mock_document):
    """Test retry logic when first attempt fails"""
    with patch("markdown_ingress.api.ingest") as mock_ingest:
        # First call fails, second succeeds
        mock_ingest.side_effect = [TimeoutError("Connection timeout"), mock_document]

        result = retry_ingest(
            url="https://example.com",
            mode="fast",
            max_retries=3,
            initial_timeout=60.0,
            enable_stealth=True,
        )

        # Verify retry metadata
        assert result.metadata["retry_attempts"] == 2
        assert result.metadata["retry_enabled"]
        assert result.metadata["final_timeout"] == 90.0

        # Verify ingest was called twice
        assert mock_ingest.call_count == 2


def test_retry_ingest_timeout_escalation(mock_document):
    """Test timeout escalation on retries"""
    with patch("markdown_ingress.api.ingest") as mock_ingest:
        # All attempts fail except last
        mock_ingest.side_effect = [
            TimeoutError("Timeout 1"),
            TimeoutError("Timeout 2"),
            mock_document,
        ]

        retry_ingest(url="https://example.com", max_retries=3, initial_timeout=60.0)

        # Verify timeout escalation
        calls = mock_ingest.call_args_list
        assert calls[0][1]["timeout"] == 60.0  # First attempt
        assert calls[1][1]["timeout"] == 90.0  # Second attempt
        assert calls[2][1]["timeout"] == 120.0  # Third attempt


def test_retry_ingest_timeout_escalation_respects_max_timeout(mock_document):
    with patch("markdown_ingress.api.ingest") as mock_ingest:
        mock_ingest.side_effect = [
            TimeoutError("Timeout 1"),
            TimeoutError("Timeout 2"),
            mock_document,
        ]

        retry_ingest(
            url="https://example.com",
            max_retries=3,
            initial_timeout=60.0,
            max_timeout=75.0,
        )

        calls = mock_ingest.call_args_list
        assert calls[0][1]["timeout"] == 60.0
        assert calls[1][1]["timeout"] == 75.0
        assert calls[2][1]["timeout"] == 75.0


def test_retry_ingest_rejects_max_timeout_below_initial_timeout():
    with pytest.raises(
        ValueError, match="max_timeout must be greater than or equal to initial_timeout"
    ):
        retry_ingest(
            url="https://example.com",
            max_retries=3,
            initial_timeout=60.0,
            max_timeout=10.0,
        )


def test_retry_ingest_all_retries_fail():
    """Test exception is raised when all retries fail"""
    with patch("markdown_ingress.api.ingest") as mock_ingest:
        # All calls fail
        mock_ingest.side_effect = TimeoutError("Connection timeout")

        with pytest.raises(TimeoutError):
            retry_ingest(url="https://example.com", max_retries=3, initial_timeout=60.0)

        # Verify all attempts were made
        assert mock_ingest.call_count == 3


def test_retry_ingest_non_retryable_error():
    """Test non-retryable errors fail immediately"""
    with patch("markdown_ingress.api.ingest") as mock_ingest:
        # Non-retryable error
        mock_ingest.side_effect = ValueError("Invalid input")

        with pytest.raises(ValueError):
            retry_ingest(url="https://example.com", max_retries=3)

        # Should fail on first attempt
        assert mock_ingest.call_count == 1


def test_retry_ingest_default_parameters(mock_document):
    """Test default parameter values"""
    with patch("markdown_ingress.api.ingest") as mock_ingest:
        mock_ingest.return_value = mock_document

        retry_ingest(url="https://example.com")

        # Verify defaults were used
        call_args = mock_ingest.call_args[1]
        assert call_args["mode"] == "auto"
        assert call_args["strict"]
        assert call_args["model"] == "gpt-4"
        assert call_args["timeout"] == 60.0


def test_retry_ingest_custom_parameters(mock_document):
    """Test custom parameter values"""
    with patch("markdown_ingress.api.ingest") as mock_ingest:
        mock_ingest.return_value = mock_document

        retry_ingest(
            url="https://example.com",
            mode="render",
            strict=False,
            model="gpt-3.5",
            max_retries=5,
            enable_stealth=False,
            initial_timeout=90.0,
        )

        # Verify custom values were passed
        call_args = mock_ingest.call_args[1]
        assert call_args["mode"] == "render"
        assert not call_args["strict"]
        assert call_args["model"] == "gpt-3.5"
        assert call_args["timeout"] == 90.0


def test_retry_ingest_retryable_errors(mock_document):
    """Test all retryable error types"""
    # TimeoutError is always retryable
    with patch("markdown_ingress.api.ingest") as mock_ingest:
        mock_ingest.side_effect = [TimeoutError("timeout"), mock_document]

        result = retry_ingest(url="https://example.com", max_retries=2)

        # Should retry and succeed on second attempt
        assert mock_ingest.call_count == 2
        assert result.metadata["retry_attempts"] == 2

    # Test custom exception types that are retryable
    # These need to have the error name in the type
    class ConnectTimeoutError(Exception):
        pass

    class ConnectError(Exception):
        pass

    with patch("markdown_ingress.api.ingest") as mock_ingest:
        mock_ingest.side_effect = [ConnectTimeoutError("timeout"), mock_document]

        result = retry_ingest(url="https://example.com", max_retries=2)

        # Should retry
        assert mock_ingest.call_count == 2


def test_retry_ingest_stealth_only_on_retry(mock_document):
    """Test stealth is only enabled on retry attempts"""
    with patch("markdown_ingress.api.ingest") as mock_ingest:
        # First fails, second succeeds
        mock_ingest.side_effect = [TimeoutError("timeout"), mock_document]

        result = retry_ingest(url="https://example.com", enable_stealth=True)

        # First attempt: stealth should be False
        assert result.metadata["retry_enabled"]
        # But it's only enabled on the second attempt
        assert result.metadata["retry_attempts"] == 2


def test_retry_ingest_retries_http_status_error(mock_document):
    request = httpx.Request("GET", "https://example.com")
    response = httpx.Response(503, request=request)

    with patch("markdown_ingress.api.ingest") as mock_ingest:
        mock_ingest.side_effect = [
            httpx.HTTPStatusError("server unavailable", request=request, response=response),
            mock_document,
        ]

        result = retry_ingest(url="https://example.com", max_retries=2)

        assert mock_ingest.call_count == 2
        assert result.metadata["retry_attempts"] == 2


def test_retry_ingest_does_not_retry_policy_block():
    blocked_doc = SafeDocument(
        markdown="# Blocked",
        metadata={"url": "https://example.com", "policy_action": "block"},
        token_estimate=1,
        content_hash="blocked",
        injection_score=1.0,
        flags=["policy_block"],
        removed_elements={},
    )

    with patch("markdown_ingress.api.ingest") as mock_ingest:
        mock_ingest.side_effect = PolicyBlockedError("Blocked by policy", document=blocked_doc)

        with pytest.raises(PolicyBlockedError):
            retry_ingest(url="https://example.com", max_retries=3)

        assert mock_ingest.call_count == 1
