"""
Configuration dataclasses for MarkDownIngress.

These dataclasses replace long parameter lists for better maintainability.
"""

from dataclasses import dataclass
from typing import Literal, Optional, Union


@dataclass
class RenderConfig:
    """
    Configuration for Renderer (Playwright-based rendering).
    
    Replaces the 15-parameter Renderer.__init__() signature.
    """
    
    timeout: float = 30.0
    """Navigation timeout in seconds"""
    
    wait_until: str = "networkidle"
    """When to consider navigation complete ('networkidle', 'load', 'domcontentloaded')"""
    
    headless: bool = True
    """Run browser in headless mode"""
    
    user_agent: Optional[str] = None
    """Custom user agent (optional)"""
    
    stealth: bool = False
    """Enable stealth mode to avoid bot detection"""
    
    disable_http2: bool = False
    """Disable HTTP/2 protocol (used for fallback)"""
    
    extreme_mode: bool = False
    """Enable extreme timeouts (up to 300s) and patient waiting"""
    
    block_resources: bool = True
    """Enable resource blocking for faster loads"""
    
    block_images: bool = True
    """Block images when resource blocking enabled"""
    
    block_fonts: bool = True
    """Block fonts when resource blocking enabled"""
    
    block_media: bool = True
    """Block media (video/audio) when resource blocking enabled"""
    
    block_ads: bool = True
    """Block advertising domains when resource blocking enabled"""
    
    block_trackers: bool = True
    """Block analytics/tracking domains when resource blocking enabled"""
    
    screenshot: Optional[Union[bool, str]] = None
    """Screenshot path (str) or True for temp file, None to disable"""


@dataclass
class IngestConfig:
    """
    Configuration for ingest() and Orchestrator.execute().
    
    Replaces the 14-parameter signatures with a clean config object.
    Groups related parameters (security, rendering, output).
    """
    
    # Core parameters
    mode: Literal["fast", "render", "auto"] = "auto"
    """Fetching mode: 'fast' (HTTP only), 'render' (Playwright), 'auto' (detect)"""
    
    strict: bool = True
    """Enable strict security mode (blocks suspicious content)"""
    
    model: str = "gpt-4"
    """LLM model name for token estimation"""
    
    timeout: float = 30.0
    """Request timeout in seconds"""
    
    auto_render_threshold: int = 50
    """Token threshold for auto mode (if fast returns < this, retry with render)"""
    
    # Rendering parameters (render mode only)
    stealth: bool = False
    """Enable stealth mode to avoid bot detection (render mode only)"""
    
    disable_http2: bool = False
    """Disable HTTP/2 protocol, use HTTP/1.1 (render mode only)"""
    
    extreme_mode: bool = False
    """Enable extreme timeouts (up to 300s) and patient waiting (render mode only)"""
    
    screenshot: Optional[Union[bool, str]] = None
    """Screenshot configuration: path (str), True for temp file, None to disable"""
    
    # Extraction parameters
    extract_metadata: bool = True
    """Extract enriched metadata"""
    
    extract_links: bool = True
    """Extract and analyze links"""
    
    # Security parameters
    advanced_security: bool = False
    """Enable Nova-tracer advanced injection detection (v0.7.0)"""
    
    use_llm: bool = False
    """Enable LLM-based detection tier (slow but most accurate, v0.7.0)"""
