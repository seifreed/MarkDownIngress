"""
Ingestion orchestration for MarkDownIngress pipeline.

This module contains the IngestOrchestrator class that coordinates
the web-to-markdown ingestion pipeline, separating concerns from the API layer.
"""

from typing import Literal, Optional, Union

from markdown_ingress.core.extractor import Extractor
from markdown_ingress.core.fetcher import Fetcher
from markdown_ingress.core.hashing import Hasher
from markdown_ingress.core.interfaces import IExtractor, IFetcher, INormalizer, IRenderer
from markdown_ingress.core.link_analyzer import LinkAnalyzer
from markdown_ingress.core.markdown import MarkdownConverter
from markdown_ingress.core.metadata_extractor import MetadataExtractor
from markdown_ingress.core.normalizer import Normalizer
from markdown_ingress.core.scoring import Scorer
from markdown_ingress.core.security_engine import SecurityEngine
from markdown_ingress.core.tokens import TokenEstimator
from markdown_ingress.models import SafeDocument

# Import renderer only if needed (optional dependency)
try:
    from markdown_ingress.core.renderer import Renderer

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    Renderer = None


class IngestOrchestrator:
    """
    Orchestrates the web → markdown ingestion pipeline.
    
    Coordinates fetching, extraction, conversion, and security analysis
    using dependency injection pattern for better testability and maintainability.
    """

    def __init__(
        self,
        extractor: Optional[IExtractor] = None,
        normalizer: Optional[INormalizer] = None,
        md_converter: Optional[MarkdownConverter] = None,
        hasher: Optional[Hasher] = None,
        token_estimator: Optional[TokenEstimator] = None,
        scorer: Optional[Scorer] = None,
        metadata_extractor: Optional[MetadataExtractor] = None,
        link_analyzer: Optional[LinkAnalyzer] = None,
    ):
        """
        Initialize orchestrator with optional dependency injection.
        
        Args:
            extractor: HTML content extractor (implements IExtractor)
            normalizer: HTML normalizer (implements INormalizer)
            md_converter: Markdown converter
            hasher: Content hasher
            token_estimator: Token estimator
            scorer: Security scorer
            metadata_extractor: Metadata extractor
            link_analyzer: Link analyzer
        """
        self.extractor = extractor
        self.normalizer = normalizer
        self.md_converter = md_converter
        self.hasher = hasher
        self.token_estimator = token_estimator
        self.scorer = scorer
        self.metadata_extractor = metadata_extractor
        self.link_analyzer = link_analyzer

    def execute(
        self,
        url: str,
        mode: Literal["fast", "render"],
        strict: bool = True,
        model: str = "gpt-4",
        timeout: float = 30.0,
        stealth: bool = False,
        disable_http2: bool = False,
        extreme_mode: bool = False,
        screenshot: Optional[Union[bool, str]] = None,
        extract_metadata: bool = True,
        extract_links: bool = True,
        advanced_security: bool = False,
        use_llm: bool = False,
    ) -> SafeDocument:
        """
        Execute the complete ingestion pipeline.
        
        Args:
            url: Target URL to ingest
            mode: Fetching mode ('fast' or 'render')
            strict: Enable strict security mode
            model: LLM model name for token estimation
            timeout: Request timeout in seconds
            stealth: Enable stealth mode (render mode only)
            disable_http2: Disable HTTP/2 protocol (render mode only)
            extreme_mode: Enable extreme timeouts (render mode only)
            screenshot: Screenshot configuration
            extract_metadata: Extract enriched metadata
            extract_links: Extract and analyze links
            advanced_security: Enable Nova-tracer advanced injection detection
            use_llm: Enable LLM-based detection tier
            
        Returns:
            SafeDocument with markdown content, metadata, and security analysis
        """
        # Initialize components with defaults if not injected
        extractor = self.extractor or Extractor(strict=strict)
        normalizer = self.normalizer or Normalizer()
        md_converter = self.md_converter or MarkdownConverter()
        hasher = self.hasher or Hasher()
        token_estimator = self.token_estimator or TokenEstimator(model=model)
        scorer = self.scorer or Scorer()

        # Step 1: Fetch HTML (mode-dependent)
        if mode == "render":
            if not PLAYWRIGHT_AVAILABLE:
                raise ImportError(
                    "Render mode requires Playwright. Install with: "
                    "pip install 'markdown-ingress[render]' && playwright install"
                )
            renderer = Renderer(
                timeout=timeout,
                stealth=stealth,
                disable_http2=disable_http2,
                extreme_mode=extreme_mode,
                screenshot=screenshot,
            )
            fetch_result = renderer.render_sync(url)
        else:  # fast mode
            fetcher = Fetcher(timeout=timeout)
            fetch_result = fetcher.fetch_sync(url)

        # Step 2: Extract main content and clean
        extraction_result = extractor.extract(fetch_result.html, fetch_result.url)

        # Step 3: Extract enriched metadata if requested
        enriched_metadata = None
        if extract_metadata:
            metadata_extractor = self.metadata_extractor or MetadataExtractor()
            enriched_metadata = metadata_extractor.extract(fetch_result.html, fetch_result.url)

        # Step 4: Extract and analyze links if requested
        links = None
        if extract_links:
            link_analyzer = self.link_analyzer or LinkAnalyzer()
            links = link_analyzer.analyze(extraction_result.html, fetch_result.url)

        # Step 5: Convert to Markdown
        markdown = md_converter.convert(extraction_result.html)

        # Step 6: Analyze security with SecurityEngine
        security_metadata = {
            "hidden_elements_count": extraction_result.removed_hidden,
        }

        security_engine = SecurityEngine(
            strict=strict, advanced_security=advanced_security, use_llm=use_llm
        )
        security_result = security_engine.analyze(extraction_result.text_content, security_metadata)

        # Step 7: Generate hashes
        content_hash = hasher.hash_content(markdown)
        structural_hash = hasher.hash_structural(markdown)

        # Step 8: Estimate tokens
        token_count = token_estimator.estimate(markdown)
        token_savings = token_estimator.estimate_savings(fetch_result.html, markdown)

        # Build metadata
        metadata = {
            "url": fetch_result.url,
            "final_url": fetch_result.final_url,
            "title": extraction_result.title,
            "fetch_time_ms": fetch_result.timing_ms,
            "status_code": fetch_result.status_code,
            "model": model,
            "mode": mode,
            "strict": strict,
            "token_savings": token_savings,
            "risk_level": scorer.get_risk_level(security_result["injection_score"]),
            "structural_hash": structural_hash,
            "advanced_security": advanced_security,
            "security_scan_method": security_result["scan_method"],
        }

        # Add screenshot path if captured
        screenshot_path = fetch_result.metadata.get("screenshot_path")

        # Build removed elements summary
        removed_elements = {
            "tags": extraction_result.removed_tags,
            "hidden_elements": extraction_result.removed_hidden,
        }

        # Create SafeDocument
        return SafeDocument(
            markdown=markdown,
            metadata=metadata,
            token_estimate=token_count,
            content_hash=content_hash,
            injection_score=security_result["injection_score"],
            flags=security_result["flags"],
            removed_elements=removed_elements,
            screenshot_path=screenshot_path,
            enriched_metadata=enriched_metadata,
            links=links,
            nova_score=security_result.get("nova_score"),
            nova_details=security_result.get("nova_details"),
        )
