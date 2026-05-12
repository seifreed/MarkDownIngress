"""
Tests for configuration file support
"""

import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from markdown_ingress.config_models import DomainPolicy, IngestConfig, RenderConfig
from markdown_ingress.core.config import Config, ConfigLoader, load_config


class TestConfig:
    """Test Config dataclass"""

    def test_config_defaults(self):
        """Config has sensible defaults"""
        config = Config()

        assert config.mode == "auto"
        assert config.timeout == 30.0
        assert config.strict is True
        assert config.model == "gpt-4"
        assert config.cache_enabled is False
        assert config.batch_max_concurrent == 5
        assert config.policy == "normal"

    def test_config_to_dict(self):
        """Convert Config to dictionary"""
        config = Config(mode="render", timeout=60.0, strict=False)
        data = config.to_dict()

        assert isinstance(data, dict)
        assert data["mode"] == "render"
        assert data["timeout"] == 60.0
        assert data["strict"] is False

    def test_config_to_json(self):
        """Export Config as JSON"""
        config = Config(model="gpt-4", cache_enabled=True)
        json_str = config.to_json()

        assert '"model": "gpt-4"' in json_str
        assert '"cache_enabled": true' in json_str

    def test_config_to_yaml(self):
        """Export Config as YAML"""
        config = Config(mode="fast", timeout=45.0)
        yaml_str = config.to_yaml()

        assert "mode: fast" in yaml_str
        assert "timeout:" in yaml_str

    def test_config_serialization_remains_safe_after_cache_initialization(self):
        config = Config(cache_enabled=True, cache_type="memory")

        config.create_cache()
        ingest_config = config.to_ingest_config()
        data = config.to_dict()

        assert ingest_config.cache is not None
        assert data["cache_enabled"] is True
        assert "_cache_backend" not in data
        assert '"cache_enabled": true' in config.to_json()
        assert "cache_enabled: true" in config.to_yaml()

    def test_create_cache_recreates_backend_when_settings_change(self, tmp_path):
        config = Config(cache_enabled=True, cache_type="memory", cache_ttl=60)

        first = config.create_cache()
        config.cache_type = "sqlite"
        config.cache_path = str(tmp_path / "config-cache.db")
        config.cache_ttl = 120
        second = config.create_cache()

        assert first is not second
        assert first.__class__.__name__ == "MemoryCache"
        assert second.__class__.__name__ == "SQLiteCache"
        assert second.default_ttl == 120

    def test_create_cache_expands_tilde_for_sqlite_backend(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        config = Config(cache_enabled=True, cache_type="sqlite", cache_path="~/.cache/mdi.sqlite")

        cache = config.create_cache()
        expected_path = (tmp_path / ".cache" / "mdi.sqlite").resolve()

        assert cache is not None
        assert cache.db_path == expected_path
        assert config._cache_backend_settings == ("sqlite", str(expected_path), 3600)

    def test_create_cache_reenable_recreates_closed_sqlite_backend(self, tmp_path):
        config = Config(
            cache_enabled=True,
            cache_type="sqlite",
            cache_path=str(tmp_path / "toggle-cache.db"),
        )

        first = config.create_cache()
        first.close()
        config.cache_enabled = False
        assert config.create_cache() is None

        config.cache_enabled = True
        second = config.create_cache()

        assert second is not first
        assert second.__class__.__name__ == "SQLiteCache"
        assert second.get("missing") is None

    def test_config_to_ingest_config_preserves_output_format_and_explicit_defaults(self):
        config = Config(output_profile="rag_chunkable", extract_blocks=False, output_format="json")

        ingest_config = config.to_ingest_config()
        resolved = ingest_config.apply_output_profile()

        assert ingest_config.output_format == "json"
        assert "extract_blocks" in ingest_config.explicit_keys()
        assert "output_format" in ingest_config.explicit_keys()
        assert resolved.extract_blocks is False

    def test_config_to_ingest_config_propagates_report_settings(self):
        config = Config(save_reports=True, reports_dir="reports-x")

        ingest_config = config.to_ingest_config()

        assert ingest_config.save_reports is True
        assert ingest_config.reports_dir == "reports-x"
        assert "save_reports" in ingest_config.explicit_keys()
        assert "reports_dir" in ingest_config.explicit_keys()

    def test_config_to_ingest_config_accepts_typed_domain_policy_objects(self):
        config = Config(
            domain_policies=[DomainPolicy(domain="example.com", output_profile="for_archive")]
        )

        ingest_config = config.to_ingest_config()

        assert len(ingest_config.domain_policies) == 1
        assert ingest_config.domain_policies[0].domain == "example.com"
        assert ingest_config.domain_policies[0].output_profile == "for_archive"
        assert "domain_policies" in ingest_config.explicit_keys()

    def test_config_rejects_unknown_output_profile_early(self):
        with pytest.raises(ValueError, match="Unknown output profile 'bogus'"):
            Config(output_profile="bogus")

    def test_domain_policy_rejects_unknown_output_profile_early(self):
        with pytest.raises(ValueError, match="Unknown output profile 'bogus'"):
            DomainPolicy(domain="example.com", output_profile="bogus")

    def test_domain_policy_rejects_invalid_policy_name_early(self):
        with pytest.raises(ValueError, match="Invalid policy_name 'bogus'"):
            DomainPolicy(domain="example.com", policy_name="bogus")

    def test_domain_policy_rejects_invalid_request_interval_early(self):
        with pytest.raises(ValueError, match="DomainPolicy.request_interval must be >= 0.0"):
            DomainPolicy(domain="example.com", request_interval=-1.0)

    def test_domain_policy_rejects_invalid_render_cost_budget_early(self):
        with pytest.raises(
            ValueError, match="DomainPolicy.render_cost_budget must be >= 1 when provided"
        ):
            DomainPolicy(domain="example.com", render_cost_budget=0)

    def test_domain_policy_rejects_invalid_timeout_early(self):
        with pytest.raises(ValueError, match="DomainPolicy.timeout must be > 0.0"):
            DomainPolicy(domain="example.com", timeout=0.0)

    def test_domain_policy_rejects_invalid_auto_render_threshold_early(self):
        with pytest.raises(ValueError, match="DomainPolicy.auto_render_threshold must be >= 1"):
            DomainPolicy(domain="example.com", auto_render_threshold=0)

    @pytest.mark.parametrize(
        ("field_name", "value"),
        [
            ("allowed_tags", "article"),
            ("blocked_tags", "form"),
            ("blocked_selectors", ".ads"),
            ("unwrap_selectors", "div.wrapper"),
        ],
    )
    def test_domain_policy_rejects_scalar_string_rule_lists_early(self, field_name, value):
        with pytest.raises(ValueError, match=rf"{field_name} must be a list of strings"):
            DomainPolicy(domain="example.com", **{field_name: value})

    def test_config_from_dict(self):
        """Create Config from dictionary"""
        data = {
            "mode": "render",
            "timeout": 120.0,
            "strict": False,
            "model": "claude-3",
            "cache_enabled": True,
        }

        config = Config.from_dict(data)

        assert config.mode == "render"
        assert config.timeout == 120.0
        assert config.strict is False
        assert config.model == "claude-3"
        assert config.cache_enabled is True

    def test_config_from_json(self):
        """Load Config from JSON string"""
        json_str = '{"mode": "fast", "timeout": 30.0, "strict": true, "model": "gpt-4"}'
        config = Config.from_json(json_str)

        assert config.mode == "fast"
        assert config.timeout == 30.0
        assert config.strict is True
        assert config.model == "gpt-4"

    def test_config_from_yaml(self):
        """Load Config from YAML string"""
        yaml_str = """
mode: render
timeout: 60.0
strict: false
model: gpt-3.5
cache_enabled: true
"""
        config = Config.from_yaml(yaml_str)

        assert config.mode == "render"
        assert config.timeout == 60.0
        assert config.strict is False
        assert config.model == "gpt-3.5"
        assert config.cache_enabled is True

    @pytest.mark.parametrize(
        ("loader", "payload", "kind"),
        [
            (Config.from_json, "[]", "list"),
            (Config.from_json, '"oops"', "str"),
            (Config.from_json, "123", "int"),
            (Config.from_yaml, "[]", "list"),
        ],
    )
    def test_config_rejects_non_mapping_roots(self, loader, payload, kind):
        with pytest.raises(ValueError, match=f"got {kind}"):
            loader(payload)

    def test_domain_policy_matches_ipv6_and_ports(self):
        policy = DomainPolicy(domain="2001:db8::1")

        assert policy.matches("http://[2001:db8::1]/page") is True
        assert policy.matches("http://[2001:db8::1]:8080/page") is True
        assert policy.matches("http://example.com./page") is False
        assert policy.matches("http://[2001:db8::2]/page") is False

    def test_domain_policy_matches_trailing_dot_hosts(self):
        policy = DomainPolicy(domain="example.com")

        assert policy.matches("http://example.com./page") is True
        assert policy.matches("http://EXAMPLE.COM./page") is True

    def test_domain_policy_matches_url_like_domain_inputs(self):
        policy = DomainPolicy(domain="https://example.com/path")

        assert policy.matches("http://example.com/other") is True
        assert policy.matches("https://sub.example.com/page") is True

    @pytest.mark.parametrize(
        ("block_threshold", "warn_threshold", "field_name"),
        [
            (1.5, 0.2, "block_threshold"),
            (-0.1, 0.2, "block_threshold"),
            (0.5, 1.5, "warn_threshold"),
            (0.5, -0.1, "warn_threshold"),
        ],
    )
    def test_domain_policy_rejects_out_of_range_thresholds(
        self,
        block_threshold,
        warn_threshold,
        field_name,
    ):
        with pytest.raises(ValueError, match=field_name):
            DomainPolicy(
                domain="example.com",
                block_threshold=block_threshold,
                warn_threshold=warn_threshold,
            )

    @pytest.mark.parametrize(
        ("payload", "message"),
        [
            ("custom_patterns: abc\n", "custom_patterns must be a list of strings"),
            (
                "domain_policies:\n  - bad\n",
                r"domain_policies\[0\] must be a mapping or DomainPolicy",
            ),
        ],
    )
    def test_config_rejects_invalid_legacy_collection_shapes(self, payload, message):
        with pytest.raises(ValueError, match=message):
            Config.from_yaml(payload)

    def test_config_from_yaml_rejects_unknown_output_profile_early(self):
        with pytest.raises(ValueError, match="Unknown output profile 'bogus'"):
            Config.from_yaml("output_profile: bogus\n")

    @pytest.mark.parametrize(
        ("factory", "message"),
        [
            (
                lambda: Config(timeout=cast(Any, "abc")),
                "timeout must be a finite number",
            ),
            (
                lambda: Config(strict=cast(Any, "false")),
                "strict must be a bool",
            ),
            (
                lambda: Config(cache_ttl=cast(Any, True)),
                "cache_ttl must be an int",
            ),
            (
                lambda: Config(timeout=cast(Any, float("nan"))),
                "timeout must be a finite number",
            ),
            (
                lambda: Config(screenshot=cast(Any, 123)),
                "screenshot must be a bool, string, or None",
            ),
            (
                lambda: IngestConfig(timeout=cast(Any, "abc")),
                "timeout must be a finite number",
            ),
            (
                lambda: IngestConfig(extract_blocks=cast(Any, "false")),
                "extract_blocks must be a bool",
            ),
            (
                lambda: IngestConfig(auto_render_threshold=cast(Any, 1.5)),
                "auto_render_threshold must be an int",
            ),
            (
                lambda: IngestConfig(custom_patterns=cast(Any, "abc")),
                "custom_patterns must be a list of strings",
            ),
            (
                lambda: IngestConfig(plugin_dirs=cast(Any, "plugins")),
                "plugin_dirs must be a list of strings",
            ),
            (
                lambda: IngestConfig(domain_policies=cast(Any, {"domain": "example.com"})),
                "domain_policies must be a list",
            ),
            (
                lambda: IngestConfig(custom_patterns=cast(Any, ["("])),
                "Invalid regex pattern",
            ),
            (
                lambda: DomainPolicy(domain="example.com", timeout=cast(Any, "abc")),
                "DomainPolicy.timeout must be a finite number",
            ),
            (
                lambda: DomainPolicy(domain=cast(Any, 123)),
                "DomainPolicy.domain must be a string",
            ),
            (
                lambda: DomainPolicy(domain="example.com", strict=cast(Any, "false")),
                "DomainPolicy.strict must be a bool",
            ),
            (
                lambda: RenderConfig(timeout=cast(Any, "abc")),
                "timeout must be a finite number",
            ),
            (
                lambda: RenderConfig(headless=cast(Any, "false")),
                "headless must be a bool",
            ),
            (
                lambda: RenderConfig(wait_until=cast(Any, 123)),
                "wait_until must be a string",
            ),
        ],
    )
    def test_config_objects_reject_invalid_scalar_types(
        self,
        factory: Callable[[], object],
        message: str,
    ):
        with pytest.raises(ValueError, match=message):
            factory()

    def test_ingest_config_normalizes_domain_policy_mappings(self):
        config = IngestConfig(domain_policies=[cast(Any, {"domain": "example.com"})])

        assert len(config.domain_policies) == 1
        assert isinstance(config.domain_policies[0], DomainPolicy)
        assert config.domain_policies[0].matches("https://example.com/page") is True


class TestConfigLoader:
    """Test ConfigLoader functionality"""

    def test_load_from_json_file(self):
        """Load configuration from JSON file"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"
            config_path.write_text('{"mode": "render", "timeout": 90.0, "model": "gpt-4"}')

            loader = ConfigLoader(str(config_path))
            config = loader.load()

            assert config.mode == "render"
            assert config.timeout == 90.0
            assert config.model == "gpt-4"

    def test_load_from_yaml_file(self):
        """Load configuration from YAML file"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text("""
mode: fast
timeout: 45.0
strict: false
cache_enabled: true
batch_max_concurrent: 10
""")

            loader = ConfigLoader(str(config_path))
            config = loader.load()

            assert config.mode == "fast"
            assert config.timeout == 45.0
            assert config.strict is False
            assert config.cache_enabled is True
            assert config.batch_max_concurrent == 10

    def test_loader_preserves_runtime_fields_supported_by_ingest_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text(
                "plugin_dirs:\n"
                "  - /tmp/plugins\n"
                "render_cost_budget: 7\n"
                "include_observability: false\n"
            )

            config = ConfigLoader(str(config_path)).load()
            ingest_config = config.to_ingest_config()

            assert config.plugin_dirs == ["/tmp/plugins"]
            assert config.render_cost_budget == 7
            assert config.include_observability is False
            assert ingest_config.plugin_dirs == ["/tmp/plugins"]
            assert ingest_config.render_cost_budget == 7
            assert ingest_config.include_observability is False
            assert "plugin_dirs" in ingest_config.explicit_keys()
            assert "render_cost_budget" in ingest_config.explicit_keys()
            assert "include_observability" in ingest_config.explicit_keys()

    def test_loader_preserves_additional_runtime_fields_supported_by_ingest_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text(
                "auto_render_threshold: 999\n"
                "extract_metadata: false\n"
                "extract_links: false\n"
                "advanced_security: true\n"
                "use_llm: true\n"
                "detect_language: false\n"
                "normalize_multilingual: false\n"
                "include_security_explanation: false\n"
                "output_formats:\n"
                "  - markdown\n"
                "  - security\n"
                "fetcher_user_agent: test-agent\n"
            )

            config = ConfigLoader(str(config_path)).load()
            ingest_config = config.to_ingest_config()

            assert config.auto_render_threshold == 999
            assert config.extract_metadata is False
            assert config.extract_links is False
            assert config.advanced_security is True
            assert config.use_llm is True
            assert config.detect_language is False
            assert config.normalize_multilingual is False
            assert config.include_security_explanation is False
            assert config.output_formats == ["markdown", "security"]
            assert config.fetcher_user_agent == "test-agent"
            assert ingest_config.auto_render_threshold == 999
            assert ingest_config.extract_metadata is False
            assert ingest_config.extract_links is False
            assert ingest_config.advanced_security is True
            assert ingest_config.use_llm is True
            assert ingest_config.detect_language is False
            assert ingest_config.normalize_multilingual is False
            assert ingest_config.include_security_explanation is False
            assert ingest_config.output_formats == ["markdown", "security"]
            assert ingest_config.fetcher_user_agent == "test-agent"
            for key in (
                "auto_render_threshold",
                "extract_metadata",
                "extract_links",
                "advanced_security",
                "use_llm",
                "detect_language",
                "normalize_multilingual",
                "include_security_explanation",
                "output_formats",
                "fetcher_user_agent",
            ):
                assert key in ingest_config.explicit_keys()

    def test_config_accepts_policy_name_alias(self):
        config = Config.from_yaml("policy_name: strict\n")

        assert config.policy == "strict"
        assert config.to_ingest_config().policy_name == "strict"

    def test_config_rejects_conflicting_policy_aliases(self):
        with pytest.raises(
            ValueError,
            match="Config cannot define both policy and policy_name with different values",
        ):
            Config.from_yaml("policy: normal\npolicy_name: strict\n")

    def test_load_from_yml_extension(self):
        """Load configuration from .yml file"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yml"
            config_path.write_text("mode: render\ntimeout: 120.0\n")

            loader = ConfigLoader(str(config_path))
            config = loader.load()

            assert config.mode == "render"
            assert config.timeout == 120.0

    def test_load_file_not_found(self):
        """Raise error if config file doesn't exist"""
        with pytest.raises(FileNotFoundError):
            loader = ConfigLoader("/nonexistent/config.yaml")
            loader.load()

    def test_load_rejects_non_mapping_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text("[]")

            loader = ConfigLoader(str(config_path))

            with pytest.raises(ValueError, match="Config data must be a JSON/YAML object"):
                loader.load()

    def test_env_overrides(self):
        """Environment variables override config file"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"
            config_path.write_text('{"mode": "fast", "timeout": 30.0}')

            # Set environment variables
            os.environ["MDI_MODE"] = "render"
            os.environ["MDI_TIMEOUT"] = "120.0"
            os.environ["MDI_STRICT"] = "false"
            os.environ["MDI_MODEL"] = "claude-3"

            try:
                loader = ConfigLoader(str(config_path))
                config = loader.load()

                assert config.mode == "render"  # Overridden
                assert config.timeout == 120.0  # Overridden
                assert config.strict is False  # Overridden
                assert config.model == "claude-3"  # Overridden
            finally:
                # Clean up env vars
                for key in ["MDI_MODE", "MDI_TIMEOUT", "MDI_STRICT", "MDI_MODEL"]:
                    os.environ.pop(key, None)

    def test_env_bool_conversion(self):
        """Environment variables convert to boolean correctly"""
        os.environ["MDI_STRICT"] = "true"
        os.environ["MDI_CACHE_ENABLED"] = "1"
        os.environ["MDI_SAVE_REPORTS"] = "yes"

        try:
            loader = ConfigLoader()
            config = loader.load()

            assert config.strict is True
            assert config.cache_enabled is True
            assert config.save_reports is True
        finally:
            for key in ["MDI_STRICT", "MDI_CACHE_ENABLED", "MDI_SAVE_REPORTS"]:
                os.environ.pop(key, None)

    def test_env_report_settings_propagate_to_runtime(self):
        os.environ["MDI_SAVE_REPORTS"] = "true"
        os.environ["MDI_REPORTS_DIR"] = "saved-reports"

        try:
            config = ConfigLoader().load()
            ingest_config = config.to_ingest_config()

            assert config.save_reports is True
            assert config.reports_dir == "saved-reports"
            assert ingest_config.save_reports is True
            assert ingest_config.reports_dir == "saved-reports"
            assert "save_reports" in ingest_config.explicit_keys()
            assert "reports_dir" in ingest_config.explicit_keys()
        finally:
            for key in ["MDI_SAVE_REPORTS", "MDI_REPORTS_DIR"]:
                os.environ.pop(key, None)

    def test_env_invalid_bool_keeps_previous_value(self):
        os.environ["MDI_STRICT"] = "definitely"
        os.environ["MDI_CACHE_ENABLED"] = "garbage"

        try:
            config = ConfigLoader()._apply_env_overrides(Config(strict=True, cache_enabled=True))
            assert config.strict is True
            assert config.cache_enabled is True
        finally:
            for key in ["MDI_STRICT", "MDI_CACHE_ENABLED"]:
                os.environ.pop(key, None)

    def test_env_invalid_numeric_values_keep_previous_value(self):
        os.environ["MDI_CACHE_TTL"] = "0"
        os.environ["MDI_BATCH_TIMEOUT"] = "0"
        os.environ["MDI_BATCH_MAX_CONCURRENT"] = "0"

        try:
            config = ConfigLoader()._apply_env_overrides(
                Config(cache_ttl=120, batch_timeout=22.0, batch_max_concurrent=7)
            )
            assert config.cache_ttl == 120
            assert config.batch_timeout == 22.0
            assert config.batch_max_concurrent == 7
        finally:
            for key in ["MDI_CACHE_TTL", "MDI_BATCH_TIMEOUT", "MDI_BATCH_MAX_CONCURRENT"]:
                os.environ.pop(key, None)

    @pytest.mark.parametrize(
        ("env_var", "attr_name", "bad_value", "expected"),
        [
            ("MDI_TIMEOUT", "timeout", "nan", 44.0),
            ("MDI_BATCH_TIMEOUT", "batch_timeout", "inf", 22.0),
            ("MDI_DOMAIN_REQUEST_INTERVAL", "domain_request_interval", "nan", 0.75),
            ("MDI_CIRCUIT_BREAKER_OPEN_SECONDS", "circuit_breaker_open_seconds", "inf", 9.0),
        ],
    )
    def test_env_non_finite_float_values_keep_previous_value(
        self, env_var, attr_name, bad_value, expected
    ):
        os.environ[env_var] = bad_value

        try:
            config = ConfigLoader()._apply_env_overrides(
                Config(
                    timeout=44.0,
                    batch_timeout=22.0,
                    domain_request_interval=0.75,
                    circuit_breaker_open_seconds=9.0,
                )
            )

            assert getattr(config, attr_name) == expected
        finally:
            os.environ.pop(env_var, None)

    def test_env_invalid_override_preserves_explicit_field_precedence(self):
        os.environ["MDI_CHUNK_SIZE"] = "999999"

        try:
            config = ConfigLoader()._apply_env_overrides(
                Config(chunk_size=2000, output_profile="for_search")
            )
            resolved = config.to_ingest_config().apply_output_profile()

            assert resolved.chunk_size == 2000
            assert "chunk_size" in resolved.explicit_keys()
        finally:
            os.environ.pop("MDI_CHUNK_SIZE", None)

    def test_env_allow_local_urls_is_exposed(self):
        os.environ["MDI_ALLOW_LOCAL_URLS"] = "true"

        try:
            config = ConfigLoader().load()
            assert config.allow_local_urls is True
            ingest_config = config.to_ingest_config()
            assert ingest_config.allow_local_urls is True
        finally:
            os.environ.pop("MDI_ALLOW_LOCAL_URLS", None)

    def test_env_invalid_output_profile_keeps_previous_value(self, caplog):
        os.environ["MDI_OUTPUT_PROFILE"] = "bogus"

        try:
            with caplog.at_level("WARNING"):
                config = ConfigLoader()._apply_env_overrides(Config(output_profile="for_search"))
            assert config.output_profile == "for_search"
            assert "Invalid output_profile 'bogus' from environment" in caplog.text
        finally:
            os.environ.pop("MDI_OUTPUT_PROFILE", None)

    def test_env_custom_patterns(self):
        """Custom patterns from environment variable"""
        os.environ["MDI_CUSTOM_PATTERNS"] = "pattern1, pattern2, pattern3"

        try:
            loader = ConfigLoader()
            config = loader.load()

            assert config.custom_patterns == ["pattern1", "pattern2", "pattern3"]
        finally:
            os.environ.pop("MDI_CUSTOM_PATTERNS", None)

    def test_env_invalid_custom_patterns_keep_previous_value(self):
        os.environ["MDI_CUSTOM_PATTERNS"] = "("

        try:
            config = ConfigLoader()._apply_env_overrides(
                Config(custom_patterns=["existing-pattern"])
            )
            assert config.custom_patterns == ["existing-pattern"]
        finally:
            os.environ.pop("MDI_CUSTOM_PATTERNS", None)

    def test_env_screenshot_boolean_and_path_parsing(self):
        os.environ["MDI_SCREENSHOT"] = "true"
        try:
            config = ConfigLoader().load()
            assert config.screenshot is True
        finally:
            os.environ.pop("MDI_SCREENSHOT", None)

        os.environ["MDI_SCREENSHOT"] = "artifacts/out.png"
        try:
            config = ConfigLoader().load()
            assert config.screenshot == "artifacts/out.png"
        finally:
            os.environ.pop("MDI_SCREENSHOT", None)

    def test_env_fetcher_overrides_are_exposed(self):
        os.environ["MDI_DOMAIN_REQUEST_INTERVAL"] = "0.5"
        os.environ["MDI_CIRCUIT_BREAKER_THRESHOLD"] = "7"
        os.environ["MDI_CIRCUIT_BREAKER_OPEN_SECONDS"] = "12.5"

        try:
            config = ConfigLoader().load()
            assert config.domain_request_interval == 0.5
            assert config.circuit_breaker_threshold == 7
            assert config.circuit_breaker_open_seconds == 12.5

            ingest_config = config.to_ingest_config()
            assert ingest_config.domain_request_interval == 0.5
            assert ingest_config.circuit_breaker_threshold == 7
            assert ingest_config.circuit_breaker_open_seconds == 12.5
        finally:
            for key in (
                "MDI_DOMAIN_REQUEST_INTERVAL",
                "MDI_CIRCUIT_BREAKER_THRESHOLD",
                "MDI_CIRCUIT_BREAKER_OPEN_SECONDS",
            ):
                os.environ.pop(key, None)

    def test_env_additional_runtime_overrides_are_exposed(self):
        env_values = {
            "MDI_RENDER_COST_BUDGET": "7",
            "MDI_INCLUDE_OBSERVABILITY": "false",
            "MDI_AUTO_RENDER_THRESHOLD": "75",
            "MDI_POLICY_NAME": "strict",
            "MDI_EXTRACT_METADATA": "false",
            "MDI_EXTRACT_LINKS": "false",
            "MDI_ADVANCED_SECURITY": "true",
            "MDI_USE_LLM": "true",
            "MDI_DETECT_LANGUAGE": "false",
            "MDI_NORMALIZE_MULTILINGUAL": "false",
            "MDI_INCLUDE_SECURITY_EXPLANATION": "false",
            "MDI_FETCHER_USER_AGENT": "EnvAgent/1.0",
            "MDI_PLUGIN_DIRS": "plugins/a, plugins/b",
            "MDI_OUTPUT_FORMATS": "markdown,security",
        }
        for key, value in env_values.items():
            os.environ[key] = value

        try:
            config = ConfigLoader().load()
            ingest_config = config.to_ingest_config()

            assert config.render_cost_budget == 7
            assert config.include_observability is False
            assert config.auto_render_threshold == 75
            assert config.policy == "strict"
            assert config.extract_metadata is False
            assert config.extract_links is False
            assert config.advanced_security is True
            assert config.use_llm is True
            assert config.detect_language is False
            assert config.normalize_multilingual is False
            assert config.include_security_explanation is False
            assert config.fetcher_user_agent == "EnvAgent/1.0"
            assert config.plugin_dirs == ["plugins/a", "plugins/b"]
            assert config.output_formats == ["markdown", "security"]
            assert ingest_config.render_cost_budget == 7
            assert ingest_config.include_observability is False
            assert ingest_config.auto_render_threshold == 75
            assert ingest_config.policy_name == "strict"
            assert ingest_config.extract_metadata is False
            assert ingest_config.extract_links is False
            assert ingest_config.advanced_security is True
            assert ingest_config.use_llm is True
            assert ingest_config.detect_language is False
            assert ingest_config.normalize_multilingual is False
            assert ingest_config.include_security_explanation is False
            assert ingest_config.fetcher_user_agent == "EnvAgent/1.0"
            assert ingest_config.plugin_dirs == ["plugins/a", "plugins/b"]
            assert ingest_config.output_formats == ["markdown", "security"]
        finally:
            for key in env_values:
                os.environ.pop(key, None)

    def test_env_policy_alias_conflict_raises_value_error(self):
        os.environ["MDI_POLICY"] = "normal"
        os.environ["MDI_POLICY_NAME"] = "strict"

        try:
            with pytest.raises(
                ValueError,
                match="Environment cannot define both MDI_POLICY and MDI_POLICY_NAME with different values",
            ):
                ConfigLoader().load()
        finally:
            os.environ.pop("MDI_POLICY", None)
            os.environ.pop("MDI_POLICY_NAME", None)

    @pytest.mark.parametrize(
        ("factory", "payload", "message", "is_mapping_payload"),
        [
            (
                Config.from_yaml,
                "output_formats:\n  - bogus\n",
                "Invalid output_formats entry 'bogus'",
                False,
            ),
            (
                IngestConfig,
                {"output_formats": ["bogus"]},
                "Invalid output_formats entry 'bogus'",
                True,
            ),
        ],
    )
    def test_invalid_output_formats_are_rejected(
        self, factory, payload, message, is_mapping_payload
    ):
        with pytest.raises(ValueError, match=message):
            if is_mapping_payload:
                factory(**payload)
            else:
                factory(payload)

    def test_save_json(self):
        """Save configuration to JSON file"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "saved_config.json"

            config = Config(mode="render", timeout=90.0, strict=False)
            loader = ConfigLoader()
            loader.save(config, str(config_path))

            assert config_path.exists()

            # Load it back
            loaded_config = Config.from_json(config_path.read_text())
            assert loaded_config.mode == "render"
            assert loaded_config.timeout == 90.0
            assert loaded_config.strict is False

    def test_save_yaml(self):
        """Save configuration to YAML file"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "saved_config.yaml"

            config = Config(mode="fast", cache_enabled=True, batch_max_concurrent=10)
            loader = ConfigLoader()
            loader.save(config, str(config_path))

            assert config_path.exists()

            # Load it back
            loaded_config = Config.from_yaml(config_path.read_text())
            assert loaded_config.mode == "fast"
            assert loaded_config.cache_enabled is True
            assert loaded_config.batch_max_concurrent == 10

    def test_load_config_convenience_function(self):
        """load_config() convenience function works"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text("mode: render\ntimeout: 75.0\n")

            config = load_config(str(config_path))

            assert isinstance(config, Config)
            assert config.mode == "render"
            assert config.timeout == 75.0

    def test_to_ingest_config_maps_runtime_fields(self):
        """Legacy file config converts into the runtime ingest config."""
        config = Config(
            mode="auto",
            timeout=45.0,
            strict=False,
            allow_local_urls=True,
            model="claude",
            cache_enabled=True,
            cache_type="memory",
            cache_ttl=120,
            policy="moderate",
            custom_patterns=["secret"],
        )

        ingest_config = config.to_ingest_config()

        assert ingest_config.mode == "auto"
        assert ingest_config.timeout == 45.0
        assert ingest_config.strict is False
        assert ingest_config.allow_local_urls is True
        assert ingest_config.model == "claude"
        assert ingest_config.cache is not None
        assert ingest_config.cache_ttl == 120
        assert ingest_config.policy_name == "normal"
        assert ingest_config.custom_patterns == ["secret"]

    def test_removed_injection_threshold_is_rejected_in_strict_config_load(self):
        with pytest.raises(ValueError, match="Unknown config keys"):
            Config.from_dict({"injection_threshold": 0.95}, strict=True)

    def test_partial_config_uses_defaults(self):
        """Partial config file fills in missing values with defaults"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "partial.yaml"
            config_path.write_text("mode: render\n")  # Only one field

            loader = ConfigLoader(str(config_path))
            config = loader.load()

            assert config.mode == "render"  # From file
            assert config.timeout == 30.0  # Default
            assert config.strict is True  # Default
            assert config.model == "gpt-4"  # Default
