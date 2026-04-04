# Changelog

## 0.8.0 - 2026-03-09

- Added output profiles: `llm_safe`, `rag_chunkable`, `for_search`, `for_archive`.
- Added domain-specific runtime policies with granular DOM filtering via allowed/blocked tags and selectors.
- Added structured block extraction and native chunking with hashes, offsets and token metadata.
- Improved Markdown preservation for technical content including fenced code blocks and tables.
- Expanded language metadata and multilingual normalization signals.
- Added security explainability and richer observability for stage timings, policy actions and render cost tracking.
- Added persistent API batch jobs with polling, TTL cleanup and optional webhooks.
- Added extractor comparison utilities in library, CLI and HTTP API.
- Expanded CLI with profile, chunking, domain-policy, compare and benchmark flows.
- Added offline fixtures and broader regression coverage for profiles, API, queue and domain rules.

## 0.7.0

- Added auto mode, advanced security hooks, metadata/link extraction, API and CLI improvements.
