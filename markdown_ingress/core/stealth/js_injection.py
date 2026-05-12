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

import random

_rng = random.SystemRandom()

try:
    from playwright.async_api import (
        Error as PlaywrightError,
    )
    from playwright.async_api import (
        TimeoutError as PlaywrightTimeoutError,
    )
except ImportError:  # pragma: no cover
    PlaywrightError = Exception  # type: ignore[assignment, misc]  # pragma: no cover
    PlaywrightTimeoutError = Exception  # type: ignore[assignment, misc]  # pragma: no cover

# ============================================================================
# WEBGL FINGERPRINT POOL
# Realistic GPU/driver combinations for fingerprint randomization
# ============================================================================

WEBGL_FINGERPRINTS = [
    ("Intel Inc.", "Intel Iris OpenGL Engine"),
    ("Intel Inc.", "Intel(R) UHD Graphics 630"),
    ("NVIDIA Corporation", "NVIDIA GeForce GTX 1060/PCIe/SSE2"),
    ("NVIDIA Corporation", "NVIDIA GeForce RTX 3070/PCIe/SSE2"),
    ("NVIDIA Corporation", "NVIDIA GeForce RTX 3080/PCIe/SSE2"),
    ("AMD", "AMD Radeon RX 580 Series"),
    ("AMD", "AMD Radeon RX 6800 XT"),
    ("Apple Inc.", "Apple M1"),
    ("Apple Inc.", "Apple M2"),
]


def _get_random_webgl_fingerprint() -> tuple[str, str]:
    """Get a random WebGL fingerprint from the pool.

    Returns:
        Tuple of (vendor, renderer) strings
    """
    return _rng.choice(WEBGL_FINGERPRINTS)


# ============================================================================
# COMPREHENSIVE STEALTH JAVASCRIPT INJECTION
# ============================================================================

