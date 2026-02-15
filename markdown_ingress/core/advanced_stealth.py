"""
Advanced stealth techniques for Playwright to bypass sophisticated bot detection.

This module provides comprehensive stealth capabilities to evade:
- Cloudflare bot detection
- Browser fingerprinting
- WebDriver detection
- Canvas/WebGL fingerprinting
- Behavioral analysis systems

Key Features:
- JavaScript injection to patch detection vectors
- Randomized browser fingerprints
- Advanced context options with realistic behavior
- Ultra-stealth browser launch arguments
- AdvancedStealthRenderer for production use
"""

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from markdown_ingress.models import FetchResult


# ============================================================================
# COMPREHENSIVE STEALTH JAVASCRIPT INJECTION
# ============================================================================

STEALTH_JS_INJECTION = """
// ============================================================================
// Core WebDriver Detection Patches
// ============================================================================

// Override navigator.webdriver (most common detection)
Object.defineProperty(navigator, 'webdriver', {
    get: () => false,
});

// Override automation-controlled flag
Object.defineProperty(navigator, 'automationControlled', {
    get: () => false,
});

// ============================================================================
// Chrome Runtime Patches
// ============================================================================

// Patch chrome.runtime to appear like a normal Chrome browser
if (!window.chrome) {
    window.chrome = {};
}

if (!window.chrome.runtime) {
    window.chrome.runtime = {
        OnInstalledReason: {
            CHROME_UPDATE: "chrome_update",
            INSTALL: "install",
            SHARED_MODULE_UPDATE: "shared_module_update",
            UPDATE: "update",
        },
        OnRestartRequiredReason: {
            APP_UPDATE: "app_update",
            OS_UPDATE: "os_update",
            PERIODIC: "periodic",
        },
        PlatformArch: {
            ARM: "arm",
            ARM64: "arm64",
            MIPS: "mips",
            MIPS64: "mips64",
            X86_32: "x86-32",
            X86_64: "x86-64",
        },
        PlatformNaclArch: {
            ARM: "arm",
            MIPS: "mips",
            MIPS64: "mips64",
            X86_32: "x86-32",
            X86_64: "x86-64",
        },
        PlatformOs: {
            ANDROID: "android",
            CROS: "cros",
            LINUX: "linux",
            MAC: "mac",
            OPENBSD: "openbsd",
            WIN: "win",
        },
        RequestUpdateCheckStatus: {
            NO_UPDATE: "no_update",
            THROTTLED: "throttled",
            UPDATE_AVAILABLE: "update_available",
        },
    };
}

// ============================================================================
// Permissions API Patches
// ============================================================================

// Override permissions.query to return realistic values
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => {
    if (parameters.name === 'notifications') {
        return Promise.resolve({ 
            state: Notification.permission,
            onchange: null,
        });
    }
    return originalQuery(parameters);
};

// ============================================================================
// Plugin and MIME Type Patches
// ============================================================================

// Patch plugins to appear like a real browser
Object.defineProperty(navigator, 'plugins', {
    get: () => {
        const pluginArray = [
            {
                name: 'Chrome PDF Plugin',
                filename: 'internal-pdf-viewer',
                description: 'Portable Document Format',
                length: 1,
            },
            {
                name: 'Chrome PDF Viewer',
                filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai',
                description: '',
                length: 1,
            },
            {
                name: 'Native Client',
                filename: 'internal-nacl-plugin',
                description: '',
                length: 2,
            },
        ];
        // Make it iterable like a real PluginArray
        pluginArray.item = function(index) {
            return this[index] || null;
        };
        pluginArray.namedItem = function(name) {
            return this.find(p => p.name === name) || null;
        };
        return pluginArray;
    },
});

// ============================================================================
// Language and Locale Patches
// ============================================================================

// Patch languages to be consistent and realistic
Object.defineProperty(navigator, 'languages', {
    get: () => ['en-US', 'en'],
});

// ============================================================================
// Hardware Concurrency and Memory Patches
// ============================================================================

// Randomize hardware concurrency (CPU cores) to avoid fingerprinting
const hardwareConcurrency = [4, 8, 12, 16][Math.floor(Math.random() * 4)];
Object.defineProperty(navigator, 'hardwareConcurrency', {
    get: () => hardwareConcurrency,
});

// Add realistic device memory (in GB)
if (!navigator.deviceMemory) {
    Object.defineProperty(navigator, 'deviceMemory', {
        get: () => [4, 8, 16][Math.floor(Math.random() * 3)],
    });
}

// ============================================================================
// WebGL Fingerprinting Patches
// ============================================================================

// Override WebGL parameters to provide consistent, realistic values
const getParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(parameter) {
    // UNMASKED_VENDOR_WEBGL
    if (parameter === 37445) {
        return 'Intel Inc.';
    }
    // UNMASKED_RENDERER_WEBGL
    if (parameter === 37446) {
        return 'Intel Iris OpenGL Engine';
    }
    return getParameter.apply(this, [parameter]);
};

// Also patch WebGL2 if available
if (typeof WebGL2RenderingContext !== 'undefined') {
    const getParameter2 = WebGL2RenderingContext.prototype.getParameter;
    WebGL2RenderingContext.prototype.getParameter = function(parameter) {
        if (parameter === 37445) {
            return 'Intel Inc.';
        }
        if (parameter === 37446) {
            return 'Intel Iris OpenGL Engine';
        }
        return getParameter2.apply(this, [parameter]);
    };
}

// ============================================================================
// Canvas Fingerprinting Protection
// ============================================================================

// Add noise to canvas fingerprinting attempts
const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
HTMLCanvasElement.prototype.toDataURL = function(type) {
    // Only add noise for fingerprinting attempts (small canvases)
    if (this.width < 100 && this.height < 100) {
        const ctx = this.getContext('2d');
        if (ctx) {
            const imageData = ctx.getImageData(0, 0, this.width, this.height);
            // Add minimal noise
            for (let i = 0; i < imageData.data.length; i += 4) {
                imageData.data[i] += Math.random() < 0.1 ? 1 : 0;
            }
            ctx.putImageData(imageData, 0, 0);
        }
    }
    return originalToDataURL.apply(this, [type]);
};

// ============================================================================
// Battery API Patches
// ============================================================================

// Remove battery API to avoid fingerprinting
if (navigator.getBattery) {
    Object.defineProperty(navigator, 'getBattery', {
        get: () => undefined,
    });
}

// ============================================================================
// Connection API Patches
// ============================================================================

// Standardize connection info to avoid fingerprinting
if (navigator.connection) {
    Object.defineProperty(navigator, 'connection', {
        get: () => ({
            effectiveType: '4g',
            rtt: 50,
            downlink: 10,
            saveData: false,
        }),
    });
}

// ============================================================================
// Screen Resolution Consistency
// ============================================================================

// Ensure screen dimensions match window dimensions (avoid detection)
const originalScreenWidth = screen.width;
const originalScreenHeight = screen.height;

Object.defineProperty(screen, 'availWidth', {
    get: () => originalScreenWidth,
});

Object.defineProperty(screen, 'availHeight', {
    get: () => originalScreenHeight,
});

// ============================================================================
// Date and Time Consistency
// ============================================================================

// Ensure timezone consistency
const originalDateTimeFormat = Intl.DateTimeFormat;
Intl.DateTimeFormat = function(...args) {
    if (args.length === 0 || !args[0]) {
        args[0] = 'en-US';
    }
    return new originalDateTimeFormat(...args);
};

// ============================================================================
// Notification Patches
// ============================================================================

// Ensure Notification permission is realistic
if (window.Notification) {
    Object.defineProperty(Notification, 'permission', {
        get: () => 'default',
    });
}

// ============================================================================
// Mouse and Touch Event Consistency
// ============================================================================

// Ensure ontouchstart is not present on desktop
if (window.ontouchstart !== undefined) {
    delete window.ontouchstart;
}

// ============================================================================
// Error Stack Trace Cleaning
// ============================================================================

// Clean up error stack traces to remove automation artifacts
const originalErrorPrepareStackTrace = Error.prepareStackTrace;
Error.prepareStackTrace = function(error, stack) {
    if (originalErrorPrepareStackTrace) {
        return originalErrorPrepareStackTrace(error, stack);
    }
    return stack;
};

// ============================================================================
// Iframe Detection Patches
// ============================================================================

// Patch iframe detection
Object.defineProperty(window, 'top', {
    get: () => window,
});

Object.defineProperty(window, 'frameElement', {
    get: () => null,
});

// ============================================================================
// Console Debug Protection
// ============================================================================

// Prevent console.debug detection
const originalConsoleDebug = console.debug;
console.debug = function(...args) {
    // Filter out automation-related messages
    const message = args.join(' ');
    if (!message.includes('puppeteer') && !message.includes('playwright')) {
        originalConsoleDebug.apply(console, args);
    }
};
"""


