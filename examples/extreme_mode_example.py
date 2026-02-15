#!/usr/bin/env python3
"""
Example: Using Extreme Mode for Very Slow or Protected Sites

This example demonstrates how to use extreme_mode for sites that:
- Take 2-5 minutes to load
- Have heavy JavaScript execution
- Use aggressive bot protection
- Have complex SPAs with delayed content loading

Extreme mode provides:
- Progressive timeout strategy (90s → 180s → 300s)
- Smart content waiting with multiple selector fallbacks
- Automatic retry with increasing patience
"""

from markdown_ingress import ingest, retry_ingest

# Example 1: Direct extreme mode usage
def example_basic_extreme_mode():
    """Use extreme mode for a single site"""
    print("Example 1: Basic extreme mode")
    print("-" * 50)
    
    url = "https://very-slow-spa-site.com"
    
    # Enable extreme mode directly
    doc = ingest(
        url=url,
        mode="render",
        extreme_mode=True,  # Enable extreme patience
        timeout=300.0,      # Maximum timeout (5 minutes)
    )
    
    print(f"Success! Got {doc.token_estimate} tokens")
    print(f"Strategy used: {doc.metadata.get('strategy_used', 'N/A')}")
    print(f"Timeout used: {doc.metadata.get('timeout_used_ms', 'N/A')}ms")
    print()


# Example 2: Using retry_ingest with automatic extreme mode on last attempt
def example_retry_with_extreme():
    """Retry with automatic extreme mode escalation"""
    print("Example 2: Retry with extreme mode escalation")
    print("-" * 50)
    
    url = "https://protected-site.com"
    
    # retry_ingest automatically enables extreme mode on the last attempt
    doc = retry_ingest(
        url=url,
        mode="render",
        max_retries=3,          # Try 3 times
        enable_stealth=True,    # Enable stealth on retries
        initial_timeout=60.0,   # Start with 60s, escalate to 90s, 120s
    )
    # Last attempt will use:
    # - timeout=120s
    # - stealth=True
    # - extreme_mode=True (automatic!)
    
    print(f"Success after {doc.metadata['retry_attempts']} attempts")
    print(f"Extreme mode used: {doc.metadata.get('extreme_mode_enabled', False)}")
    print(f"Final timeout: {doc.metadata['final_timeout']}s")
    print()


# Example 3: Manual progressive strategy
def example_manual_progressive():
    """Try normal mode first, then extreme mode"""
    print("Example 3: Manual progressive approach")
    print("-" * 50)
    
    url = "https://sometimes-slow-site.com"
    
    try:
        # First attempt: Normal mode
        print("Trying normal mode...")
        doc = ingest(url=url, mode="render", timeout=30.0)
        print("Success with normal mode!")
        
    except Exception as e:
        print(f"Normal mode failed: {e}")
        print("Switching to extreme mode...")
        
        # Second attempt: Extreme mode
        doc = ingest(
            url=url,
            mode="render",
            extreme_mode=True,
            timeout=300.0,
            stealth=True,  # Also enable stealth
        )
        print("Success with extreme mode!")
    
    print(f"Got {doc.token_estimate} tokens")
    print()


# Example 4: Checking which strategy worked
def example_check_strategy():
    """Check which timeout strategy was successful"""
    print("Example 4: Checking successful strategy")
    print("-" * 50)
    
    url = "https://slow-site.com"
    
    doc = ingest(url=url, mode="render", extreme_mode=True)
    
    if doc.metadata.get('extreme_mode'):
        strategy = doc.metadata.get('strategy_used')
        attempt = doc.metadata.get('strategy_attempt')
        timeout_ms = doc.metadata.get('timeout_used_ms')
        
        print(f"✓ Extreme mode was used")
        print(f"  Strategy: {strategy}")
        print(f"  Attempt: {attempt}/3")
        print(f"  Timeout: {timeout_ms/1000:.0f}s")
        
        # Strategy meanings:
        if strategy == 'networkidle':
            print("  → Site loaded quickly (network became idle)")
        elif strategy == 'domcontentloaded':
            print("  → Site needed moderate time (DOM loaded)")
        elif strategy == 'load':
            print("  → Site needed maximum time (full page load)")
    else:
        print("Normal mode was sufficient")
    
    print()


# Example 5: Combining all protection measures
def example_maximum_protection():
    """Use all available protection measures for extremely difficult sites"""
    print("Example 5: Maximum protection stack")
    print("-" * 50)
    
    url = "https://heavily-protected-site.com"
    
    # Stack all available features:
    doc = retry_ingest(
        url=url,
        mode="render",
        max_retries=5,           # More attempts
        enable_stealth=True,     # Stealth mode
        initial_timeout=90.0,    # Higher initial timeout
    )
    # This will try:
    # 1. Normal render (90s, no stealth)
    # 2. Stealth mode (120s, stealth=True)
    # 3. Stealth mode (150s, stealth=True)
    # 4. Stealth mode (180s, stealth=True)
    # 5. EXTREME mode (210s, stealth=True, extreme_mode=True)
    #    → Progressive: 90s → 180s → 300s
    
    print(f"Success!")
    print(f"Total attempts: {doc.metadata['retry_attempts']}")
    print(f"Stealth used: {doc.metadata['retry_enabled']}")
    print(f"Extreme mode: {doc.metadata.get('extreme_mode_enabled', False)}")
    print(f"Final timeout: {doc.metadata['final_timeout']}s")
    print()


def print_strategy_details():
    """Print details about the progressive timeout strategies"""
    print("=" * 70)
    print("EXTREME MODE STRATEGIES")
    print("=" * 70)
    print()
    print("Progressive Timeout Strategies (tried in order):")
    print()
    print("1. networkidle (90 seconds)")
    print("   - Waits for network to be idle (no requests for 500ms)")
    print("   - Best for sites with async content loading")
    print()
    print("2. domcontentloaded (180 seconds)")
    print("   - Waits for DOM to be fully parsed")
    print("   - Good for sites that load resources after DOM ready")
    print()
    print("3. load (300 seconds)")
    print("   - Waits for complete page load (including images, styles)")
    print("   - Maximum patience for extremely slow sites")
    print()
    print("Smart Content Waiting:")
    print("  • Tries multiple content selectors: article, main, [role=\"main\"],")
    print("    .content, #content, body")
    print("  • Waits for meaningful text content (>50 characters)")
    print("  • Checks for loading indicators to disappear")
    print("  • Validates page has actual content elements")
    print()
    print("=" * 70)
    print()


if __name__ == "__main__":
    print_strategy_details()
    
    print("NOTE: These examples use placeholder URLs.")
    print("Replace with real URLs to test extreme mode functionality.")
    print()
    print("Uncomment examples below to run them:")
    print()
    
    # Uncomment to run examples:
    # example_basic_extreme_mode()
    # example_retry_with_extreme()
    # example_manual_progressive()
    # example_check_strategy()
    # example_maximum_protection()
    
    print("Examples available:")
    print("  1. example_basic_extreme_mode() - Direct extreme mode usage")
    print("  2. example_retry_with_extreme() - Automatic escalation")
    print("  3. example_manual_progressive() - Manual fallback")
    print("  4. example_check_strategy() - Check which strategy worked")
    print("  5. example_maximum_protection() - All features combined")
