"""
Token estimation module using tiktoken
"""

import tiktoken


class TokenEstimator:
    """Estimate token counts for different LLM models"""

    # Model name mappings to tiktoken encodings
    MODEL_ENCODINGS = {
        "gpt-4": "cl100k_base",
        "gpt-4-turbo": "cl100k_base",
        "gpt-3.5-turbo": "cl100k_base",
        "claude": "cl100k_base",  # Approximation
        "claude-3": "cl100k_base",  # Approximation
        "text-davinci-003": "p50k_base",
    }

    def __init__(self, model: str = "gpt-4"):
        """
        Initialize token estimator.

        Args:
            model: Model name for token estimation
        """
        self.model = model
        self.encoding_name = self.MODEL_ENCODINGS.get(model, "cl100k_base")
        self.encoding = tiktoken.get_encoding(self.encoding_name)

    def estimate(self, text: str) -> int:
        """
        Estimate token count for text.

        Args:
            text: Text content to count

        Returns:
            Estimated token count
        """
        tokens = self.encoding.encode(text)
        return len(tokens)

    def estimate_savings(self, original_html: str, markdown: str) -> dict:
        """
        Calculate token savings from HTML to Markdown conversion.

        Args:
            original_html: Original HTML content
            markdown: Converted markdown content

        Returns:
            Dict with token counts and savings metrics
        """
        html_tokens = self.estimate(original_html)
        md_tokens = self.estimate(markdown)

        saved_tokens = max(0, html_tokens - md_tokens)
        savings_pct = (saved_tokens / html_tokens * 100) if html_tokens > 0 else 0

        return {
            "html_tokens": html_tokens,
            "markdown_tokens": md_tokens,
            "saved_tokens": saved_tokens,
            "savings_percent": round(savings_pct, 2),
        }
