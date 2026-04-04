#!/usr/bin/env python3
"""
Example: ingest multiple URLs concurrently from Python.
"""

import asyncio

from markdown_ingress import (
    IngestConfig,
    MemoryCache,
    get_ingest_stats,
    ingest_many_async,
    reset_ingest_stats,
)


async def main() -> None:
    reset_ingest_stats()
    cache = MemoryCache()
    config = IngestConfig(
        mode="fast",
        timeout=15.0,
        cache=cache,
        policy_name="normal",
    )

    urls = [
        "https://example.com",
        "https://example.org",
        "https://iana.org/domains/example",
    ]

    result = await ingest_many_async(urls, config=config, max_concurrent=3)

    print(f"Successful: {result.successful}")
    print(f"Failed: {result.failed}")

    for url, doc in zip(urls, result.documents, strict=False):
        if doc is None:
            print(f"FAIL {url}: {result.errors[url]}")
            continue
        print(f"OK {url}: {doc.token_estimate} tokens")

    stats = get_ingest_stats()
    print("\nProcess stats:")
    print(f"  Requests: {stats['requests_total']}")
    print(f"  Cache hits: {stats['cache_hits']}")
    print(f"  In-flight followers: {stats['inflight_followers']}")
    print(f"  Fast successes: {stats['mode_results']['fast']['success']}")
    print(f"  Fast avg ms: {stats['mode_timings_ms']['fast']['avg']:.2f}")


if __name__ == "__main__":
    asyncio.run(main())
