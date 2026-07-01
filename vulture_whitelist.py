"""Vulture allowlist — verified false positives for dead-code scanning.

Each entry below is a symbol vulture reports as unused but that IS used,
just not in a way vulture's static pass can see. Verified one-by-one
(see commit history of the dead-code sweep). Categories:

  * pydantic: ``model_config`` and ``validate_*`` validators are invoked by
    pydantic/FastAPI, not by direct calls.
  * Protocol/interface methods (``IFetcher.fetch``, ``IJobQueue``, ...) are
    structural contracts implemented elsewhere.
  * Framework-read attributes: ``row_factory`` (sqlite3), ``sock``
    (http.client), ``__cause__`` (traceback machinery), ``__exit__`` params.
  * Public API in ``__all__`` (``process_batch``, ``Plugin``/``PluginLoader``
    methods, ``Policy.get_action``, ``Benchmark.compare_modes``, ...) and
    response-model fields serialized into API output.
  * ``__getattr__`` lazy-import seam; dataclass/TypedDict fields surfaced via
    ``asdict``/serialization.

Usage — scan for NEW dead code (this file suppresses the known FPs):

    vulture markdown_ingress mcp_server.py vulture_whitelist.py

A genuinely-dead symbol will appear in the output because it will NOT be
listed here. When you intentionally remove or rename a whitelisted symbol,
delete its line below too.
"""

exc_tb  # unused variable (markdown_ingress/adapters/cache/sqlite.py:342)
exc_type  # unused variable (markdown_ingress/adapters/cache/sqlite.py:342)
exc_val  # unused variable (markdown_ingress/adapters/cache/sqlite.py:342)
markdown_length  # unused variable (markdown_ingress/adapters/extractors/comparison.py:21)
heading_count  # unused variable (markdown_ingress/adapters/extractors/comparison.py:23)
link_count  # unused variable (markdown_ingress/adapters/extractors/comparison.py:24)
table_count  # unused variable (markdown_ingress/adapters/extractors/comparison.py:26)
transport_url  # unused variable (markdown_ingress/adapters/fetching/http_support.py:52)
_.fetch  # unused method (markdown_ingress/adapters/fetching/httpx_fetch_async.py:32)
_.row_factory  # unused attribute (markdown_ingress/adapters/jobs/sqlite_job_queue.py:128)
_.row_factory  # unused attribute (markdown_ingress/adapters/jobs/sqlite_job_queue.py:346)
_._prepare_html  # unused method (markdown_ingress/adapters/markdown/markdownify_converter.py:59)
_.sock  # unused attribute (markdown_ingress/adapters/webhooks/http_notifier.py:71)
model_config  # unused variable (markdown_ingress/api_server_models.py:108)
_.validate_policy_name  # unused method (markdown_ingress/api_server_models.py:130)
_.validate_output_profile  # unused method (markdown_ingress/api_server_models.py:135)
model_config  # unused variable (markdown_ingress/api_server_models.py:144)
_.validate_output_formats  # unused method (markdown_ingress/api_server_models.py:183)
_.validate_screenshot  # unused method (markdown_ingress/api_server_models.py:188)
_.validate_policy_name  # unused method (markdown_ingress/api_server_models.py:193)
_.validate_output_profile  # unused method (markdown_ingress/api_server_models.py:198)
_.validate_reports_dir  # unused method (markdown_ingress/api_server_models.py:203)
_.validate_url_ssrf  # unused method (markdown_ingress/api_server_models.py:212)
model_config  # unused variable (markdown_ingress/api_server_models.py:220)
_.validate_timeout_bounds  # unused method (markdown_ingress/api_server_models.py:233)
_.validate_url_ssrf  # unused method (markdown_ingress/api_server_models.py:239)
_.validate_urls_ssrf  # unused method (markdown_ingress/api_server_models.py:251)
success_count  # unused variable (markdown_ingress/api_server_models.py:286)
failure_count  # unused variable (markdown_ingress/api_server_models.py:287)
poll_url  # unused variable (markdown_ingress/api_server_models.py:318)
expires_in_seconds  # unused variable (markdown_ingress/api_server_models.py:319)
ttl_applies_to  # unused variable (markdown_ingress/api_server_models.py:320)
model_config  # unused variable (markdown_ingress/api_server_models.py:334)
_.row_factory  # unused attribute (markdown_ingress/api_server_queue.py:192)
current_db_path  # unused variable (markdown_ingress/api_server_snapshot.py:19)
pending_visible_total  # unused variable (markdown_ingress/api_server_snapshot.py:24)
repair_in_progress  # unused variable (markdown_ingress/api_server_snapshot.py:31)
_.process_batch  # unused method (markdown_ingress/application/batch.py:205)
IngestArgs  # unused class (markdown_ingress/cli_parsing.py:37)
no_metadata  # unused variable (markdown_ingress/cli_parsing.py:53)
no_links  # unused variable (markdown_ingress/cli_parsing.py:54)
_.compare_modes  # unused method (markdown_ingress/core/benchmark.py:211)
__getattr__  # unused function (markdown_ingress/core/config.py:278)
load_config  # unused function (markdown_ingress/core/config.py:263)
_.__cause__  # unused attribute (markdown_ingress/core/exception_copy.py:68)
_.fetch  # unused method (markdown_ingress/core/interfaces.py:20)
IJobQueue  # unused class (markdown_ingress/core/interfaces.py:203)
IBatchIngestUseCase  # unused class (markdown_ingress/core/interfaces.py:264)
_.seed  # unused attribute (markdown_ingress/core/metadata_extractor.py:97)
_.is_available  # unused method (markdown_ingress/core/nova_guard.py:322)
_.get_config  # unused method (markdown_ingress/core/plugin.py:47)
_.get_plugin  # unused method (markdown_ingress/core/plugin.py:196)
_.list_plugins  # unused method (markdown_ingress/core/plugin.py:208)
_.get_action  # unused method (markdown_ingress/core/policy.py:265)
_.reset_stats  # unused method (markdown_ingress/core/resource_blocker.py:360)
_.success_rate  # unused property (markdown_ingress/shared_results.py:82)
fetch_url  # unused function (mcp_server.py:31) — registered via @mcp.tool() decorator
