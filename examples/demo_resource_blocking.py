#!/usr/bin/env python3
"""
Example script demonstrating resource blocking for faster page loads.

This script shows how to use the ResourceBlocker to:
1. Block images, fonts, and media for faster rendering
2. Block ads and trackers to reduce bandwidth
3. Track blocking statistics
"""

import asyncio
import sys

from markdown_ingress.core.renderer import Renderer


async def demo_resource_blocking():
    """Demonstrate resource blocking with before/after comparison."""

    test_url = "https://www.bbc.com/news"  # News site with lots of images/media

    print("=" * 80)
    print("Resource Blocking Demo")
    print("=" * 80)
    print()

    # Test 1: Without resource blocking
    print("Test 1: Rendering WITHOUT resource blocking...")
    print("-" * 80)

    renderer_no_block = Renderer(timeout=30.0, wait_until="domcontentloaded", block_resources=False)

    try:
        result_no_block = await renderer_no_block.render(test_url)
        print("✓ Success!")
        print(f"  Time: {result_no_block.timing_ms:.0f}ms")
        print(f"  HTML size: {len(result_no_block.html):,} bytes")
        print(f"  Status: {result_no_block.status_code}")
        print()
    except Exception as e:
        print(f"✗ Failed: {e}")
        result_no_block = None

    # Test 2: With resource blocking
    print("Test 2: Rendering WITH resource blocking...")
    print("-" * 80)

    renderer_with_block = Renderer(
        timeout=30.0,
        wait_until="domcontentloaded",
        block_resources=True,
        block_images=True,
        block_fonts=True,
        block_media=True,
        block_ads=True,
        block_trackers=True,
    )

    try:
        result_with_block = await renderer_with_block.render(test_url)
        print("✓ Success!")
        print(f"  Time: {result_with_block.timing_ms:.0f}ms")
        print(f"  HTML size: {len(result_with_block.html):,} bytes")
        print(f"  Status: {result_with_block.status_code}")

        # Show blocking statistics
        if "blocked_requests" in result_with_block.metadata:
            print()
            print("  Blocking Statistics:")
            print(f"    Total requests: {result_with_block.metadata['total_requests']}")
            print(f"    Blocked requests: {result_with_block.metadata['blocked_requests']}")
            print(f"    Block rate: {result_with_block.metadata['block_rate_pct']:.1f}%")

            if result_with_block.metadata.get("blocked_by_type"):
                print("    Blocked by type:")
                for rtype, count in result_with_block.metadata["blocked_by_type"].items():
                    print(f"      - {rtype}: {count}")
        print()
    except Exception as e:
        print(f"✗ Failed: {e}")
        result_with_block = None

    # Comparison
    if result_no_block and result_with_block:
        print("=" * 80)
        print("Comparison")
        print("=" * 80)

        time_diff = result_no_block.timing_ms - result_with_block.timing_ms
        time_pct = (
            (time_diff / result_no_block.timing_ms * 100) if result_no_block.timing_ms > 0 else 0
        )

        print(f"Time saved: {time_diff:.0f}ms ({time_pct:.1f}% faster)")
        print(f"Without blocking: {result_no_block.timing_ms:.0f}ms")
        print(f"With blocking:    {result_with_block.timing_ms:.0f}ms")
        print()

        if "blocked_requests" in result_with_block.metadata:
            blocked = result_with_block.metadata["blocked_requests"]
            total = result_with_block.metadata["total_requests"]
            block_rate = result_with_block.metadata["block_rate_pct"]
            print(f"Blocked {blocked} out of {total} requests ({block_rate:.1f}%)")

    print("=" * 80)


def main():
    """Run the demo."""
    try:
        asyncio.run(demo_resource_blocking())
    except KeyboardInterrupt:
        print("\n\nDemo interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nDemo failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
