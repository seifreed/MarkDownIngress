"""
Configuration file support for MarkDownIngress
"""

import copy
import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Literal

import yaml  # type: ignore[import-untyped]

import markdown_ingress.config_validation as config_validation
from markdown_ingress.config_models import (
    DomainPolicy,
    IngestConfig,
    _normalize_domain_policies,
)
from markdown_ingress.core.cache import Cache
from markdown_ingress.core.config_runtime import build_ingest_config

_logger = logging.getLogger(__name__)

_cache_backend_factory: Callable[[str, str | None, int], Cache] | None = None


def register_cache_factory(fn: Callable[[str, str | None, int], Cache]) -> None:
    global _cache_backend_factory
    _cache_backend_factory = fn


# Valid values for Literal fields
VALID_MODES = ("fast", "render", "auto")
VALID_CACHE_TYPES = ("memory", "sqlite")
VALID_OUTPUT_FORMATS = config_validation.VALID_OUTPUT_FORMATS
VALID_CHUNKING_STRATEGIES = ("none", "heading", "size")
VALID_POLICIES = ("permissive", "normal", "strict", "paranoid", "moderate")

_collect_config_init_values = config_validation.collect_init_values
_ensure_bool = config_validation.ensure_bool
_ensure_finite_float = config_validation.ensure_finite_float
_ensure_int = config_validation.ensure_int
_ensure_optional_bool = config_validation.ensure_optional_bool
_ensure_optional_int = config_validation.ensure_optional_int
_ensure_screenshot_value = config_validation.ensure_screenshot_value
_ensure_str = config_validation.ensure_str
_validate_output_profile_name = config_validation.validate_output_profile_name
_validate_output_representations = config_validation.validate_output_representations
_validate_regex_patterns = config_validation.validate_regex_patterns
_validate_string_list = config_validation.validate_string_list

_MAX_TIMEOUT_SECONDS = 3_600
_MIN_CHUNK_SIZE = 100
_MAX_CHUNK_SIZE = 50_000
_MIN_CHUNK_OVERLAP = 0
_MAX_CHUNK_OVERLAP = 10_000
_MIN_BATCH_CONCURRENCY = 1
_MIN_CACHE_TTL = 1

_DEFAULT_TIMEOUT_SECONDS = 30.0
_DEFAULT_AUTO_RENDER_THRESHOLD = 50
_DEFAULT_CACHE_TTL_SECONDS = 3600
_DEFAULT_BATCH_MAX_CONCURRENT = 5
_DEFAULT_BATCH_TIMEOUT_SECONDS = 30.0
_DEFAULT_DOMAIN_REQUEST_INTERVAL = 0.25
_DEFAULT_CIRCUIT_BREAKER_THRESHOLD = 3
_DEFAULT_CIRCUIT_BREAKER_OPEN_SECONDS = 30.0
_DEFAULT_CHUNK_SIZE = 1200
_DEFAULT_CHUNK_OVERLAP = 120


