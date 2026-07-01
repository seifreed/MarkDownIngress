#!/usr/bin/env python3
"""
Example: use MarkDownIngress as a normal PyPI library.
"""

import os

from markdown_ingress import IngestConfig, MemoryCache, ingest


def main() -> None:
    cache = MemoryCache()
    config = IngestConfig(
        mode="auto",
        timeout=20.0,
        cache=cache,
        policy_name="normal",
    )

    doc = ingest(os.getenv("MDI_EXAMPLE_URL", "https://example.com"), config=config)

    print(f"Title: {doc.metadata.get('title')}")
    print(f"Mode: {doc.metadata.get('mode')}")
    print(f"Tokens: {doc.token_estimate}")
    print(f"Injection score: {doc.injection_score:.3f}")


if __name__ == "__main__":
    main()
