"""Tests for token estimation"""

from markdown_ingress.core.tokens import TokenEstimator


def test_token_estimation():
    """Test basic token counting"""
    estimator = TokenEstimator(model="gpt-4")

    text = "This is a simple test."
    count = estimator.estimate(text)

    assert count > 0
    assert count < 20  # Should be small number


def test_token_savings():
    """Test savings calculation"""
    estimator = TokenEstimator(model="gpt-4")

    html = """
    <html>
    <head><title>Test</title></head>
    <body>
        <h1>Title</h1>
        <p>Content</p>
    </body>
    </html>
    """

    markdown = "# Title\n\nContent"

    savings = estimator.estimate_savings(html, markdown)

    assert savings["html_tokens"] > savings["markdown_tokens"]
    assert savings["saved_tokens"] > 0
    assert savings["savings_percent"] > 0


def test_different_models():
    """Test that different models work"""
    text = "Test content for token counting."

    gpt4_estimator = TokenEstimator(model="gpt-4")
    claude_estimator = TokenEstimator(model="claude")

    gpt4_count = gpt4_estimator.estimate(text)
    claude_count = claude_estimator.estimate(text)

    # Both should return counts (might be same due to same encoding)
    assert gpt4_count > 0
    assert claude_count > 0