# Additional stealth script for post-page-load injection
STEALTH_JS_POST_LOAD = """
// Additional runtime evasions after page load

// Remove automation test artifacts
delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;

// Clean up any automation properties
const automationProps = [
    '__webdriver_evaluate',
    '__selenium_evaluate',
    '__webdriver_script_function',
    '__webdriver_script_func',
    '__webdriver_script_fn',
    '__fxdriver_evaluate',
    '__driver_unwrapped',
    '__webdriver_unwrapped',
    '__driver_evaluate',
    '__selenium_unwrapped',
    '__fxdriver_unwrapped',
];

automationProps.forEach(prop => {
    if (window[prop]) {
        delete window[prop];
    }
    if (document[prop]) {
        delete document[prop];
    }
});

// Ensure no automation flags on document
['$chrome_asyncScriptInfo', '__$webdriverAsyncExecutor'].forEach(prop => {
    if (document[prop]) {
        delete document[prop];
    }
});
"""


# ============================================================================
# ULTRA STEALTH BROWSER LAUNCH ARGUMENTS
# ============================================================================

ULTRA_STEALTH_ARGS = [
    # Core automation hiding
    '--disable-blink-features=AutomationControlled',
    
    # Isolation and security features to disable
    '--disable-features=IsolateOrigins,site-per-process',
    '--disable-site-isolation-trials',
    '--disable-web-security',
    
    # Resource optimization
    '--disable-dev-shm-usage',
    '--no-sandbox',
    '--disable-setuid-sandbox',
    
    # UI and extensions
    '--disable-infobars',
    '--disable-extensions',
    '--disable-default-apps',
    
    # Window and display settings
    '--window-size=1920,1080',
    '--start-maximized',
    '--force-color-profile=srgb',
    
    # GPU and rendering
    '--disable-gpu',
    '--disable-software-rasterizer',
    
    # Background processes and throttling
    '--disable-background-timer-throttling',
    '--disable-backgrounding-occluded-windows',
    '--disable-renderer-backgrounding',
    '--disable-background-networking',
    
    # Network and IPC
    '--disable-ipc-flooding-protection',
    '--disable-hang-monitor',
    
    # Startup and prompts
    '--no-first-run',
    '--no-default-browser-check',
    '--no-service-autorun',
    
    # Password and credentials
    '--password-store=basic',
    '--use-mock-keychain',
    
    # Audio
    '--mute-audio',
    '--autoplay-policy=no-user-gesture-required',
    
    # Metrics and reporting
    '--disable-client-side-phishing-detection',
    '--disable-component-update',
    '--disable-domain-reliability',
    
    # Sync and cloud features
    '--disable-sync',
    '--disable-translate',
    
    # Additional privacy
    '--disable-breakpad',
    '--disable-crash-reporter',
    
    # Performance
    '--enable-features=NetworkService,NetworkServiceInProcess',
    '--force-device-scale-factor=1',
]


