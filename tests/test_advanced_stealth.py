#!/usr/bin/env python3
"""
Unit tests for advanced stealth functionality.

Tests configuration, injection, and core functionality without
requiring external network access.
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from markdown_ingress.adapters.rendering.advanced_stealth_renderer import AdvancedStealthRenderer
from markdown_ingress.core.stealth import (
    ADVANCED_USER_AGENTS,
    ADVANCED_VIEWPORT_SIZES,
    REALISTIC_HEADERS,
    STEALTH_JS_INJECTION,
    STEALTH_JS_POST_LOAD,
    TIMEZONES,
    ULTRA_STEALTH_ARGS,
    AdvancedStealthConfig,
    get_advanced_context_options,
    get_advanced_stealth_config,
)


def test_constants():
    """Test that all required constants are defined and valid"""
    print("Testing constants...")

    # Test JavaScript injection
    assert STEALTH_JS_INJECTION, "STEALTH_JS_INJECTION should not be empty"
    assert len(STEALTH_JS_INJECTION) > 1000, "STEALTH_JS_INJECTION should be substantial"
    assert "navigator.webdriver" in STEALTH_JS_INJECTION, "Should patch webdriver"
    assert "WebGLRenderingContext" in STEALTH_JS_INJECTION, "Should patch WebGL"
    print("  ✓ STEALTH_JS_INJECTION is valid")

    # Test post-load script
    assert STEALTH_JS_POST_LOAD, "STEALTH_JS_POST_LOAD should not be empty"
    assert "cdc_adoQpoasnfa76pfcZLmcfl" in STEALTH_JS_POST_LOAD, "Should clean CDC properties"
    print("  ✓ STEALTH_JS_POST_LOAD is valid")

    # Test browser arguments
    assert ULTRA_STEALTH_ARGS, "ULTRA_STEALTH_ARGS should not be empty"
    assert len(ULTRA_STEALTH_ARGS) >= 37, "Should have many stealth arguments"
    assert "--disable-blink-features=AutomationControlled" in ULTRA_STEALTH_ARGS
    print(f"  ✓ ULTRA_STEALTH_ARGS contains {len(ULTRA_STEALTH_ARGS)} arguments")

    # Test user agents
    assert ADVANCED_USER_AGENTS, "ADVANCED_USER_AGENTS should not be empty"
    assert len(ADVANCED_USER_AGENTS) > 20, "Should have diverse user agents"
    assert all("Mozilla" in ua for ua in ADVANCED_USER_AGENTS)
    print(f"  ✓ ADVANCED_USER_AGENTS contains {len(ADVANCED_USER_AGENTS)} user agents")

    # Test viewport sizes
    assert ADVANCED_VIEWPORT_SIZES, "ADVANCED_VIEWPORT_SIZES should not be empty"
    assert all(isinstance(vp, tuple) and len(vp) == 2 for vp in ADVANCED_VIEWPORT_SIZES)
    assert all(w > 0 and h > 0 for w, h in ADVANCED_VIEWPORT_SIZES)
    print(f"  ✓ ADVANCED_VIEWPORT_SIZES contains {len(ADVANCED_VIEWPORT_SIZES)} viewports")

    # Test timezones
    assert TIMEZONES, "TIMEZONES should not be empty"
    assert all("/" in tz for tz in TIMEZONES)
    print(f"  ✓ TIMEZONES contains {len(TIMEZONES)} timezones")

    # Test realistic headers
    assert REALISTIC_HEADERS, "REALISTIC_HEADERS should not be empty"
    assert "Accept" in REALISTIC_HEADERS
    assert "User-Agent" not in REALISTIC_HEADERS  # Should be set separately
    print(f"  ✓ REALISTIC_HEADERS contains {len(REALISTIC_HEADERS)} headers")

    print("✓ All constants are valid\n")


def test_advanced_stealth_config():
    """Test AdvancedStealthConfig creation"""
    print("Testing AdvancedStealthConfig...")

    # Test default config
    config = get_advanced_stealth_config(randomize=False)
    assert isinstance(config, AdvancedStealthConfig)
    assert config.user_agent
    assert config.viewport_width > 0
    assert config.viewport_height > 0
    assert config.device_scale_factor >= 1.0
    assert config.locale == "en-US"
    assert config.timezone
    assert isinstance(config.permissions, list)
    assert isinstance(config.extra_http_headers, dict)
    assert isinstance(config.browser_args, list)
    print("  ✓ Default config creation works")

    # Test randomized config
    config1 = get_advanced_stealth_config(randomize=True)
    config2 = get_advanced_stealth_config(randomize=True)
    # Should be different (very high probability)
    assert (
        config1.user_agent != config2.user_agent or config1.viewport_width != config2.viewport_width
    ), "Randomized configs should differ"
    print("  ✓ Randomization works")


def test_stealth_config_generator_request_scoped_no_immediate_repeat(monkeypatch):
    """Generator keeps state per request and avoids immediate repetition without globals."""
    from markdown_ingress.core.stealth import browser_config
    from markdown_ingress.core.stealth.browser_config import StealthConfigGenerator

    def _fixed_choice(seq):
        if seq is browser_config.ADVANCED_USER_AGENTS:
            return seq[0]
        if seq is browser_config.ADVANCED_VIEWPORT_SIZES:
            return seq[0]
        if seq is browser_config.TIMEZONES:
            return seq[0]
        return seq[0]

    monkeypatch.setattr(browser_config.random, "choice", _fixed_choice)
    monkeypatch.setattr(browser_config.random, "uniform", lambda _low, _high: 1.0)

    generator = StealthConfigGenerator(randomize=True)
    first = generator.next_config()
    second = generator.next_config()

    assert (
        first.user_agent != second.user_agent
        or first.viewport_width != second.viewport_width
        or first.timezone != second.timezone
        or first.device_scale_factor != second.device_scale_factor
    ), "request-scoped generator should avoid immediate repetition"


def test_advanced_stealth_config_avoids_immediate_repeat_with_explicit_signature(monkeypatch):
    """Request-scoped signature prevents returning the same random signature twice."""
    from markdown_ingress.core.stealth import browser_config

    def _fixed_choice(seq):
        if seq is browser_config.ADVANCED_USER_AGENTS:
            return seq[0]
        if seq is browser_config.ADVANCED_VIEWPORT_SIZES:
            return seq[0]
        if seq is browser_config.TIMEZONES:
            return seq[0]
        return seq[0]

    monkeypatch.setattr(browser_config.random, "choice", _fixed_choice)
    monkeypatch.setattr(browser_config.random, "uniform", lambda _low, _high: 1.0)

    base = browser_config.get_advanced_stealth_config(randomize=True)
    previous_signature = (
        base.user_agent,
        (base.viewport_width, base.viewport_height),
        base.timezone,
        base.device_scale_factor,
    )
    next_config = browser_config.get_advanced_stealth_config(
        randomize=True,
        previous_signature=previous_signature,
    )

    assert (
        next_config.user_agent != base.user_agent
        or next_config.viewport_width != base.viewport_width
        or next_config.timezone != base.timezone
        or next_config.device_scale_factor != base.device_scale_factor
    ), "previous_signature must avoid immediate repetition"


def test_advanced_stealth_validates_public_url_without_dns_resolution(monkeypatch):
    calls: list[tuple[str, bool, bool]] = []

    def fake_validate(url, *, allow_local, resolve_dns):
        calls.append((url, allow_local, resolve_dns))
        return url

    monkeypatch.setattr(
        "markdown_ingress.adapters.rendering.advanced_stealth_renderer.validate_http_url_no_ssrf",
        fake_validate,
    )
    renderer = AdvancedStealthRenderer(allow_local_urls=False)

    assert renderer._validate_render_url("https://rebind.example/page") == (
        "https://rebind.example/page"
    )
    assert calls == [("https://rebind.example/page", False, False)]


def test_advanced_stealth_config_custom_inputs():
    """Test custom inputs are applied directly."""
    custom_ua = "Custom User Agent"
    custom_viewport = (1024, 768)
    custom_tz = "Europe/London"
    config = get_advanced_stealth_config(
        randomize=False,
        user_agent=custom_ua,
        viewport=custom_viewport,
        timezone=custom_tz,
    )
    assert config.user_agent == custom_ua
    assert config.viewport_width == 1024
    assert config.viewport_height == 768
    assert config.timezone == custom_tz


def test_context_options():
    """Test context options generation"""
    print("Testing context options...")

    # Test with default config
    options = get_advanced_context_options()
    assert isinstance(options, dict)
    assert "user_agent" in options
    assert "viewport" in options
    assert "device_scale_factor" in options
    assert "locale" in options
    assert "timezone_id" in options
    assert "bypass_csp" in options
    assert "ignore_https_errors" in options
    assert "extra_http_headers" in options
    print("  ✓ Default context options are complete")

    # Test viewport structure
    assert isinstance(options["viewport"], dict)
    assert "width" in options["viewport"]
    assert "height" in options["viewport"]
    print("  ✓ Viewport structure is correct")

    # Test with custom config
    config = get_advanced_stealth_config(randomize=False)
    options = get_advanced_context_options(config)
    assert options["user_agent"] == config.user_agent
    assert options["viewport"]["width"] == config.viewport_width
    assert options["viewport"]["height"] == config.viewport_height
    print("  ✓ Custom config propagates correctly")

    print("✓ Context options tests passed\n")


def test_renderer_initialization():
    """Test AdvancedStealthRenderer initialization"""
    print("Testing AdvancedStealthRenderer initialization...")

    # Test default initialization
    renderer = AdvancedStealthRenderer()
    assert renderer.timeout == 30000  # 30 seconds in ms
    assert renderer.wait_until == "networkidle"
    assert renderer.headless is True
    assert renderer.randomize_fingerprint is True
    assert renderer.disable_http2 is False
    assert renderer.stealth_config is not None
    print("  ✓ Default initialization works")

    # Test custom initialization
    custom_config = get_advanced_stealth_config(randomize=False)
    renderer = AdvancedStealthRenderer(
        timeout=60.0,
        wait_until="load",
        headless=False,
        randomize_fingerprint=False,
        disable_http2=True,
        stealth_config=custom_config,
    )
    assert renderer.timeout == 60000  # 60 seconds
    assert renderer.wait_until == "load"
    assert renderer.headless is False
    assert renderer.randomize_fingerprint is False
    assert renderer.disable_http2 is True
    assert renderer.stealth_config == custom_config
    print("  ✓ Custom initialization works")

    print("✓ Renderer initialization tests passed\n")


def test_javascript_injection_content():
    """Test JavaScript injection content for critical patches"""
    print("Testing JavaScript injection content...")

    js = STEALTH_JS_INJECTION

    # Core patches
    critical_patches = [
        "navigator.webdriver",
        "automationControlled",
        "chrome.runtime",
        "permissions.query",
        "plugins",
        "languages",
        "WebGLRenderingContext",
        "HTMLCanvasElement",
        "hardwareConcurrency",
    ]

    for patch in critical_patches:
        assert patch in js, f"Missing critical patch: {patch}"
        print(f"  ✓ Includes {patch} patch")

    assert "new Proxy(originalNavigator" in js
    assert "PluginArray.prototype" in js
    assert "return undefined;" in js
    assert "this.width <= 280 && this.height <= 280" in js

    print("✓ JavaScript injection content tests passed\n")


def test_browser_args_validity():
    """Test that browser arguments are valid"""
    print("Testing browser arguments validity...")

    args = ULTRA_STEALTH_ARGS

    # All should start with --
    for arg in args:
        assert arg.startswith("--"), f"Invalid arg format: {arg}"

    # Check for critical args
    critical_args = [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-infobars",
    ]

    for critical_arg in critical_args:
        assert critical_arg in args, f"Missing critical arg: {critical_arg}"
        print(f"  ✓ Includes {critical_arg}")

    # No duplicates
    assert len(args) == len(set(args)), "Browser args should not have duplicates"
    print(f"  ✓ No duplicates in {len(args)} arguments")

    print("✓ Browser arguments validity tests passed\n")


def test_user_agent_pool_quality():
    """Test user agent pool quality"""
    print("Testing user agent pool quality...")

    uas = ADVANCED_USER_AGENTS

    # All should be strings
    assert all(isinstance(ua, str) for ua in uas), "All user agents should be strings"

    # All should start with Mozilla
    assert all(ua.startswith("Mozilla/5.0") for ua in uas), "All should be Mozilla/5.0"

    # Should have variety
    chrome_count = sum(1 for ua in uas if "Chrome" in ua)
    firefox_count = sum(1 for ua in uas if "Firefox" in ua)
    safari_count = sum(1 for ua in uas if "Safari" in ua and "Chrome" not in ua)

    assert chrome_count > 0, "Should have Chrome user agents"
    assert firefox_count > 0, "Should have Firefox user agents"
    assert safari_count > 0, "Should have Safari user agents"

    print(f"  ✓ Chrome: {chrome_count}, Firefox: {firefox_count}, Safari: {safari_count}")

    # Should have different platforms
    windows_count = sum(1 for ua in uas if "Windows" in ua)
    mac_count = sum(1 for ua in uas if "Macintosh" in ua or "Mac OS" in ua)
    linux_count = sum(1 for ua in uas if "Linux" in ua)

    assert windows_count > 0, "Should have Windows user agents"
    assert mac_count > 0, "Should have macOS user agents"

    print(f"  ✓ Windows: {windows_count}, macOS: {mac_count}, Linux: {linux_count}")

    print("✓ User agent pool quality tests passed\n")


def run_all_tests():
    """Run all tests"""
    print("=" * 80)
    print("ADVANCED STEALTH MODULE TESTS")
    print("=" * 80)
    print()

    tests = [
        ("Constants", test_constants),
        ("AdvancedStealthConfig", test_advanced_stealth_config),
        ("Context Options", test_context_options),
        ("Renderer Initialization", test_renderer_initialization),
        ("JavaScript Injection Content", test_javascript_injection_content),
        ("Browser Arguments Validity", test_browser_args_validity),
        ("User Agent Pool Quality", test_user_agent_pool_quality),
    ]

    passed = 0
    failed = 0

    for name, test_func in tests:
        try:
            test_func()
            passed += 1
        except AssertionError as e:
            print(f"✗ {name} FAILED: {e}\n")
            failed += 1
        except Exception as e:
            print(f"✗ {name} ERROR: {e}\n")
            failed += 1

    print("=" * 80)
    print(f"TEST RESULTS: {passed} passed, {failed} failed")
    print("=" * 80)

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