STEALTH_JS_INJECTION = """
// ============================================================================
// Core WebDriver Detection Patches
// ============================================================================

// Hide navigator.webdriver so access returns undefined and `'webdriver' in navigator` is false.
const originalNavigator = window.navigator;
const navigatorProxy = new Proxy(originalNavigator, {
    has(target, key) {
        if (key === 'webdriver') {
            return false;
        }
        return Reflect.has(target, key);
    },
    get(target, key, receiver) {
        if (key === 'webdriver') {
            return undefined;
        }
        const value = Reflect.get(target, key, receiver);
        return typeof value === 'function' ? value.bind(target) : value;
    },
    getOwnPropertyDescriptor(target, key) {
        if (key === 'webdriver') {
            return undefined;
        }
        return Reflect.getOwnPropertyDescriptor(target, key);
    },
});

try {
    delete Navigator.prototype.webdriver;
} catch (e) {}
try {
    Object.defineProperty(window, 'navigator', {
        get: () => navigatorProxy,
        configurable: true,
    });
} catch (e) {}

// Override automation-controlled flag
Object.defineProperty(originalNavigator, 'automationControlled', {
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

// BUG FIX: Use function() instead of arrow function to preserve this binding
// Arrow functions don't have their own this, which can be detected
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = function(parameters) {
    if (parameters.name === 'notifications') {
        return Promise.resolve({
            state: Notification.permission,
            onchange: null,
        });
    }
    // BUG FIX: Use .call() with correct context (navigator.permissions, not window.navigator.permissions)
    return originalQuery.call(this, parameters);
};

// ============================================================================
// Plugin and MIME Type Patches
// ============================================================================

const makeFakeMimeType = (type, suffixes, description) => {
    const mimeType = typeof MimeType !== 'undefined' ? Object.create(MimeType.prototype) : {};
    Object.defineProperties(mimeType, {
        type: { value: type, enumerable: true },
        suffixes: { value: suffixes, enumerable: true },
        description: { value: description, enumerable: true },
        enabledPlugin: { value: null, enumerable: true, writable: true },
    });
    return mimeType;
};

const makeFakePlugin = (name, filename, description, mimeTypes) => {
    const plugin = typeof Plugin !== 'undefined' ? Object.create(Plugin.prototype) : {};
    Object.defineProperties(plugin, {
        name: { value: name, enumerable: true },
        filename: { value: filename, enumerable: true },
        description: { value: description, enumerable: true },
        length: { value: mimeTypes.length, enumerable: true },
    });
    mimeTypes.forEach((mimeType, index) => {
        mimeType.enabledPlugin = plugin;
        Object.defineProperty(plugin, index, {
            value: mimeType,
            enumerable: true,
        });
    });
    plugin.item = function(index) {
        return mimeTypes[index] || null;
    };
    plugin.namedItem = function(type) {
        return mimeTypes.find(mimeType => mimeType.type === type) || null;
    };
    return plugin;
};

const fakePlugins = (() => {
    const pluginEntries = [
        makeFakePlugin(
            'Chrome PDF Plugin',
            'internal-pdf-viewer',
            'Portable Document Format',
            [makeFakeMimeType('application/x-google-chrome-pdf', 'pdf', 'Portable Document Format')]
        ),
        makeFakePlugin(
            'Chrome PDF Viewer',
            'mhjfbmdgcfjbbpaeojofohoefgiehjai',
            '',
            [makeFakeMimeType('application/pdf', 'pdf', 'Portable Document Format')]
        ),
        makeFakePlugin(
            'Native Client',
            'internal-nacl-plugin',
            '',
            [
                makeFakeMimeType('application/x-nacl', '', 'Native Client Executable'),
                makeFakeMimeType('application/x-pnacl', '', 'Portable Native Client Executable'),
            ]
        ),
    ];
    const pluginArray = typeof PluginArray !== 'undefined'
        ? Object.create(PluginArray.prototype)
        : [];
    Object.defineProperty(pluginArray, 'length', {
        get: () => pluginEntries.length,
        enumerable: false,
    });
    pluginEntries.forEach((plugin, index) => {
        Object.defineProperty(pluginArray, index, {
            get: () => pluginEntries[index],
            enumerable: true,
        });
    });
    pluginArray.item = function(index) {
        return pluginEntries[index] || null;
    };
    pluginArray.namedItem = function(name) {
        return pluginEntries.find(plugin => plugin.name === name) || null;
    };
    pluginArray[Symbol.iterator] = function* () {
        for (const plugin of pluginEntries) {
            yield plugin;
        }
    };
    return pluginArray;
})();

Object.defineProperty(originalNavigator, 'plugins', {
    get: () => fakePlugins,
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

// Add realistic device memory (in GB) — capture once to avoid detection
const deviceMemory = [4, 8, 16][Math.floor(Math.random() * 3)];
if (!navigator.deviceMemory) {
    Object.defineProperty(navigator, 'deviceMemory', {
        get: () => deviceMemory,
    });
}

// ============================================================================
// WebGL Fingerprinting Patches
// ============================================================================

// BUG FIX: Use randomized fingerprints from a pool of realistic values
// Static fingerprints make all library users trackable as a group
// Placeholders will be replaced with random values at injection time
const getParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(parameter) {
    // UNMASKED_VENDOR_WEBGL
    if (parameter === 37445) {
        return '__WEBGL_VENDOR__';
    }
    // UNMASKED_RENDERER_WEBGL
    if (parameter === 37446) {
        return '__WEBGL_RENDERER__';
    }
    return getParameter.apply(this, [parameter]);
};

// Also patch WebGL2 if available
if (typeof WebGL2RenderingContext !== 'undefined') {
    const getParameter2 = WebGL2RenderingContext.prototype.getParameter;
    WebGL2RenderingContext.prototype.getParameter = function(parameter) {
        if (parameter === 37445) {
            return '__WEBGL_VENDOR__';
        }
        if (parameter === 37446) {
            return '__WEBGL_RENDERER__';
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
    if (this.width <= 280 && this.height <= 280) {
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

// Ensure timezone consistency — preserve prototype and static methods
const originalDateTimeFormat = Intl.DateTimeFormat;
const wrappedDateTimeFormat = function(...args) {
    if (args.length === 0 || !args[0]) {
        args[0] = 'en-US';
    }
    return new originalDateTimeFormat(...args);
};
wrappedDateTimeFormat.prototype = originalDateTimeFormat.prototype;
wrappedDateTimeFormat.supportedLocalesOf = originalDateTimeFormat.supportedLocalesOf;
Object.defineProperty(wrappedDateTimeFormat, 'name', { value: 'DateTimeFormat' });
Object.defineProperty(wrappedDateTimeFormat, 'length', { value: originalDateTimeFormat.length });
Intl.DateTimeFormat = wrappedDateTimeFormat;

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
    await inject_stealth_pre_nav(page)
    await inject_stealth_post_nav(page)


async def inject_stealth_pre_nav(page) -> None:
    """Inject stealth init scripts that persist across navigations.

    Call this BEFORE page.goto(). Uses add_init_script so the patches
    are re-applied on every navigation and frame load.
    """
    webgl_vendor, webgl_renderer = _get_random_webgl_fingerprint()

    stealth_js = STEALTH_JS_INJECTION.replace("__WEBGL_VENDOR__", webgl_vendor).replace(
        "__WEBGL_RENDERER__", webgl_renderer
    )

    await page.add_init_script(stealth_js)


async def inject_stealth_post_nav(page) -> None:
    """Run post-load cleanup to remove automation artifacts.

    Call this AFTER page.goto() has completed, so the evaluate runs
    on the actual target page (not about:blank).
    """
    try:
        await page.evaluate(STEALTH_JS_POST_LOAD)
    except (PlaywrightTimeoutError, PlaywrightError):
        pass  # pragma: no cover
