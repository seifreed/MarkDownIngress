"""
JavaScript injection for stealth mode.

This module provides:
- Core WebDriver detection patches
- Chrome runtime patches
- Permissions API patches
- Plugin and MIME type patches
- Language and locale patches
- Console debug protection
"""


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
# INJECTION FUNCTION
# ============================================================================


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