# ============================================================================
# ADVANCED CONFIGURATION
# ============================================================================

@dataclass
class AdvancedStealthConfig:
    """Advanced stealth configuration with full customization."""
    
    user_agent: str
    viewport_width: int
    viewport_height: int
    device_scale_factor: float
    locale: str = "en-US"
    timezone: str = "America/New_York"
    permissions: list[str] = field(default_factory=lambda: ["geolocation", "notifications"])
    extra_http_headers: dict[str, str] = field(default_factory=dict)
    browser_args: list[str] = field(default_factory=list)
    
    # Advanced features
    enable_javascript: bool = True
    bypass_csp: bool = True
    ignore_https_errors: bool = True
    has_touch: bool = False
    is_mobile: bool = False
    geolocation: Optional[dict] = None


# Enhanced user agent pool with more variety
ADVANCED_USER_AGENTS = [
    # Chrome 120-123 (Windows)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    
    # Chrome 120-123 (macOS)
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    
    # Chrome 120-123 (Linux)
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    
    # Edge 120-123 (Windows)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
    
    # Edge (macOS)
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
    
    # Firefox 121-124 (Windows)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    
    # Firefox (macOS)
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:123.0) Gecko/20100101 Firefox/123.0",
    
    # Firefox (Linux)
    "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:122.0) Gecko/20100101 Firefox/122.0",
    
    # Safari 17 (macOS)
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]


