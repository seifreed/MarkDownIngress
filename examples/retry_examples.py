#!/usr/bin/env python3
"""
Example usage of retry_ingest with exponential backoff
"""

from markdown_ingress import retry_ingest

print("Example: Basic retry usage")
print("=" * 60)

# Basic usage
doc = retry_ingest("https://example.com")

print("✅ Success!")
print(f"  Retry attempts: {doc.metadata['retry_attempts']}")
print(f"  Final timeout: {doc.metadata['final_timeout']}s")
print(f"  Stealth enabled: {doc.metadata['retry_enabled']}")
print(f"  Tokens: {doc.token_estimate}")
