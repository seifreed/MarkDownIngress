"""
Tests for configuration file support
"""

import os
import tempfile
from pathlib import Path

import pytest

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
            config = ConfigLoader()._apply_env_overrides(
                Config(strict=True, cache_enabled=True)
            )
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

    def test_env_allow_local_urls_is_exposed(self):
        os.environ["MDI_ALLOW_LOCAL_URLS"] = "true"

        try:
            config = ConfigLoader().load()
            assert config.allow_local_urls is True
            ingest_config = config.to_ingest_config()
            assert ingest_config.allow_local_urls is True
        finally:
            os.environ.pop("MDI_ALLOW_LOCAL_URLS", None)

    def test_env_custom_patterns(self):
        """Custom patterns from environment variable"""
        os.environ["MDI_CUSTOM_PATTERNS"] = "pattern1, pattern2, pattern3"

        try:
            loader = ConfigLoader()
            config = loader.load()

            assert config.custom_patterns == ["pattern1", "pattern2", "pattern3"]
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
