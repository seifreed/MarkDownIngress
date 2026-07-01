#!/usr/bin/env python3
"""
Example usage of advanced stealth capabilities for bypassing bot detection.

This script demonstrates:
1. Basic usage of AdvancedStealthRenderer
2. Custom stealth configuration
3. Testing against bot detection services
4. Integration with existing MarkDownIngress workflow
"""

import asyncio
import os
import sys
from pathlib import Path


def _ensure_source_checkout_importable() -> None:
    """Allow direct execution from a source checkout without import-time side effects."""
    repo_root = str(Path(__file__).resolve().parent.parent)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)


if __name__ == "__main__":
    _ensure_source_checkout_importable()

from markdown_ingress.adapters.rendering.advanced_stealth_renderer import (  # noqa: E402
    AdvancedStealthRenderer,
)
from markdown_ingress.core.stealth import (  # noqa: E402
    STEALTH_JS_INJECTION,
    ULTRA_STEALTH_ARGS,
    get_advanced_context_options,
    get_advanced_stealth_config,
    inject_stealth,
)


async def example_basic_usage():
    """Example 1: Basic usage with default settings"""
    print("=" * 80)
    print("Example 1: Basic Advanced Stealth Rendering")
    print("=" * 80)

    renderer = AdvancedStealthRenderer(
        timeout=30.0,
        headless=True,
        randomize_fingerprint=True,
    )

    # Test URL - bot detection testing service
    test_url = os.getenv("MDI_EXAMPLE_STEALTH_URL", "https://bot.sannysoft.com/")

    print(f"\nRendering: {test_url}")
    print("This site tests for bot detection indicators...")

    try:
        result = await renderer.render(test_url)

        print(f"\n✓ Status: {result.status_code}")
        print(f"✓ Final URL: {result.final_url}")
        print(f"✓ Timing: {result.timing_ms:.2f}ms")
        print(f"✓ HTML length: {len(result.html)} bytes")
        print("\nMetadata:")
        for key, value in result.metadata.items():
            print(f"  - {key}: {value}")

        # Check for bot detection indicators in HTML
        html_lower = result.html.lower()
        indicators = {
            "webdriver": "navigator.webdriver" in html_lower,
            "headless": "headless" in html_lower and "true" in html_lower,
            "automation": "automation" in html_lower,
        }

        print("\nBot Detection Indicators Found:")
        for indicator, found in indicators.items():
            status = "✗ DETECTED" if found else "✓ Clean"
            print(f"  {indicator}: {status}")

    except Exception as e:
        print(f"\n✗ Error: {e}")


async def example_custom_config():
    """Example 2: Custom stealth configuration"""
    print("\n" + "=" * 80)
    print("Example 2: Custom Stealth Configuration")
    print("=" * 80)

    # Create custom config
    config = get_advanced_stealth_config(
        randomize=True,
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
        ),
        viewport=(1920, 1080),
        timezone="America/New_York",
    )

    print("\nCustom Configuration:")
    print(f"  User Agent: {config.user_agent[:80]}...")
    print(f"  Viewport: {config.viewport_width}x{config.viewport_height}")
    print(f"  Device Scale: {config.device_scale_factor}")
    print(f"  Timezone: {config.timezone}")
    print(f"  Browser Args: {len(config.browser_args)} arguments")

    renderer = AdvancedStealthRenderer(
        timeout=30.0,
        headless=True,
        stealth_config=config,
    )

    test_url = os.getenv("MDI_EXAMPLE_STEALTH_URL", "https://www.google.com")

    print(f"\nRendering: {test_url}")

    try:
        result = await renderer.render(test_url)
        print("\n✓ Successfully rendered with custom config")
        print(f"✓ Status: {result.status_code}")
        print(f"✓ Timing: {result.timing_ms:.2f}ms")
    except Exception as e:
        print(f"\n✗ Error: {e}")


async def example_cloudflare_test():
    """Example 3: Testing against Cloudflare-protected site"""
    print("\n" + "=" * 80)
    print("Example 3: Cloudflare Challenge Test")
    print("=" * 80)

    renderer = AdvancedStealthRenderer(
        timeout=45.0,  # Longer timeout for challenges
        headless=True,
        randomize_fingerprint=True,
    )

    # Cloudflare test URL
    test_url = "https://nowsecure.nl"  # Cloudflare-protected test site

    print(f"\nAttempting to bypass Cloudflare on: {test_url}")
    print("This may take a moment...")

    try:
        result = await renderer.render(test_url)

        print(f"\n✓ Status: {result.status_code}")

        # Check if we got past Cloudflare
        if "cloudflare" in result.html.lower() and "checking" in result.html.lower():
            print("⚠ Still showing Cloudflare challenge page")
        elif result.status_code == 403:
            print("⚠ Blocked by Cloudflare (403)")
        else:
            print("✓ Successfully bypassed Cloudflare!")

        print(f"✓ Timing: {result.timing_ms:.2f}ms")

    except Exception as e:
        print(f"\n✗ Error: {e}")
        print(
            "Note: Some Cloudflare challenges require additional waiting or may not be bypassable"
        )


