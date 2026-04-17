"""Token estimation adapter using tiktoken."""

import tiktoken

from markdown_ingress.core.interfaces import ITokenEstimator


class TokenEstimator(ITokenEstimator):
    """Estimate token counts for different LLM models using tiktoken."""

    MODEL_ENCODINGS = {
        "gpt-4": "cl100k_base",
        "gpt-4-turbo": "cl100k_base",
        "gpt-3.5-turbo": "cl100k_base",
        "claude": "cl100k_base",
        "claude-3": "cl100k_base",
        "text-davinci-003": "p50k_base",
    }

    def __init__(self, model: str = "gpt-4"):
        self.model = model
        self.encoding_name = self.MODEL_ENCODINGS.get(model, "cl100k_base")
        self.encoding = tiktoken.get_encoding(self.encoding_name)

    def estimate(self, text: str) -> int:
        tokens = self.encoding.encode(text)
        return len(tokens)

    def estimate_savings(self, original_html: str, markdown: str) -> dict:
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
