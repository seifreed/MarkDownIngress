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
import markdown_ingress.core.config_rules as config_rules
from markdown_ingress.config_models import (
    DomainPolicy,
    IngestConfig,
)
from markdown_ingress.config_models import (
    _normalize_domain_policies as _normalize_domain_policies,
)
from markdown_ingress.core.cache import Cache
from markdown_ingress.core.config_rules import validate_config
from markdown_ingress.core.config_runtime import build_ingest_config

_logger = logging.getLogger(__name__)

_cache_backend_factory: Callable[[str, str | None, int], Cache] | None = None


def register_cache_factory(fn: Callable[[str, str | None, int], Cache]) -> None:
    global _cache_backend_factory
    _cache_backend_factory = fn


_collect_config_init_values = config_validation.collect_init_values
_validate_regex_patterns = config_validation.validate_regex_patterns
_validate_string_list = config_validation.validate_string_list

VALID_MODES = config_rules.VALID_MODES
VALID_CACHE_TYPES = config_rules.VALID_CACHE_TYPES
VALID_OUTPUT_FORMATS = config_rules.VALID_OUTPUT_FORMATS
VALID_CHUNKING_STRATEGIES = config_rules.VALID_CHUNKING_STRATEGIES
VALID_POLICIES = config_rules.VALID_POLICIES

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
    mode: config_validation.Mode = "auto"
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
    output_format: config_validation.OutputFormat = "text"
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
    chunking_strategy: config_validation.ChunkingStrategy = "none"
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
        validate_config(self)

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
