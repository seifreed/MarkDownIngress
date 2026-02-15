"""
Batch processing for multiple URLs
"""

import asyncio
from typing import List, Optional, Callable, Dict, Any
from dataclasses import dataclass, field
from markdown_ingress.models import SafeDocument
from markdown_ingress.core.fetcher import Fetcher
from markdown_ingress.core.extractor import Extractor
from markdown_ingress.core.normalizer import Normalizer
from markdown_ingress.core.markdown import MarkdownConverter
from markdown_ingress.core.hashing import Hasher
from markdown_ingress.core.tokens import TokenEstimator
from markdown_ingress.core.security import SecurityAnalyzer
from markdown_ingress.core.scoring import Scorer

# Try to import renderer
try:
    from markdown_ingress.core.renderer import Renderer
    RENDERER_AVAILABLE = True
except ImportError:
    RENDERER_AVAILABLE = False
    Renderer = None


@dataclass
class BatchResult:
    """Result from batch processing"""
    total: int
    successful: int
    failed: int
    documents: List[SafeDocument] = field(default_factory=list)
    errors: Dict[str, str] = field(default_factory=dict)  # url -> error message
    
    @property
    def success_rate(self) -> float:
        """Calculate success rate percentage"""
        return (self.successful / self.total * 100) if self.total > 0 else 0.0


class BatchProcessor:
    """Process multiple URLs in batch with parallel execution"""
    
    def __init__(
        self,
        mode: str = "fast",
        strict: bool = True,
        model: str = "gpt-4",
        timeout: float = 30.0,
        max_concurrent: int = 5,
        on_progress: Optional[Callable[[int, int, str], None]] = None
    ):
        """
        Initialize batch processor.
        
        Args:
            mode: Fetching mode ('fast' or 'render')
            strict: Enable strict security mode
            model: LLM model for token estimation
            timeout: Request timeout in seconds
            max_concurrent: Maximum concurrent requests
            on_progress: Optional callback(current, total, url) for progress tracking
        """
        self.mode = mode
        self.strict = strict
        self.model = model
        self.timeout = timeout
        self.max_concurrent = max_concurrent
        self.on_progress = on_progress
        
        # Initialize components
        self.extractor = Extractor(strict=strict)
        self.normalizer = Normalizer()
        self.md_converter = MarkdownConverter()
        self.hasher = Hasher()
        self.token_estimator = TokenEstimator(model=model)
        self.security_analyzer = SecurityAnalyzer(strict=strict)
        self.scorer = Scorer()
    
    async def process_url(self, url: str) -> SafeDocument:
        """
        Process a single URL asynchronously.
        
        Args:
            url: URL to process
            
        Returns:
            SafeDocument
            
        Raises:
            Exception: On processing errors
        """
        # Handle auto mode
        if self.mode == "auto":
            from markdown_ingress.api import ingest
            # Use high-level API which handles auto mode logic
            # Run in thread pool since ingest is synchronous
            import asyncio
            loop = asyncio.get_event_loop()
            doc = await loop.run_in_executor(
                None,
                lambda: ingest(url, mode="auto", strict=self.strict, model=self.model, timeout=self.timeout)
            )
            return doc
        
        # Fetch HTML based on mode
        if self.mode == "render":
            if not RENDERER_AVAILABLE:
                raise ImportError("Render mode requires Playwright")
            renderer = Renderer(timeout=self.timeout)
            fetch_result = await renderer.render(url)
        else:  # fast mode
            fetcher = Fetcher(timeout=self.timeout)
            # Use async version
            fetch_result = await fetcher.fetch(url)
        
        # Extract content
        extraction_result = self.extractor.extract(fetch_result.html, fetch_result.url)
        
        # Convert to markdown
        markdown = self.md_converter.convert(extraction_result.html)
        
        # Security analysis
        hidden_detected = extraction_result.removed_hidden > 0
        security_analysis = self.security_analyzer.analyze(
            extraction_result.text_content,
            hidden_content_detected=hidden_detected
        )
        
        # Generate hash
        content_hash = self.hasher.hash_content(markdown)
        
        # Token estimation
        token_count = self.token_estimator.estimate(markdown)
        token_savings = self.token_estimator.estimate_savings(fetch_result.html, markdown)
        
        # Build metadata
        metadata = {
            'url': fetch_result.url,
            'final_url': fetch_result.final_url,
            'title': extraction_result.title,
            'fetch_time_ms': fetch_result.timing_ms,
            'status_code': fetch_result.status_code,
            'model': self.model,
            'mode': self.mode,
            'strict': self.strict,
            'token_savings': token_savings,
            'risk_level': self.scorer.get_risk_level(security_analysis.score),
        }
        
        # Build removed elements
        removed_elements = {
            'tags': extraction_result.removed_tags,
            'hidden_elements': extraction_result.removed_hidden,
        }
        
        return SafeDocument(
            markdown=markdown,
            metadata=metadata,
            token_estimate=token_count,
            content_hash=content_hash,
            injection_score=security_analysis.score,
            flags=security_analysis.flags,
            removed_elements=removed_elements
        )
    
    async def process_batch_async(self, urls: List[str]) -> BatchResult:
        """
        Process multiple URLs concurrently.
        
        Args:
            urls: List of URLs to process
            
        Returns:
            BatchResult with documents and error details
        """
        total = len(urls)
        successful = 0
        failed = 0
        documents = []
        errors = {}
        
        # Create semaphore for concurrency control
        semaphore = asyncio.Semaphore(self.max_concurrent)
        
        async def process_with_semaphore(index: int, url: str):
            nonlocal successful, failed
            
            async with semaphore:
                try:
                    # Progress callback
                    if self.on_progress:
                        self.on_progress(index + 1, total, url)
                    
                    doc = await self.process_url(url)
                    documents.append(doc)
                    successful += 1
                    
                except Exception as e:
                    errors[url] = str(e)
                    failed += 1
        
        # Process all URLs concurrently
        tasks = [process_with_semaphore(i, url) for i, url in enumerate(urls)]
        await asyncio.gather(*tasks)
        
        return BatchResult(
            total=total,
            successful=successful,
            failed=failed,
            documents=documents,
            errors=errors
        )
    
    def process_batch(self, urls: List[str]) -> BatchResult:
        """
        Synchronous wrapper for batch processing.
        
        Args:
            urls: List of URLs to process
            
        Returns:
            BatchResult
        """
        return asyncio.run(self.process_batch_async(urls))
