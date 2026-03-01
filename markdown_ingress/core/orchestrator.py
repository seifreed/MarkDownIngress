"""
Ingestion orchestration for MarkDownIngress pipeline.

This module contains the IngestOrchestrator class that coordinates
the web-to-markdown ingestion pipeline, separating concerns from the API layer.
"""

import copy
from typing import Literal

from markdown_ingress.config_models import IngestConfig, RenderConfig
from markdown_ingress.core.cache import Cache
from markdown_ingress.core.extractor import Extractor
from markdown_ingress.core.fetcher import Fetcher
from markdown_ingress.core.hashing import Hasher
from markdown_ingress.core.interfaces import IExtractor, INormalizer
from markdown_ingress.core.link_analyzer import LinkAnalyzer
from markdown_ingress.core.markdown import MarkdownConverter
from markdown_ingress.core.metadata_extractor import MetadataExtractor
from markdown_ingress.core.plugin import PluginLoader
from markdown_ingress.core.policy import PolicyEngine
from markdown_ingress.core.scoring import Scorer
from markdown_ingress.core.security import InjectionPattern, SecurityAnalyzer
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
        extractor: IExtractor | None = None,
        normalizer: INormalizer | None = None,
        md_converter: MarkdownConverter | None = None,
        hasher: Hasher | None = None,
        token_estimator: TokenEstimator | None = None,
        scorer: Scorer | None = None,
        metadata_extractor: MetadataExtractor | None = None,
        link_analyzer: LinkAnalyzer | None = None,
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
        config: IngestConfig | None = None,
        # Backward compatibility: accept individual parameters
        mode: Literal["fast", "render"] | None = None,
        strict: bool | None = None,
        model: str | None = None,
        timeout: float | None = None,
        stealth: bool | None = None,
        disable_http2: bool | None = None,
        extreme_mode: bool | None = None,
        screenshot: bool | str | None = None,
        extract_metadata: bool | None = None,
        extract_links: bool | None = None,
        advanced_security: bool | None = None,
        use_llm: bool | None = None,
    ) -> SafeDocument:
        """
        Execute the complete ingestion pipeline.

        Args:
            url: Target URL to ingest
            config: IngestConfig object with all settings (recommended)
            mode: Fetching mode (deprecated, use config)
            strict: Enable strict security mode (deprecated, use config)
            model: LLM model name for token estimation (deprecated, use config)
            timeout: Request timeout in seconds (deprecated, use config)
            stealth: Enable stealth mode (deprecated, use config)
            disable_http2: Disable HTTP/2 protocol (deprecated, use config)
            extreme_mode: Enable extreme timeouts (deprecated, use config)
            screenshot: Screenshot configuration (deprecated, use config)
            extract_metadata: Extract enriched metadata (deprecated, use config)
            extract_links: Extract and analyze links (deprecated, use config)
            advanced_security: Enable Nova-tracer detection (deprecated, use config)
            use_llm: Enable LLM-based detection (deprecated, use config)

        Returns:
            SafeDocument with markdown content, metadata, and security analysis
        """
        # If no config provided, create from individual parameters or defaults
        if config is None:
            config = IngestConfig(
                mode=mode if mode is not None else "auto",
                strict=strict if strict is not None else True,
                model=model if model is not None else "gpt-4",
                timeout=timeout if timeout is not None else 30.0,
                stealth=stealth if stealth is not None else False,
                disable_http2=disable_http2 if disable_http2 is not None else False,
                extreme_mode=extreme_mode if extreme_mode is not None else False,
                screenshot=screenshot,
                extract_metadata=extract_metadata if extract_metadata is not None else True,
                extract_links=extract_links if extract_links is not None else True,
                advanced_security=advanced_security if advanced_security is not None else False,
                use_llm=use_llm if use_llm is not None else False,
            )
        else:
            # Config provided - override with any explicit parameters
            if mode is not None:
                config.mode = mode
            if strict is not None:
                config.strict = strict
            if model is not None:
                config.model = model
            if timeout is not None:
                config.timeout = timeout
            if stealth is not None:
                config.stealth = stealth
            if disable_http2 is not None:
                config.disable_http2 = disable_http2
            if extreme_mode is not None:
                config.extreme_mode = extreme_mode
            if screenshot is not None:
                config.screenshot = screenshot
            if extract_metadata is not None:
                config.extract_metadata = extract_metadata
            if extract_links is not None:
                config.extract_links = extract_links
            if advanced_security is not None:
                config.advanced_security = advanced_security
            if use_llm is not None:
                config.use_llm = use_llm

        # Initialize components with defaults if not injected
        extractor = self.extractor or Extractor(strict=config.strict)
        md_converter = self.md_converter or MarkdownConverter()
        hasher = self.hasher or Hasher()
        token_estimator = self.token_estimator or TokenEstimator(model=config.model)
        scorer = self.scorer or Scorer()

        # Step 0: Cache lookup (if enabled)
        cache_backend = config.cache
        cache_key = None
        if cache_backend is not None:
            cache_key = Cache.make_key(url=url, mode=config.mode, strict=config.strict)
            cached = cache_backend.get(cache_key)
            if cached is not None:
                cached_copy = copy.deepcopy(cached)
                cached_copy.metadata["cache_hit"] = True
                return cached_copy

        # Step 1: Fetch HTML (mode-dependent)
        if config.mode == "render":
            if not PLAYWRIGHT_AVAILABLE:
                raise ImportError(
                    "Render mode requires Playwright. Install with: "
                    "pip install 'markdown-ingress[render]' && playwright install"
                )
            render_config = RenderConfig(
                timeout=config.timeout,
                stealth=config.stealth,
                disable_http2=config.disable_http2,
                extreme_mode=config.extreme_mode,
                screenshot=config.screenshot,
            )
            renderer = Renderer(config=render_config)
            fetch_result = renderer.render_sync(url)
        else:  # fast or auto mode
            fetcher = Fetcher(timeout=config.timeout)
            try:
                fetch_result = fetcher.fetch_sync(url)
            except Exception as fast_exc:
                # Auto-fallback: if fast mode fails and Playwright is available, try render mode
                if PLAYWRIGHT_AVAILABLE and config.mode == "auto":
                    import logging
                    logging.getLogger(__name__).info(
                        "Fast fetch failed for %s (%s), retrying with Playwright render mode",
                        url, type(fast_exc).__name__,
                    )
                    render_config = RenderConfig(
                        timeout=config.timeout,
                        stealth=config.stealth,
                        disable_http2=config.disable_http2,
                        extreme_mode=True,  # use all fallback wait strategies on auto-retry
                        screenshot=config.screenshot,
                    )
                    renderer = Renderer(config=render_config)
                    fetch_result = renderer.render_sync(url)
                else:
                    raise

        # Step 2: Extract main content and clean
        extraction_result = extractor.extract(fetch_result.html, fetch_result.url)

        # Step 3: Extract enriched metadata if requested
        enriched_metadata = None
        if config.extract_metadata:
            metadata_extractor = self.metadata_extractor or MetadataExtractor()
            enriched_metadata = metadata_extractor.extract(fetch_result.html, fetch_result.url)

        # Step 4: Extract and analyze links if requested
        links = None
        if config.extract_links:
            link_analyzer = self.link_analyzer or LinkAnalyzer()
            links = link_analyzer.analyze(extraction_result.html, fetch_result.url)

        # Step 5: Convert to Markdown
        markdown = md_converter.convert(extraction_result.html)

        # Step 6: Analyze security with SecurityEngine
        security_metadata = {
            "hidden_elements_count": extraction_result.removed_hidden,
        }

        security_engine = SecurityEngine(
            strict=config.strict, advanced_security=config.advanced_security, use_llm=config.use_llm
        )
        security_result = security_engine.analyze(extraction_result.text_content, security_metadata)

        # Optional extension: custom/plugin patterns integrated into scoring path.
        extra_patterns = list(config.custom_patterns)
        plugins_loaded = 0
        if config.plugin_dirs:
            plugin_loader = PluginLoader()
            for plugin_dir in config.plugin_dirs:
                plugins_loaded += plugin_loader.load_from_directory(plugin_dir)
            extra_patterns.extend(plugin_loader.get_all_patterns())

        if extra_patterns:
            custom_defs = [
                InjectionPattern(
                    pattern=pattern,
                    weight=0.5,
                    description=f"custom_pattern_{idx + 1}",
                )
                for idx, pattern in enumerate(extra_patterns)
            ]

            class ExtendedSecurityAnalyzer(SecurityAnalyzer):
                INJECTION_PATTERNS = SecurityAnalyzer.INJECTION_PATTERNS + custom_defs

            extended_analyzer = ExtendedSecurityAnalyzer(strict=config.strict)
            extended_analysis = extended_analyzer.analyze(
                extraction_result.text_content,
                hidden_content_detected=security_metadata["hidden_elements_count"] > 0,
            )
            security_result["injection_score"] = max(
                security_result["injection_score"], extended_analysis.score
            )
            security_result["flags"] = list(set(security_result["flags"] + extended_analysis.flags))

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
            "model": config.model,
            "mode": config.mode,
            "strict": config.strict,
            "token_savings": token_savings,
            "risk_level": scorer.get_risk_level(security_result["injection_score"]),
            "structural_hash": structural_hash,
            "advanced_security": config.advanced_security,
            "security_scan_method": security_result["scan_method"],
            "custom_patterns_count": len(extra_patterns),
            "plugins_loaded": plugins_loaded,
        }

        # Apply policy decision on final score.
        policy_engine = PolicyEngine.from_name(config.policy_name)
        policy_action = policy_engine.get_action(security_result["injection_score"])
        metadata["policy"] = config.policy_name
        metadata["policy_action"] = policy_action
        if policy_action == "block":
            security_result["flags"] = list(set(security_result["flags"] + ["policy_block"]))

        # Add screenshot path if captured
        screenshot_path = fetch_result.metadata.get("screenshot_path")

        # Build removed elements summary
        removed_elements = {
            "tags": extraction_result.removed_tags,
            "hidden_elements": extraction_result.removed_hidden,
        }

        # Create SafeDocument
        document = SafeDocument(
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

        # Step 9: Cache write-through (if enabled)
        if cache_backend is not None and cache_key is not None:
            cache_backend.set(cache_key, document, ttl=config.cache_ttl)
            document.metadata["cache_hit"] = False

        return document
