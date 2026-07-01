#!/usr/bin/env python3
"""
Example: ingest multiple URLs concurrently from Python.
"""

import asyncio
import os

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

    urls = os.getenv("MDI_EXAMPLE_URLS")
    url_list = (
        urls.split(",")
        if urls
        else [
            "https://example.com",
            "https://example.org",
            "https://iana.org/domains/example",
        ]
    )

    result = await ingest_many_async(url_list, config=config, max_concurrent=3)

    print(f"Successful: {result.successful}")
    print(f"Failed: {result.failed}")

    errors_by_index = {error.index: error.error for error in result.error_items}

    for index, (url, doc) in enumerate(zip(url_list, result.documents, strict=False)):
        if doc is None:
            print(f"FAIL {url}: {errors_by_index.get(index, 'unknown error')}")
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