async def example_stealth_injection_manual():
    """Example 4: Manual stealth injection with custom Playwright code"""
    print("\n" + "=" * 80)
    print("Example 4: Manual Stealth Injection")
    print("=" * 80)

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("✗ Playwright not installed. Skipping this example.")
        return

    print("\nDemonstrating manual control with inject_stealth()...")

    async with async_playwright() as p:
        # Launch with ultra stealth args
        browser = await p.chromium.launch(
            headless=True,
            args=ULTRA_STEALTH_ARGS,
            ignore_default_args=["--enable-automation"],
        )

        # Create context with advanced options
        config = get_advanced_stealth_config()
        context_options = get_advanced_context_options(config)
        context = await browser.new_context(**context_options)

        # Create page and inject stealth
        page = await context.new_page()
        await inject_stealth(page)

        print(f"✓ Browser launched with {len(ULTRA_STEALTH_ARGS)} stealth arguments")
        print(f"✓ Stealth JavaScript injected ({len(STEALTH_JS_INJECTION)} bytes)")

        # Navigate to test page
        test_url = "https://arh.antoinevastel.com/bots/areyouheadless"
        print(f"\nNavigating to: {test_url}")

        try:
            await page.goto(test_url, timeout=30000)

            # Get the detection results
            html = await page.content()

            if "You are" in html:
                if "headless" in html.lower():
                    print("✗ Detected as headless")
                else:
                    print("✓ Not detected as headless!")

            print("✓ Page loaded successfully")

        except Exception as e:
            print(f"✗ Error during navigation: {e}")
        finally:
            await context.close()
            await browser.close()


async def example_comparison():
    """Example 5: Compare regular vs advanced stealth"""
    print("\n" + "=" * 80)
    print("Example 5: Regular vs Advanced Stealth Comparison")
    print("=" * 80)

    test_url = "https://bot.sannysoft.com/"

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("✗ Playwright not installed. Skipping this example.")
        return

    # Test 1: Regular browser (no stealth)
    print("\n[Test 1] Regular browser (no stealth):")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        try:
            await page.goto(test_url, timeout=30000)
            html = await page.content()

            # Check for webdriver detection
            webdriver_detected = "webdriver" in html.lower() and "true" in html.lower()
            print(f"  navigator.webdriver detected: {'YES ✗' if webdriver_detected else 'NO ✓'}")

        except Exception as e:
            print(f"  Error: {e}")
        finally:
            await context.close()
            await browser.close()

    # Test 2: Advanced stealth
    print("\n[Test 2] Advanced stealth renderer:")
    renderer = AdvancedStealthRenderer(timeout=30.0, headless=True)

    try:
        result = await renderer.render(test_url)

        # Check for webdriver detection
        webdriver_detected = "webdriver" in result.html.lower() and "true" in result.html.lower()
        print(f"  navigator.webdriver detected: {'YES ✗' if webdriver_detected else 'NO ✓'}")
        print(f"  Stealth mode: {result.metadata.get('stealth_injected', False)}")

    except Exception as e:
        print(f"  Error: {e}")


def show_config_info():
    """Display configuration information"""
    print("\n" + "=" * 80)
    print("Advanced Stealth Configuration Info")
    print("=" * 80)

    config = get_advanced_stealth_config()

    print(f"\nUltra Stealth Browser Arguments ({len(ULTRA_STEALTH_ARGS)} total):")
    for i, arg in enumerate(ULTRA_STEALTH_ARGS[:10], 1):
        print(f"  {i:2d}. {arg}")
    print(f"  ... and {len(ULTRA_STEALTH_ARGS) - 10} more")

    print("\nStealth JavaScript Injection:")
    print(f"  Size: {len(STEALTH_JS_INJECTION)} bytes")
    print(f"  Lines: {STEALTH_JS_INJECTION.count(chr(10))} lines")

    print("\nDefault Configuration:")
    print(f"  Default User Agent: {config.user_agent}")
    print(f"  Default Viewport: {config.viewport_width}x{config.viewport_height}")
    print(f"  Default Timezone: {config.timezone}")


async def main():
    """Run all examples"""
    print("\n" + "=" * 80)
    print("ADVANCED STEALTH CAPABILITIES DEMONSTRATION")
    print("=" * 80)
    print("\nThis script demonstrates the advanced stealth features for")
    print("bypassing Cloudflare, fingerprinting, and bot detection.")
    print()

    # Show configuration info first
    show_config_info()

    # Run examples
    examples = [
        ("Basic Usage", example_basic_usage),
        ("Custom Config", example_custom_config),
        ("Cloudflare Test", example_cloudflare_test),
        ("Manual Injection", example_stealth_injection_manual),
        ("Comparison", example_comparison),
    ]

    print("\n" + "=" * 80)
    print("Available Examples:")
    for i, (name, _) in enumerate(examples, 1):
        print(f"  {i}. {name}")
    print("=" * 80)

    # Run a subset of examples (not all require external sites)
    print("\nRunning safe local examples...")

    await example_custom_config()

    print("\n" + "=" * 80)
    print("To test against live sites, run specific examples:")
    print("  - example_basic_usage() - Bot detection test")
    print("  - example_cloudflare_test() - Cloudflare bypass test")
    print("  - example_comparison() - Side-by-side comparison")
    print("=" * 80)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
    except Exception as e:
        print(f"\n\nError: {e}")
        import traceback

        traceback.print_exc()
