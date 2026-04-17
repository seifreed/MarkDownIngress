"""
Fingerprint spoofing for stealth mode.

This module provides JavaScript injection for:
- WebGL fingerprinting protection
- Canvas fingerprinting protection
"""

# This module extends the base JavaScript injection with fingerprinting-specific patches
# These are already included in js_injection.py but documented here for organization

# Note: The WebGL and Canvas fingerprinting patches are included in the main
# STEALTH_JS_INJECTION in js_injection.py. This module serves as a placeholder
# for future fingerprinting-specific functionality and to maintain the
# organizational structure requested.

# The actual fingerprinting code is in js_injection.py lines covering:
# - WebGL Fingerprinting Patches (WebGLRenderingContext.prototype.getParameter)
# - Canvas Fingerprinting Protection (HTMLCanvasElement.prototype.toDataURL)

WEBGL_FINGERPRINT_JS = """
// ============================================================================
// WebGL Fingerprinting Patches
// ============================================================================

// Override WebGL parameters to provide realistic per-session values
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
"""


CANVAS_FINGERPRINT_JS = """
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
"""