@dataclass(init=False)
class Config:
    """MarkDownIngress configuration"""

    # Fetching
    mode: Literal["fast", "render", "auto"] = "auto"
    timeout: float = _DEFAULT_TIMEOUT_SECONDS
    auto_render_threshold: int = _DEFAULT_AUTO_RENDER_THRESHOLD

    # Security
    strict: bool = True
    allow_local_urls: bool | None = None

    # Token estimation
    model: str = "gpt-4"

    # Caching
    cache_enabled: bool = False
    cache_type: Literal["memory", "sqlite"] = "memory"
    cache_ttl: int = _DEFAULT_CACHE_TTL_SECONDS
    cache_path: str = ".cache/markdown_ingress.db"

    # Batch processing
    batch_max_concurrent: int = _DEFAULT_BATCH_MAX_CONCURRENT
    batch_timeout: float = _DEFAULT_BATCH_TIMEOUT_SECONDS

    # Rendering (for render mode)
    stealth: bool = False
    disable_http2: bool = False
    extreme_mode: bool = False
    screenshot: bool | str | None = None
    fetcher_user_agent: str = ""
    domain_request_interval: float = _DEFAULT_DOMAIN_REQUEST_INTERVAL
    circuit_breaker_threshold: int = _DEFAULT_CIRCUIT_BREAKER_THRESHOLD
    circuit_breaker_open_seconds: float = _DEFAULT_CIRCUIT_BREAKER_OPEN_SECONDS

    # Policy
    policy: str = "normal"
    custom_patterns: list[str] = field(default_factory=list)
    plugin_dirs: list[str] = field(default_factory=list)
    domain_policies: list[DomainPolicy] = field(default_factory=list)

    # Output
    output_format: Literal["text", "json", "markdown"] = "text"
    output_profile: str = "default"
    output_formats: list[str] = field(default_factory=lambda: ["markdown"])
    extract_blocks: bool = False
    extract_metadata: bool = True
    extract_links: bool = True
    advanced_security: bool = False
    use_llm: bool = False
    detect_language: bool = True
    normalize_multilingual: bool = True
    include_security_explanation: bool = True
    chunking_strategy: Literal["none", "heading", "size"] = "none"
    chunk_size: int = _DEFAULT_CHUNK_SIZE
    chunk_overlap: int = _DEFAULT_CHUNK_OVERLAP
    save_reports: bool = False
    reports_dir: str = "reports"
    render_cost_budget: int | None = None
    include_observability: bool = True
    _cache_backend: Cache | None = field(default=None, init=False, repr=False)
    _cache_backend_settings: tuple[str, str | None, int] | None = field(
        default=None, init=False, repr=False
    )
    _explicit_keys: frozenset[str] = field(default_factory=frozenset, init=False, repr=False)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        values, explicit = _collect_config_init_values(type(self), args, kwargs)
        for key, value in values.items():
            setattr(self, key, value)
        self._cache_backend = None
        self._cache_backend_settings = None
        self.__post_init__()
        self._explicit_keys = explicit

    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        if not isinstance(self.mode, str):
            raise ValueError(f"mode must be a string, got {type(self.mode).__name__}")
        self.timeout = _ensure_finite_float("timeout", self.timeout)
        self.auto_render_threshold = _ensure_int(
            "auto_render_threshold", self.auto_render_threshold
        )
        self.strict = _ensure_bool("strict", self.strict)
        self.allow_local_urls = _ensure_optional_bool("allow_local_urls", self.allow_local_urls)
        self.model = _ensure_str("model", self.model)
        self.cache_enabled = _ensure_bool("cache_enabled", self.cache_enabled)
        if not isinstance(self.cache_type, str):
            raise ValueError(f"cache_type must be a string, got {type(self.cache_type).__name__}")
        self.cache_ttl = _ensure_int("cache_ttl", self.cache_ttl)
        self.cache_path = _ensure_str("cache_path", self.cache_path)
        self.batch_max_concurrent = _ensure_int("batch_max_concurrent", self.batch_max_concurrent)
        self.batch_timeout = _ensure_finite_float("batch_timeout", self.batch_timeout)
        self.stealth = _ensure_bool("stealth", self.stealth)
        self.disable_http2 = _ensure_bool("disable_http2", self.disable_http2)
        self.extreme_mode = _ensure_bool("extreme_mode", self.extreme_mode)
        self.screenshot = _ensure_screenshot_value("screenshot", self.screenshot)
        self.fetcher_user_agent = _ensure_str("fetcher_user_agent", self.fetcher_user_agent)
        self.domain_request_interval = _ensure_finite_float(
            "domain_request_interval", self.domain_request_interval
        )
        self.circuit_breaker_threshold = _ensure_int(
            "circuit_breaker_threshold", self.circuit_breaker_threshold
        )
        self.circuit_breaker_open_seconds = _ensure_finite_float(
            "circuit_breaker_open_seconds", self.circuit_breaker_open_seconds
        )
        self.policy = _ensure_str("policy", self.policy)
        if not isinstance(self.output_format, str):
            raise ValueError(
                f"output_format must be a string, got {type(self.output_format).__name__}"
            )
        self.output_profile = _ensure_str("output_profile", self.output_profile)
        self.extract_blocks = _ensure_bool("extract_blocks", self.extract_blocks)
        self.extract_metadata = _ensure_bool("extract_metadata", self.extract_metadata)
        self.extract_links = _ensure_bool("extract_links", self.extract_links)
        self.advanced_security = _ensure_bool("advanced_security", self.advanced_security)
        self.use_llm = _ensure_bool("use_llm", self.use_llm)
        self.detect_language = _ensure_bool("detect_language", self.detect_language)
        self.normalize_multilingual = _ensure_bool(
            "normalize_multilingual", self.normalize_multilingual
        )
        self.include_security_explanation = _ensure_bool(
            "include_security_explanation", self.include_security_explanation
        )
        if not isinstance(self.chunking_strategy, str):
            raise ValueError(
                f"chunking_strategy must be a string, got {type(self.chunking_strategy).__name__}"
            )
        self.chunk_size = _ensure_int("chunk_size", self.chunk_size)
        self.chunk_overlap = _ensure_int("chunk_overlap", self.chunk_overlap)
        self.save_reports = _ensure_bool("save_reports", self.save_reports)
        self.reports_dir = _ensure_str("reports_dir", self.reports_dir)
        self.render_cost_budget = _ensure_optional_int(
            "render_cost_budget", self.render_cost_budget
        )
        self.include_observability = _ensure_bool(
            "include_observability", self.include_observability
        )

        # Validate Literal fields
        if self.mode not in VALID_MODES:
            raise ValueError(
                f"Invalid mode '{self.mode}'. Must be one of: {', '.join(VALID_MODES)}"
            )
        if self.cache_type not in VALID_CACHE_TYPES:
            raise ValueError(
                f"Invalid cache_type '{self.cache_type}'. Must be one of: "
                f"{', '.join(VALID_CACHE_TYPES)}"
            )
        if self.output_format not in VALID_OUTPUT_FORMATS:
            raise ValueError(
                f"Invalid output_format '{self.output_format}'. Must be one of: "
                f"{', '.join(VALID_OUTPUT_FORMATS)}"
            )
        if self.chunking_strategy not in VALID_CHUNKING_STRATEGIES:
            raise ValueError(
                f"Invalid chunking_strategy '{self.chunking_strategy}'. Must be one of: "
                f"{', '.join(VALID_CHUNKING_STRATEGIES)}"
            )

        # Validate policy
        if self.policy not in VALID_POLICIES:
            raise ValueError(
                f"Invalid policy '{self.policy}'. Must be one of: {', '.join(VALID_POLICIES)}"
            )

        # Validate positive values
        if self.timeout <= 0 or self.timeout > _MAX_TIMEOUT_SECONDS:
            raise ValueError(
                f"timeout must be > 0 and <= {_MAX_TIMEOUT_SECONDS}, got {self.timeout}"
            )
        if self.auto_render_threshold < 1:
            raise ValueError(
                "auto_render_threshold must be >= 1, " f"got {self.auto_render_threshold}"
            )
        if self.batch_max_concurrent < _MIN_BATCH_CONCURRENCY:
            raise ValueError(
                f"batch_max_concurrent must be >= {_MIN_BATCH_CONCURRENCY}, "
                f"got {self.batch_max_concurrent}"
            )
        if self.batch_timeout <= 0:
            raise ValueError(f"batch_timeout must be positive, got {self.batch_timeout}")
        if self.cache_ttl < _MIN_CACHE_TTL:
            raise ValueError(f"cache_ttl must be positive, got {self.cache_ttl}")
        if self.chunk_size < _MIN_CHUNK_SIZE or self.chunk_size > _MAX_CHUNK_SIZE:
            raise ValueError(
                f"chunk_size must be between {_MIN_CHUNK_SIZE} and {_MAX_CHUNK_SIZE}, "
                f"got {self.chunk_size}"
            )
        if self.chunk_overlap < _MIN_CHUNK_OVERLAP or self.chunk_overlap > _MAX_CHUNK_OVERLAP:
            raise ValueError(
                f"chunk_overlap must be between {_MIN_CHUNK_OVERLAP} and "
                f"{_MAX_CHUNK_OVERLAP}, got {self.chunk_overlap}"
            )
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError(
                f"chunk_overlap ({self.chunk_overlap}) must be less than "
                f"chunk_size ({self.chunk_size})"
            )

        self.custom_patterns = _validate_string_list("custom_patterns", self.custom_patterns)
        self.plugin_dirs = _validate_string_list("plugin_dirs", self.plugin_dirs)
        self.output_formats = _validate_output_representations(self.output_formats)
        self.domain_policies = _normalize_domain_policies(self.domain_policies)
        _validate_output_profile_name(self.output_profile)

        if self.render_cost_budget is not None and self.render_cost_budget < 1:
            raise ValueError(
                "render_cost_budget must be >= 1 when provided, " f"got {self.render_cost_budget}"
            )

        # Validate custom_patterns are valid regex
        _validate_regex_patterns(self.custom_patterns)

    def to_dict(self) -> dict[str, Any]:
        """Convert config to dictionary"""
        data: dict[str, Any] = {}
        for config_field in fields(self):
            if config_field.name.startswith("_"):
                continue
            value = getattr(self, config_field.name)
            if config_field.name == "domain_policies":
                data[config_field.name] = [
                    asdict(item) if isinstance(item, DomainPolicy) else copy.deepcopy(item)
                    for item in value
                ]
                continue
            data[config_field.name] = copy.deepcopy(value)
        return data

    def explicit_keys(self) -> frozenset[str]:
        """Return config fields explicitly set by the caller, file, or environment."""
        return frozenset(getattr(self, "_explicit_keys", frozenset()))

    def to_json(self, indent: int = 2) -> str:
        """Export as JSON"""
        return json.dumps(self.to_dict(), indent=indent)

    def to_yaml(self) -> str:
        """Export as YAML"""
        return str(yaml.dump(self.to_dict(), default_flow_style=False))

    def normalized_policy(self) -> str:
        """Return a policy name accepted by the policy engine."""
        if self.policy == "moderate":
            return "normal"
        return self.policy

    def create_cache(self) -> Cache | None:
        """Instantiate a cache backend from config settings."""
        if not self.cache_enabled:
            self._cache_backend = None
            self._cache_backend_settings = None
            return None

        sqlite_path = (
            str(Path(self.cache_path).expanduser()) if self.cache_type == "sqlite" else None
        )
        cache_settings = (
            self.cache_type,
            sqlite_path,
            self.cache_ttl,
        )
        cached_backend_closed = bool(getattr(self._cache_backend, "_closed", False))
        if (
            self._cache_backend is not None
            and self._cache_backend_settings == cache_settings
            and not cached_backend_closed
        ):
            return self._cache_backend

        if _cache_backend_factory is None:
            raise RuntimeError(
                "No cache factory registered; call register_cache_factory() before "
                "using Config.create_cache()."
            )
        self._cache_backend = _cache_backend_factory(self.cache_type, sqlite_path, self.cache_ttl)
        self._cache_backend_settings = cache_settings
        return self._cache_backend

    def to_ingest_config(self) -> IngestConfig:
        """Convert legacy Config into the runtime IngestConfig used by the API/CLI."""
        return build_ingest_config(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any], strict: bool = False) -> "Config":
        """Create config from dictionary.

        Args:
            data: Dictionary with config values
            strict: If True, raise ValueError for unrecognized keys instead of warning

        Returns:
            Config instance
        """
        import logging

        _logger = logging.getLogger(__name__)
        if not isinstance(data, Mapping):
            raise ValueError(
                "Config data must be a JSON/YAML object (mapping), " f"got {type(data).__name__}"
            )
        data = dict(data)
        if "policy_name" in data:
            if "policy" in data and data["policy"] != data["policy_name"]:
                raise ValueError(
                    "Config cannot define both policy and policy_name with different values"
                )
            data["policy"] = data.pop("policy_name")
        valid_keys = {k for k in cls.__dataclass_fields__ if not k.startswith("_")}
        unknown_keys = set(data.keys()) - valid_keys
        if unknown_keys:
            if strict:
                raise ValueError(
                    f"Unknown config keys: {', '.join(sorted(unknown_keys))}. "
                    f"Valid keys: {', '.join(sorted(valid_keys))}"
                )
            _logger.warning(
                "Unrecognized config keys (ignored): %s. Valid keys: %s",
                ", ".join(sorted(unknown_keys)),
                ", ".join(sorted(valid_keys)),
            )
        return cls(**{k: v for k, v in data.items() if k in valid_keys})

    @classmethod
    def from_json(cls, json_str: str) -> "Config":
        """Load config from JSON string"""
        data = json.loads(json_str)
        return cls.from_dict(data)

    @classmethod
    def from_yaml(cls, yaml_str: str) -> "Config":
        """Load config from YAML string"""
        data = yaml.safe_load(yaml_str)
        return cls.from_dict(data)


def load_config(config_path: str | None = None) -> "Config":
    """
    Convenience function to load configuration.

    Args:
        config_path: Optional explicit config file path

    Returns:
        Loaded Config object
    """
    from markdown_ingress.core.config_loader import ConfigLoader

    return ConfigLoader(config_path).load()


def __getattr__(name: str):
    if name == "ConfigLoader":
        from markdown_ingress.core.config_loader import ConfigLoader

        return ConfigLoader
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