# Diverse viewport sizes
ADVANCED_VIEWPORT_SIZES = [
    # Common desktop resolutions
    (1920, 1080),  # Full HD (most common)
    (1366, 768),   # Common laptop
    (1440, 900),   # MacBook Pro 13"
    (1536, 864),   # Surface/Windows scaled
    (1280, 720),   # HD
    (1600, 900),   # HD+
    (2560, 1440),  # 2K
    (1680, 1050),  # Legacy wide
    (1280, 1024),  # 5:4 ratio
    (1920, 1200),  # WUXGA
]


# Timezone options for randomization
TIMEZONES = [
    "America/New_York",
    "America/Chicago",
    "America/Los_Angeles",
    "America/Denver",
    "Europe/London",
    "Europe/Paris",
    "Europe/Berlin",
    "Asia/Tokyo",
    "Asia/Shanghai",
    "Australia/Sydney",
]


# Realistic HTTP headers
REALISTIC_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "Cache-Control": "max-age=0",
}


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_advanced_stealth_config(
    randomize: bool = True,
    user_agent: Optional[str] = None,
    viewport: Optional[tuple[int, int]] = None,
    timezone: Optional[str] = None,
) -> AdvancedStealthConfig:
    """
    Get an advanced stealth configuration with maximum anti-detection.
    
    Args:
        randomize: Whether to randomize user agent and viewport
        user_agent: Custom user agent (overrides randomization)
        viewport: Custom viewport as (width, height) tuple
        timezone: Custom timezone (e.g., "America/New_York")
    
    Returns:
        AdvancedStealthConfig: Comprehensive stealth configuration
    
    Example:
        >>> config = get_advanced_stealth_config()
        >>> print(config.user_agent)
        Mozilla/5.0 (Windows NT 10.0; Win64; x64) ...
        >>> print(config.viewport_width, config.viewport_height)
        1920 1080
    """
    if randomize:
        selected_ua = user_agent or random.choice(ADVANCED_USER_AGENTS)
        selected_viewport = viewport or random.choice(ADVANCED_VIEWPORT_SIZES)
        selected_timezone = timezone or random.choice(TIMEZONES)
    else:
        selected_ua = user_agent or ADVANCED_USER_AGENTS[0]
        selected_viewport = viewport or ADVANCED_VIEWPORT_SIZES[0]
        selected_timezone = timezone or TIMEZONES[0]
    
    viewport_width, viewport_height = selected_viewport
    
    # Randomize device scale factor (1.0 to 2.0)
    device_scale_factor = round(random.uniform(1.0, 2.0), 2) if randomize else 1.0
    
    return AdvancedStealthConfig(
        user_agent=selected_ua,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        device_scale_factor=device_scale_factor,
        locale="en-US",
        timezone=selected_timezone,
        permissions=["geolocation", "notifications"],
        extra_http_headers=REALISTIC_HEADERS.copy(),
        browser_args=ULTRA_STEALTH_ARGS.copy(),
    )


def get_advanced_context_options(
    stealth_config: Optional[AdvancedStealthConfig] = None,
) -> dict[str, Any]:
    """
    Get browser context options with all anti-detection measures enabled.
    
    Args:
        stealth_config: Optional AdvancedStealthConfig. If not provided,
                       a randomized one is generated.
    
    Returns:
        dict: Context options suitable for playwright's browser.new_context()
              with comprehensive stealth settings.
    
    Example:
        >>> options = get_advanced_context_options()
        >>> context = await browser.new_context(**options)
    """
    if stealth_config is None:
        stealth_config = get_advanced_stealth_config()
    
    context_options = {
        "user_agent": stealth_config.user_agent,
        "viewport": {
            "width": stealth_config.viewport_width,
            "height": stealth_config.viewport_height,
        },
        "device_scale_factor": stealth_config.device_scale_factor,
        "locale": stealth_config.locale,
        "timezone_id": stealth_config.timezone,
        "bypass_csp": stealth_config.bypass_csp,
        "ignore_https_errors": stealth_config.ignore_https_errors,
        "java_script_enabled": stealth_config.enable_javascript,
        "has_touch": stealth_config.has_touch,
        "is_mobile": stealth_config.is_mobile,
        "extra_http_headers": stealth_config.extra_http_headers,
    }
    
    # Add permissions
    if stealth_config.permissions:
        context_options["permissions"] = stealth_config.permissions
    
    # Add geolocation if specified
    if stealth_config.geolocation:
        context_options["geolocation"] = stealth_config.geolocation
    
    return context_options


