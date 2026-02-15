"""
Main API for MarkDownIngress
"""

from typing import Literal, Optional
from markdown_ingress.models import SafeDocument
from markdown_ingress.core.fetcher import Fetcher
from markdown_ingress.core.extractor import Extractor
from markdown_ingress.core.normalizer import Normalizer
from markdown_ingress.core.markdown import MarkdownConverter
from markdown_ingress.core.hashing import Hasher
from markdown_ingress.core.tokens import TokenEstimator
from markdown_ingress.core.security import SecurityAnalyzer
from markdown_ingress.core.scoring import Scorer


def ingest(
    url: str,
    mode: Literal["fast", "render"] = "fast",
    strict: bool = True,
    model: str = "gpt-4",
    timeout: float = 30.0
) -> SafeDocument:
    """
    Ingest web content and convert to safe, sanitized Markdown.
    
    This is the main entry point for MarkDownIngress. It fetches content from a URL,
    extracts the main content, converts to Markdown, and analyzes for security risks.
    
    Args:
        url: Target URL to ingest
        mode: Fetching mode - 'fast' (HTTP only) or 'render' (with JS, requires Playwright)
        strict: Enable strict security mode (blocks suspicious content)
        model: LLM model name for token estimation (default: 'gpt-4')
        timeout: Request timeout in seconds (default: 30.0)
        
    Returns:
        SafeDocument with markdown content, metadata, and security analysis
        
    Raises:
        ValueError: If mode is 'render' (not yet implemented)
        httpx.HTTPError: On network/HTTP errors
        
    Example:
        >>> from markdown_ingress import ingest
        >>> doc = ingest("https://example.com", mode="fast", strict=True)
        >>> print(doc.markdown)
        >>> print(f"Injection score: {doc.injection_score}")
    """
    if mode == "render":
        raise ValueError("Render mode not yet implemented. Use mode='fast' for v0.1")
    
    # Initialize components
    fetcher = Fetcher(timeout=timeout)
    extractor = Extractor(strict=strict)
    normalizer = Normalizer()
    md_converter = MarkdownConverter()
    hasher = Hasher()
    token_estimator = TokenEstimator(model=model)
    security_analyzer = SecurityAnalyzer(strict=strict)
    scorer = Scorer()
    
    # Step 1: Fetch HTML
    fetch_result = fetcher.fetch_sync(url)
    
    # Step 2: Extract main content and clean
    extraction_result = extractor.extract(fetch_result.html, fetch_result.url)
    
    # Step 3: Convert to Markdown
    markdown = md_converter.convert(extraction_result.html)
    
    # Step 4: Analyze security
    hidden_detected = extraction_result.removed_hidden > 0
    security_analysis = security_analyzer.analyze(
        extraction_result.text_content,
        hidden_content_detected=hidden_detected
    )
    
    # Step 5: Generate hash
    content_hash = hasher.hash_content(markdown)
    
    # Step 6: Estimate tokens
    token_count = token_estimator.estimate(markdown)
    token_savings = token_estimator.estimate_savings(fetch_result.html, markdown)
    
    # Build metadata
    metadata = {
        'url': fetch_result.url,
        'final_url': fetch_result.final_url,
        'title': extraction_result.title,
        'fetch_time_ms': fetch_result.timing_ms,
        'status_code': fetch_result.status_code,
        'model': model,
        'mode': mode,
        'strict': strict,
        'token_savings': token_savings,
        'risk_level': scorer.get_risk_level(security_analysis.score),
    }
    
    # Build removed elements summary
    removed_elements = {
        'tags': extraction_result.removed_tags,
        'hidden_elements': extraction_result.removed_hidden,
    }
    
    # Create SafeDocument
    return SafeDocument(
        markdown=markdown,
        metadata=metadata,
        token_estimate=token_count,
        content_hash=content_hash,
        injection_score=security_analysis.score,
        flags=security_analysis.flags,
        removed_elements=removed_elements
    )
