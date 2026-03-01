"""
Tests for FastAPI server endpoints
"""

from unittest.mock import patch

from fastapi.testclient import TestClient

from markdown_ingress.api_server import app
from markdown_ingress.models import SafeDocument, SecurityReport

client = TestClient(app)


# Mock SafeDocument for testing
def create_mock_document():
    """Create a mock SafeDocument for testing"""
    return SafeDocument(
        markdown="# Example Domain\n\nThis domain is for use in illustrative examples.",
        metadata={
            "url": "https://example.com",
            "final_url": "https://example.com",
            "title": "Example Domain",
            "fetch_time_ms": 250.0,
            "status_code": 200,
            "model": "gpt-4",
            "mode": "fast",
            "strict": True,
            "token_savings": {"percentage_saved": 95.2},
            "risk_level": "LOW",
            "structural_hash": "sha256:def456",
            "retry_attempts": 1,
        },
        token_estimate=150,
        content_hash="sha256:abc123",
        injection_score=0.0,
        flags=[],
        removed_elements={"tags": {}, "hidden_elements": 0}
    )


def create_mock_security_report():
    """Create a mock SecurityReport for testing"""
    return SecurityReport(
        injection_score=0.0,
        risk_level="LOW",
        flags=[],
        hidden_content_detected=False,
        hidden_elements_count=0,
        url="https://example.com",
        title="Example Domain",
        token_estimate=150,
        token_reduction_percent=95.2,
        content_hash="sha256:abc123",
        structural_hash="sha256:def456",
        removed_elements={"tags": {}, "hidden_elements": 0}
    )


def test_root_endpoint():
    """Test root endpoint returns API info"""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["version"] == "0.7.0"
    assert "message" in data
    assert "endpoints" in data


def test_health_endpoint():
    """Test health check endpoint"""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["version"] == "0.7.0"
    assert data["service"] == "MarkDownIngress API"


@patch('markdown_ingress.api_server.ingest')
def test_ingest_endpoint_basic(mock_ingest):
    """Test basic ingestion endpoint with fast mode"""
    mock_ingest.return_value = create_mock_document()

    response = client.post(
        "/ingest",
        json={
            "url": "https://example.com",
            "mode": "fast",
            "strict": True,
            "timeout": 30
        }
    )
    assert response.status_code == 200
    data = response.json()

    # Check response structure
    assert "markdown" in data
    assert "metadata" in data
    assert "token_estimate" in data
    assert "injection_score" in data
    assert "flags" in data
    assert "content_hash" in data
    assert "removed_elements" in data

    # Check data types
    assert isinstance(data["markdown"], str)
    assert isinstance(data["metadata"], dict)
    assert isinstance(data["token_estimate"], int)
    assert isinstance(data["injection_score"], float)
    assert isinstance(data["flags"], list)
    assert isinstance(data["content_hash"], str)

    # Check injection score range
    assert 0.0 <= data["injection_score"] <= 1.0