async def inject_stealth(page):
    """
    Inject all stealth scripts into a Playwright page.
    
    This function patches all known detection vectors including:
    - navigator.webdriver
    - Chrome runtime
    - WebGL fingerprinting
    - Canvas fingerprinting
    - Plugin detection
    - And many more...
    
    Args:
        page: Playwright Page object
    
    Returns:
        None (modifies page in-place)
    
    Example:
        >>> page = await context.new_page()
        >>> await inject_stealth(page)
        >>> await page.goto("https://example.com")
    """
    # Inject main stealth script before page loads
    await page.add_init_script(STEALTH_JS_INJECTION)
    
    # Add post-load stealth script
    try:
        await page.evaluate(STEALTH_JS_POST_LOAD)
    except Exception:
        # Page might not be ready yet, that's okay
        # The init script is the critical one
        pass


# ============================================================================
# ADVANCED STEALTH RENDERER
# ============================================================================

class AdvancedStealthRenderer:
    """
    Advanced Playwright renderer with maximum bot detection evasion.
    
    Features:
    - Ultra stealth browser arguments
    - Comprehensive JavaScript injection
    - Randomized browser fingerprints
    - Realistic HTTP headers
    - Smart waiting strategies
    - Automatic retry logic
    - HTTP/2 fallback support
    
    Example:
        >>> renderer = AdvancedStealthRenderer()
        >>> result = await renderer.render("https://example.com")
        >>> print(result.html)
    """
    
    DEFAULT_TIMEOUT = 30000  # milliseconds
    DEFAULT_WAIT_UNTIL = "networkidle"
    
    def __init__(
        self,
        timeout: float = 30.0,
        wait_until: str = "networkidle",
        headless: bool = True,
        randomize_fingerprint: bool = True,
        disable_http2: bool = False,
        stealth_config: Optional[AdvancedStealthConfig] = None,
    ):
        """
        Initialize advanced stealth renderer.
        
        Args:
            timeout: Navigation timeout in seconds (default: 30.0)
            wait_until: When to consider navigation complete
                       ('networkidle', 'load', 'domcontentloaded')
            headless: Run browser in headless mode (default: True)
            randomize_fingerprint: Randomize user agent, viewport, etc.
            disable_http2: Disable HTTP/2 protocol (for compatibility)
            stealth_config: Optional custom stealth configuration
        """
        self.timeout = int(timeout * 1000)  # Convert to milliseconds
        self.wait_until = wait_until
        self.headless = headless
        self.randomize_fingerprint = randomize_fingerprint
        self.disable_http2 = disable_http2
        
        # Generate or use provided stealth config
        if stealth_config is None:
            self.stealth_config = get_advanced_stealth_config(
                randomize=randomize_fingerprint
            )
        else:
            self.stealth_config = stealth_config
    
    async def render(self, url: str) -> FetchResult:
        """
        Render URL using advanced stealth techniques.
        
        Includes automatic retry logic and HTTP/2 fallback.
        
        Args:
            url: Target URL to render
        
        Returns:
            FetchResult with rendered HTML and metadata
        
        Raises:
            ImportError: If playwright is not installed
            Exception: On unrecoverable errors
        """
        try:
            result = await self._render_with_browser(url)
            return result
        except Exception as e:
            error_str = str(e)
            # Check for HTTP/2 protocol error
            if 'ERR_HTTP2_PROTOCOL_ERROR' in error_str and not self.disable_http2:
                # Retry with HTTP/2 disabled
                retry_renderer = AdvancedStealthRenderer(
                    timeout=self.timeout / 1000.0,
                    wait_until=self.wait_until,
                    headless=self.headless,
                    randomize_fingerprint=self.randomize_fingerprint,
                    disable_http2=True,
                    stealth_config=self.stealth_config,
                )
                result = await retry_renderer._render_with_browser(url)
                result.metadata['http2_fallback'] = True
                result.metadata['original_error'] = 'ERR_HTTP2_PROTOCOL_ERROR'
                return result
            raise
    
    async def _render_with_browser(self, url: str) -> FetchResult:
        """
        Internal method to render URL with browser.
        
        Args:
            url: Target URL to render
        
        Returns:
            FetchResult with rendered HTML and metadata
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise ImportError(
                "Playwright is not installed. Install with: "
                "pip install 'markdown-ingress[render]' or "
                "pip install playwright && playwright install"
            )
        
        start_time = time.perf_counter()
        
        async with async_playwright() as p:
            # Prepare browser arguments
            browser_args = self.stealth_config.browser_args.copy()
            
            # Add HTTP/2 disable flag if needed
            if self.disable_http2:
                browser_args.append('--disable-http2')
            
            # Launch browser with ultra stealth args
            launch_options = {
                'headless': self.headless,
                'args': browser_args,
                'ignore_default_args': ['--enable-automation'],
            }
            
            browser = await p.chromium.launch(**launch_options)
            
            try:
                # Create context with advanced stealth options
                context_options = get_advanced_context_options(self.stealth_config)
                context = await browser.new_context(**context_options)
                
                try:
                    # Create page
                    page = await context.new_page()
                    
                    # Inject stealth scripts
                    await inject_stealth(page)
                    
                    # Navigate to URL
                    response = await page.goto(
                        url,
                        timeout=self.timeout,
                        wait_until=self.wait_until
                    )
                    
                    # Additional wait for dynamic content
                    await page.wait_for_timeout(500)
                    
                    # Get final URL (after redirects)
                    final_url = page.url
                    
                    # Get status code
                    status_code = response.status if response else 200
                    
                    # Get rendered HTML
                    html = await page.content()
                    
                    # Get headers
                    headers = dict(response.headers) if response else {}
                    
                    elapsed_ms = (time.perf_counter() - start_time) * 1000
                    
                    # Build metadata
                    metadata = {
                        'renderer': 'advanced_stealth_playwright',
                        'user_agent': self.stealth_config.user_agent,
                        'viewport': f"{self.stealth_config.viewport_width}x{self.stealth_config.viewport_height}",
                        'device_scale_factor': self.stealth_config.device_scale_factor,
                        'timezone': self.stealth_config.timezone,
                        'http2_disabled': self.disable_http2,
                        'stealth_injected': True,
                    }
                    
                    return FetchResult(
                        html=html,
                        url=url,
                        status_code=status_code,
                        final_url=final_url,
                        headers=headers,
                        timing_ms=elapsed_ms,
                        metadata=metadata
                    )
                
                finally:
                    await context.close()
            
            finally:
                await browser.close()
    
    def render_sync(self, url: str) -> FetchResult:
        """
        Synchronous wrapper for render().
        
        Args:
            url: Target URL to render
        
        Returns:
            FetchResult with rendered HTML
        """
        return asyncio.run(self.render(url))


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

async def render_with_advanced_stealth(
    url: str,
    timeout: float = 30.0,
    headless: bool = True,
) -> FetchResult:
    """
    Convenience function to render a URL with advanced stealth.
    
    Args:
        url: Target URL to render
        timeout: Navigation timeout in seconds
        headless: Run browser in headless mode
    
    Returns:
        FetchResult with rendered HTML
    
    Example:
        >>> result = await render_with_advanced_stealth("https://example.com")
        >>> print(result.status_code)
        200
    """
    renderer = AdvancedStealthRenderer(
        timeout=timeout,
        headless=headless,
    )
    return await renderer.render(url)


def render_with_advanced_stealth_sync(
    url: str,
    timeout: float = 30.0,
    headless: bool = True,
) -> FetchResult:
    """
    Synchronous convenience function for advanced stealth rendering.
    
    Args:
        url: Target URL to render
        timeout: Navigation timeout in seconds
        headless: Run browser in headless mode
    
    Returns:
        FetchResult with rendered HTML
    
    Example:
        >>> result = render_with_advanced_stealth_sync("https://example.com")
        >>> print(result.html[:100])
    """
    return asyncio.run(render_with_advanced_stealth(url, timeout, headless))