@patch('markdown_ingress.api_server.ingest')
def test_ingest_endpoint_auto_mode(mock_ingest):
    """Test ingestion with auto mode"""
    mock_ingest.return_value = create_mock_document()

    response = client.post(
        "/ingest",
        json={
            "url": "https://example.com",
            "mode": "auto"
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert "markdown" in data
    assert data["metadata"]["mode"] in ["fast", "render"]


def test_ingest_endpoint_invalid_mode():
    """Test ingestion with invalid mode parameter"""
    response = client.post(
        "/ingest",
        json={
            "url": "https://example.com",
            "mode": "invalid_mode"
        }
    )
    assert response.status_code == 422  # Validation error


def test_ingest_endpoint_invalid_url():
    """Test ingestion with invalid URL"""
    response = client.post(
        "/ingest",
        json={
            "url": "not-a-valid-url",
            "mode": "fast"
        }
    )
    assert response.status_code == 422  # Validation error


@patch('markdown_ingress.api_server.ingest')
def test_ingest_endpoint_with_stealth(mock_ingest):
    """Test ingestion with stealth mode enabled"""
    mock_ingest.return_value = create_mock_document()

    response = client.post(
        "/ingest",
        json={
            "url": "https://example.com",
            "mode": "fast",
            "stealth": True
        }
    )
    assert response.status_code == 200


@patch('markdown_ingress.api_server.retry_ingest')
def test_retry_ingest_endpoint(mock_retry):
    """Test retry ingestion endpoint"""
    mock_retry.return_value = create_mock_document()

    response = client.post(
        "/ingest/retry",
        json={
            "url": "https://example.com",
            "mode": "fast",
            "max_retries": 2,
            "initial_timeout": 30.0
        }
    )
    assert response.status_code == 200
    data = response.json()

    # Should have retry metadata
    assert "metadata" in data
    assert "retry_attempts" in data["metadata"]
    assert data["metadata"]["retry_attempts"] >= 1


@patch('markdown_ingress.api_server.ingest')
def test_batch_ingest_endpoint(mock_ingest):
    """Test batch ingestion endpoint"""
    mock_ingest.return_value = create_mock_document()

    response = client.post(
        "/ingest/batch",
        json={
            "urls": [
                "https://example.com",
                "https://example.org"
            ],
            "mode": "fast",
            "timeout": 30
        }
    )
    assert response.status_code == 200
    data = response.json()

    # Check response structure
    assert "results" in data
    assert "success_count" in data
    assert "failure_count" in data

    # Check results
    assert isinstance(data["results"], list)
    assert len(data["results"]) == 2

    # Each result should have url and success fields
    for result in data["results"]:
        assert "url" in result
        assert "success" in result
        if result["success"]:
            assert "data" in result
        else:
            assert "error" in result


def test_batch_ingest_empty_urls():
    """Test batch ingestion with no URLs"""
    response = client.post(
        "/ingest/batch",
        json={
            "urls": [],
            "mode": "fast"
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success_count"] == 0
    assert data["failure_count"] == 0


def test_batch_ingest_too_many_urls():
    """Test batch ingestion exceeding max limit"""
    # Create 101 URLs (exceeds 100 limit)
    urls = [f"https://example{i}.com" for i in range(101)]
    response = client.post(
        "/ingest/batch",
        json={
            "urls": urls,
            "mode": "fast"
        }
    )
    assert response.status_code == 422  # Validation error


@patch('markdown_ingress.api_server.generate_security_report')
def test_security_report_endpoint(mock_report):
    """Test security report generation endpoint"""
    mock_report.return_value = create_mock_security_report()

    response = client.post(
        "/security/report",
        json={
            "url": "https://example.com",
            "mode": "fast",
            "strict": True,
            "timeout": 30
        }
    )
    assert response.status_code == 200
    data = response.json()

    # Check response structure
    assert "injection_score" in data
    assert "risk_level" in data
    assert "flags" in data
    assert "hidden_content_detected" in data
    assert "hidden_elements_count" in data
    assert "url" in data
    assert "title" in data
    assert "token_estimate" in data
    assert "token_reduction_percent" in data
    assert "content_hash" in data
    assert "structural_hash" in data
    assert "removed_elements" in data

    # Check data types
    assert isinstance(data["injection_score"], float)
    assert isinstance(data["risk_level"], str)
    assert isinstance(data["flags"], list)
    assert isinstance(data["hidden_content_detected"], bool)
    assert isinstance(data["hidden_elements_count"], int)

    # Check valid risk levels
    assert data["risk_level"] in ["LOW", "MEDIUM", "HIGH", "CRITICAL", "UNKNOWN"]


@patch('markdown_ingress.api_server.ingest')
def test_timeout_validation(mock_ingest):
    """Test timeout parameter validation"""
    mock_ingest.return_value = create_mock_document()

    # Too low
    response = client.post(
        "/ingest",
        json={
            "url": "https://example.com",
            "mode": "fast",
            "timeout": 0
        }
    )
    assert response.status_code == 422

    # Too high
    response = client.post(
        "/ingest",
        json={
            "url": "https://example.com",
            "mode": "fast",
            "timeout": 400
        }
    )
    assert response.status_code == 422

    # Valid range
    response = client.post(
        "/ingest",
        json={
            "url": "https://example.com",
            "mode": "fast",
            "timeout": 60
        }
    )
    assert response.status_code == 200


@patch('markdown_ingress.api_server.retry_ingest')
def test_max_retries_validation(mock_retry):
    """Test max_retries parameter validation"""
    mock_retry.return_value = create_mock_document()

    # Too low
    response = client.post(
        "/ingest/retry",
        json={
            "url": "https://example.com",
            "max_retries": 0
        }
    )
    assert response.status_code == 422

    # Too high
    response = client.post(
        "/ingest/retry",
        json={
            "url": "https://example.com",
            "max_retries": 15
        }
    )
    assert response.status_code == 422

    # Valid range
    response = client.post(
        "/ingest/retry",
        json={
            "url": "https://example.com",
            "max_retries": 3
        }
    )
    assert response.status_code == 200


def test_openapi_docs():
    """Test that OpenAPI docs are accessible"""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    openapi_schema = response.json()

    # Check basic OpenAPI structure
    assert "openapi" in openapi_schema
    assert "info" in openapi_schema
    assert "paths" in openapi_schema

    # Check API info
    assert openapi_schema["info"]["title"] == "MarkDownIngress API"
    assert openapi_schema["info"]["version"] == "0.7.0"

    # Check endpoints exist
    assert "/ingest" in openapi_schema["paths"]
    assert "/ingest/retry" in openapi_schema["paths"]
    assert "/ingest/batch" in openapi_schema["paths"]
    assert "/security/report" in openapi_schema["paths"]
    assert "/health" in openapi_schema["paths"]
