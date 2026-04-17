"""
Configuration file support for MarkDownIngress
"""

import copy
import json
import logging
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import MISSING, asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Literal

import yaml  # type: ignore[import-untyped]

from markdown_ingress.config_models import (
    DomainPolicy,
    IngestConfig,
    _validate_output_profile_name,
    _validate_output_representations,
)
from markdown_ingress.core.cache import Cache, MemoryCache, SQLiteCache

_logger = logging.getLogger(__name__)

# Valid values for Literal fields
VALID_MODES = ("fast", "render", "auto")
VALID_CACHE_TYPES = ("memory", "sqlite")
VALID_OUTPUT_FORMATS = ("text", "json", "markdown")
VALID_CHUNKING_STRATEGIES = ("none", "heading", "size")
VALID_POLICIES = ("permissive", "normal", "strict", "paranoid", "moderate")


def _validate_regex_patterns(patterns: list[str]) -> None:
    for pattern in patterns:
        try:
            re.compile(pattern)
        except re.error as e:
            raise ValueError(f"Invalid regex pattern '{pattern}': {e}") from e


def _validate_string_list(field_name: str, value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list of strings, got {type(value).__name__}")
    normalized: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ValueError(f"{field_name}[{index}] must be a string, got {type(item).__name__}")
        normalized.append(item)
    return normalized


def _parse_csv_string_list(field_name: str, value: str) -> list[str]:
    parsed = [item.strip() for item in value.split(",") if item.strip()]
    return _validate_string_list(field_name, parsed)


def _normalize_domain_policies(value: Any) -> list[DomainPolicy]:
    if not isinstance(value, list):
        raise ValueError(
            "domain_policies must be a list of DomainPolicy objects or mappings, "
            f"got {type(value).__name__}"
        )
    normalized: list[DomainPolicy] = []
    for index, item in enumerate(value):
        if isinstance(item, DomainPolicy):
            normalized.append(copy.deepcopy(item))
            continue
        if not isinstance(item, Mapping):
            raise ValueError(
                f"domain_policies[{index}] must be a mapping or DomainPolicy, got {type(item).__name__}"
            )
        try:
            normalized.append(DomainPolicy(**dict(item)))
        except Exception as exc:
            raise ValueError(f"domain_policies[{index}] is invalid: {exc}") from exc
    return normalized


def _collect_config_init_values(
    cls, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> tuple[dict[str, Any], frozenset[str]]:
    """Resolve dataclass-style init arguments while preserving explicit keys."""
    init_fields = [config_field for config_field in fields(cls) if config_field.init]
    if len(args) > len(init_fields):
        raise TypeError(
            f"{cls.__name__}.__init__() takes at most {len(init_fields)} positional arguments "
            f"but {len(args)} were given"
        )

    remaining_kwargs = dict(kwargs)
    values: dict[str, Any] = {}
    explicit: set[str] = set()

    for index, config_field in enumerate(init_fields):
        if index < len(args):
            if config_field.name in remaining_kwargs:
                raise TypeError(
                    f"{cls.__name__}.__init__() got multiple values for argument '{config_field.name}'"
                )
            values[config_field.name] = args[index]
            explicit.add(config_field.name)
            continue

        if config_field.name in remaining_kwargs:
            values[config_field.name] = remaining_kwargs.pop(config_field.name)
            explicit.add(config_field.name)
            continue

        if config_field.default is not MISSING:
            values[config_field.name] = copy.deepcopy(config_field.default)
            continue

        if config_field.default_factory is not MISSING:
            values[config_field.name] = config_field.default_factory()
            continue

        raise TypeError(
            f"{cls.__name__}.__init__() missing required argument: '{config_field.name}'"
        )

    if remaining_kwargs:
        unexpected = next(iter(remaining_kwargs))
        raise TypeError(
            f"{cls.__name__}.__init__() got an unexpected keyword argument '{unexpected}'"
        )

    return values, frozenset(explicit)


@dataclass(init=False)
class Config:
    """MarkDownIngress configuration"""

    # Fetching
    mode: Literal["fast", "render", "auto"] = "auto"
    timeout: float = 30.0
    auto_render_threshold: int = 50

    # Security
    strict: bool = True
    allow_local_urls: bool | None = None

    # Token estimation
    model: str = "gpt-4"

    # Caching
    cache_enabled: bool = False
    cache_type: Literal["memory", "sqlite"] = "memory"
    cache_ttl: int = 3600
    cache_path: str = ".cache/markdown_ingress.db"

    # Batch processing
    batch_max_concurrent: int = 5
    batch_timeout: float = 30.0

    # Rendering (for render mode)
    stealth: bool = False
    disable_http2: bool = False
    extreme_mode: bool = False
    screenshot: bool | str | None = None
    fetcher_user_agent: str = ""
    domain_request_interval: float = 0.25
    circuit_breaker_threshold: int = 3
    circuit_breaker_open_seconds: float = 30.0

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
    chunk_size: int = 1200
    chunk_overlap: int = 120
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
        # Validate Literal fields
        if self.mode not in VALID_MODES:
            raise ValueError(
                f"Invalid mode '{self.mode}'. Must be one of: {', '.join(VALID_MODES)}"
            )
        if self.cache_type not in VALID_CACHE_TYPES:
            raise ValueError(
                f"Invalid cache_type '{self.cache_type}'. Must be one of: {', '.join(VALID_CACHE_TYPES)}"
            )
        if self.output_format not in VALID_OUTPUT_FORMATS:
            raise ValueError(
                f"Invalid output_format '{self.output_format}'. Must be one of: {', '.join(VALID_OUTPUT_FORMATS)}"
            )
        if self.chunking_strategy not in VALID_CHUNKING_STRATEGIES:
            raise ValueError(
                f"Invalid chunking_strategy '{self.chunking_strategy}'. Must be one of: {', '.join(VALID_CHUNKING_STRATEGIES)}"
            )

        # Validate policy
        if self.policy not in VALID_POLICIES:
            raise ValueError(
                f"Invalid policy '{self.policy}'. Must be one of: {', '.join(VALID_POLICIES)}"
            )

        # Validate positive values
        if self.timeout <= 0 or self.timeout > 3600:
            raise ValueError(f"timeout must be > 0 and <= 3600, got {self.timeout}")
        if self.auto_render_threshold < 1:
            raise ValueError(
                "auto_render_threshold must be >= 1, " f"got {self.auto_render_threshold}"
            )
        if self.batch_max_concurrent < 1:
            raise ValueError(f"batch_max_concurrent must be >= 1, got {self.batch_max_concurrent}")
        if self.batch_timeout <= 0:
            raise ValueError(f"batch_timeout must be positive, got {self.batch_timeout}")
        if self.cache_ttl <= 0:
            raise ValueError(f"cache_ttl must be positive, got {self.cache_ttl}")
        if self.chunk_size < 100 or self.chunk_size > 50000:
            raise ValueError(f"chunk_size must be between 100 and 50000, got {self.chunk_size}")
        if self.chunk_overlap < 0 or self.chunk_overlap > 10000:
            raise ValueError(f"chunk_overlap must be between 0 and 10000, got {self.chunk_overlap}")
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError(
                f"chunk_overlap ({self.chunk_overlap}) must be less than chunk_size ({self.chunk_size})"
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

        if self.cache_type == "sqlite":
            if sqlite_path is None:
                raise ValueError("sqlite_path must not be None when cache_type is 'sqlite'")
            self._cache_backend = SQLiteCache(db_path=sqlite_path, default_ttl=self.cache_ttl)
            self._cache_backend_settings = cache_settings
            return self._cache_backend

        self._cache_backend = MemoryCache(default_ttl=self.cache_ttl)
        self._cache_backend_settings = cache_settings
        return self._cache_backend

    def to_ingest_config(self) -> IngestConfig:
        """Convert legacy Config into the runtime IngestConfig used by the API/CLI."""
        kwargs: dict[str, Any] = dict(
            mode=self.mode,
            strict=self.strict,
            model=self.model,
            timeout=self.timeout,
            auto_render_threshold=self.auto_render_threshold,
            cache=self.create_cache(),
            cache_ttl=self.cache_ttl if self.cache_enabled else None,
            allow_local_urls=self.allow_local_urls,
            policy_name=self.normalized_policy(),
            custom_patterns=_validate_string_list("custom_patterns", self.custom_patterns),
            plugin_dirs=_validate_string_list("plugin_dirs", self.plugin_dirs),
            domain_policies=_normalize_domain_policies(self.domain_policies),
            output_format=self.output_format,
            output_profile=self.output_profile,
            output_formats=_validate_string_list("output_formats", self.output_formats),
            extract_blocks=self.extract_blocks,
            extract_metadata=self.extract_metadata,
            extract_links=self.extract_links,
            advanced_security=self.advanced_security,
            use_llm=self.use_llm,
            detect_language=self.detect_language,
            normalize_multilingual=self.normalize_multilingual,
            include_security_explanation=self.include_security_explanation,
            chunking_strategy=self.chunking_strategy,
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            save_reports=self.save_reports,
            reports_dir=self.reports_dir,
            render_cost_budget=self.render_cost_budget,
            include_observability=self.include_observability,
            fetcher_user_agent=self.fetcher_user_agent,
            domain_request_interval=self.domain_request_interval,
            circuit_breaker_threshold=self.circuit_breaker_threshold,
            circuit_breaker_open_seconds=self.circuit_breaker_open_seconds,
            batch_timeout=self.batch_timeout,
            batch_max_concurrent=self.batch_max_concurrent,
        )
        # Forward render-related settings when present on the legacy Config
        for render_field in ("stealth", "disable_http2", "extreme_mode", "screenshot"):
            if hasattr(self, render_field):
                kwargs[render_field] = getattr(self, render_field)

        ingest_config = IngestConfig(**kwargs)
        config_to_runtime_keys: dict[str, tuple[str, ...]] = {
            "mode": ("mode",),
            "timeout": ("timeout",),
            "auto_render_threshold": ("auto_render_threshold",),
            "strict": ("strict",),
            "allow_local_urls": ("allow_local_urls",),
            "model": ("model",),
            "cache_enabled": ("cache", "cache_ttl"),
            "cache_ttl": ("cache_ttl",),
            "policy": ("policy_name",),
            "custom_patterns": ("custom_patterns",),
            "plugin_dirs": ("plugin_dirs",),
            "domain_policies": ("domain_policies",),
            "output_format": ("output_format",),
            "output_profile": ("output_profile",),
            "output_formats": ("output_formats",),
            "extract_blocks": ("extract_blocks",),
            "extract_metadata": ("extract_metadata",),
            "extract_links": ("extract_links",),
            "advanced_security": ("advanced_security",),
            "use_llm": ("use_llm",),
            "detect_language": ("detect_language",),
            "normalize_multilingual": ("normalize_multilingual",),
            "include_security_explanation": ("include_security_explanation",),
            "chunking_strategy": ("chunking_strategy",),
            "chunk_size": ("chunk_size",),
            "chunk_overlap": ("chunk_overlap",),
            "save_reports": ("save_reports",),
            "reports_dir": ("reports_dir",),
            "stealth": ("stealth",),
            "disable_http2": ("disable_http2",),
            "extreme_mode": ("extreme_mode",),
            "screenshot": ("screenshot",),
            "batch_timeout": ("batch_timeout",),
            "batch_max_concurrent": ("batch_max_concurrent",),
            "render_cost_budget": ("render_cost_budget",),
            "include_observability": ("include_observability",),
            "fetcher_user_agent": ("fetcher_user_agent",),
            "domain_request_interval": ("domain_request_interval",),
            "circuit_breaker_threshold": ("circuit_breaker_threshold",),
            "circuit_breaker_open_seconds": ("circuit_breaker_open_seconds",),
        }
        explicit_runtime_keys: set[str] = set()
        for config_key in self.explicit_keys():
            explicit_runtime_keys.update(config_to_runtime_keys.get(config_key, ()))
        object.__setattr__(ingest_config, "_explicit_keys", frozenset(explicit_runtime_keys))
        return ingest_config

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


class ConfigLoader:
    """Load configuration from files and environment variables"""

    DEFAULT_LOCATIONS = [
        ".markdowningress.yaml",
        ".markdowningress.yml",
        ".markdowningress.json",
        "~/.config/markdowningress/config.yaml",
        "~/.config/markdowningress/config.yml",
        "~/.config/markdowningress/config.json",
    ]

    def __init__(self, config_path: str | None = None):
        """
        Initialize config loader.

        Args:
            config_path: Explicit config file path (optional)
        """
        self.config_path = config_path

    def load(self) -> Config:
        """
        Load configuration with priority:
        1. Explicit config file path (if provided)
        2. Default locations (in order)
        3. Environment variables
        4. Defaults

        Returns:
            Loaded Config object
        """
        config = Config()  # Start with defaults

        # Try to load from file
        if self.config_path:
            # Explicit path provided
            config = self._load_from_file(self.config_path)
        else:
            # Try default locations
            for location in self.DEFAULT_LOCATIONS:
                expanded_path = Path(location).expanduser()
                if expanded_path.exists():
                    config = self._load_from_file(str(expanded_path))
                    break

        # Override with environment variables
        config = self._apply_env_overrides(config)

        return config

    def _load_from_file(self, filepath: str) -> Config:
        """Load config from file (JSON or YAML)"""
        path = Path(filepath).expanduser()

        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {filepath}")

        content = path.read_text()

        # Determine format from extension
        if filepath.endswith(".json"):
            return Config.from_json(content)
        elif filepath.endswith((".yaml", ".yml")):
            return Config.from_yaml(content)
        else:
            # Try to detect format
            try:
                return Config.from_json(content)
            except json.JSONDecodeError:
                try:
                    return Config.from_yaml(content)
                except yaml.YAMLError:
                    raise ValueError(f"Unable to parse config file: {filepath}")

    def _apply_env_overrides(self, config: Config) -> Config:
        """Apply environment variable overrides"""
        explicit = set(config.explicit_keys())
        previous_explicit = {
            config_field.name: config_field.name in explicit
            for config_field in fields(Config)
            if not config_field.name.startswith("_")
        }
        env_policy = os.getenv("MDI_POLICY")
        env_policy_name = os.getenv("MDI_POLICY_NAME")
        if env_policy is not None and env_policy_name is not None and env_policy != env_policy_name:
            raise ValueError(
                "Environment cannot define both MDI_POLICY and MDI_POLICY_NAME "
                "with different values"
            )
        env_mapping: dict[str, tuple[str, Callable[[str], object]]] = {
            "MDI_MODE": ("mode", str),
            "MDI_TIMEOUT": ("timeout", float),
            "MDI_AUTO_RENDER_THRESHOLD": ("auto_render_threshold", int),
            "MDI_STRICT": ("strict", self._str_to_bool),
            "MDI_ALLOW_LOCAL_URLS": ("allow_local_urls", self._str_to_bool),
            "MDI_MODEL": ("model", str),
            "MDI_CACHE_ENABLED": ("cache_enabled", self._str_to_bool),
            "MDI_CACHE_TYPE": ("cache_type", str),
            "MDI_CACHE_TTL": ("cache_ttl", int),
            "MDI_CACHE_PATH": ("cache_path", str),
            "MDI_BATCH_MAX_CONCURRENT": ("batch_max_concurrent", int),
            "MDI_BATCH_TIMEOUT": ("batch_timeout", float),
            "MDI_POLICY": ("policy", str),
            "MDI_POLICY_NAME": ("policy", str),
            "MDI_OUTPUT_FORMAT": ("output_format", str),
            "MDI_OUTPUT_PROFILE": ("output_profile", str),
            "MDI_EXTRACT_BLOCKS": ("extract_blocks", self._str_to_bool),
            "MDI_EXTRACT_METADATA": ("extract_metadata", self._str_to_bool),
            "MDI_EXTRACT_LINKS": ("extract_links", self._str_to_bool),
            "MDI_ADVANCED_SECURITY": ("advanced_security", self._str_to_bool),
            "MDI_USE_LLM": ("use_llm", self._str_to_bool),
            "MDI_DETECT_LANGUAGE": ("detect_language", self._str_to_bool),
            "MDI_NORMALIZE_MULTILINGUAL": ("normalize_multilingual", self._str_to_bool),
            "MDI_INCLUDE_SECURITY_EXPLANATION": (
                "include_security_explanation",
                self._str_to_bool,
            ),
            "MDI_CHUNKING_STRATEGY": ("chunking_strategy", str),
            "MDI_CHUNK_SIZE": ("chunk_size", int),
            "MDI_CHUNK_OVERLAP": ("chunk_overlap", int),
            "MDI_SAVE_REPORTS": ("save_reports", self._str_to_bool),
            "MDI_REPORTS_DIR": ("reports_dir", str),
            "MDI_RENDER_COST_BUDGET": ("render_cost_budget", int),
            "MDI_INCLUDE_OBSERVABILITY": ("include_observability", self._str_to_bool),
            "MDI_STEALTH": ("stealth", self._str_to_bool),
            "MDI_DISABLE_HTTP2": ("disable_http2", self._str_to_bool),
            "MDI_EXTREME_MODE": ("extreme_mode", self._str_to_bool),
            "MDI_SCREENSHOT": ("screenshot", self._str_to_bool_or_string),
            "MDI_FETCHER_USER_AGENT": ("fetcher_user_agent", str),
            "MDI_DOMAIN_REQUEST_INTERVAL": ("domain_request_interval", float),
            "MDI_CIRCUIT_BREAKER_THRESHOLD": ("circuit_breaker_threshold", int),
            "MDI_CIRCUIT_BREAKER_OPEN_SECONDS": ("circuit_breaker_open_seconds", float),
        }
        previous_values = {
            attr_name: getattr(config, attr_name) for _, (attr_name, _) in env_mapping.items()
        }

        for env_var, (attr_name, converter) in env_mapping.items():
            value = os.getenv(env_var)
            if value is not None:
                try:
                    setattr(config, attr_name, converter(value))
                    explicit.add(attr_name)
                except (ValueError, TypeError) as e:
                    _logger.warning(
                        "Invalid value for %s (%s=%s): %s. Keeping previous value.",
                        attr_name,
                        env_var,
                        value,
                        e,
                    )

        # Handle custom patterns (comma-separated)
        custom_patterns_env = os.getenv("MDI_CUSTOM_PATTERNS")
        if custom_patterns_env:
            patterns = _parse_csv_string_list("custom_patterns", custom_patterns_env)
            if patterns:
                try:
                    _validate_regex_patterns(patterns)
                except ValueError as e:
                    _logger.warning(
                        "Invalid value for custom_patterns (%s=%s): %s. Keeping previous value.",
                        "MDI_CUSTOM_PATTERNS",
                        custom_patterns_env,
                        e,
                    )
                else:
                    config.custom_patterns = patterns
                    explicit.add("custom_patterns")

        list_env_mapping: dict[str, str] = {
            "MDI_PLUGIN_DIRS": "plugin_dirs",
            "MDI_OUTPUT_FORMATS": "output_formats",
        }
        for env_var, attr_name in list_env_mapping.items():
            value = os.getenv(env_var)
            if value is None:
                continue
            try:
                parsed = _parse_csv_string_list(attr_name, value)
                if attr_name == "output_formats":
                    parsed = _validate_output_representations(parsed)
                setattr(config, attr_name, parsed)
                explicit.add(attr_name)
            except (ValueError, TypeError) as e:
                _logger.warning(
                    "Invalid value for %s (%s=%s): %s. Keeping previous value.",
                    attr_name,
                    env_var,
                    value,
                    e,
                )

        def _restore(attr_name: str, message: str, *args: object) -> None:
            _logger.warning(message, *args)
            setattr(config, attr_name, previous_values[attr_name])
            if previous_explicit.get(attr_name, False):
                explicit.add(attr_name)
            else:
                explicit.discard(attr_name)

        # Validate Literal fields after env override (in case invalid values were set)
        # Mode validation
        if config.mode not in VALID_MODES:
            _restore(
                "mode",
                "Invalid mode '%s' from environment, valid values: %s. Keeping previous value %r.",
                config.mode,
                VALID_MODES,
                previous_values["mode"],
            )

        # Cache type validation
        if config.cache_type not in VALID_CACHE_TYPES:
            _restore(
                "cache_type",
                "Invalid cache_type '%s' from environment, valid values: %s. Keeping previous value %r.",
                config.cache_type,
                VALID_CACHE_TYPES,
                previous_values["cache_type"],
            )

        # Output format validation
        if config.output_format not in VALID_OUTPUT_FORMATS:
            _restore(
                "output_format",
                "Invalid output_format '%s' from environment, valid values: %s. Keeping previous value %r.",
                config.output_format,
                VALID_OUTPUT_FORMATS,
                previous_values["output_format"],
            )
        try:
            config.output_profile = (
                _validate_output_profile_name(config.output_profile) or "default"
            )
        except ValueError as exc:
            _restore(
                "output_profile",
                "Invalid output_profile '%s' from environment: %s. Keeping previous value %r.",
                config.output_profile,
                exc,
                previous_values["output_profile"],
            )
        if config.auto_render_threshold < 1:
            _restore(
                "auto_render_threshold",
                "Invalid auto_render_threshold %r from environment, must be >= 1. Keeping previous value %r.",
                config.auto_render_threshold,
                previous_values["auto_render_threshold"],
            )
        if config.render_cost_budget is not None and config.render_cost_budget < 1:
            _restore(
                "render_cost_budget",
                "Invalid render_cost_budget %r from environment, must be >= 1. Keeping previous value %r.",
                config.render_cost_budget,
                previous_values["render_cost_budget"],
            )
        if not config.output_formats:
            _restore(
                "output_formats",
                "Invalid output_formats from environment, list cannot be empty. Keeping previous value %r.",
                previous_values["output_formats"],
            )
        else:
            try:
                config.output_formats = _validate_output_representations(config.output_formats)
            except (ValueError, TypeError) as exc:
                _restore(
                    "output_formats",
                    "Invalid output_formats from environment: %s. Keeping previous value %r.",
                    exc,
                    previous_values["output_formats"],
                )

        # Chunking strategy validation
        if config.chunking_strategy not in VALID_CHUNKING_STRATEGIES:
            _restore(
                "chunking_strategy",
                "Invalid chunking_strategy '%s' from environment, valid values: %s. Keeping previous value %r.",
                config.chunking_strategy,
                VALID_CHUNKING_STRATEGIES,
                previous_values["chunking_strategy"],
            )

        # Policy validation
        if config.policy not in VALID_POLICIES:
            _restore(
                "policy",
                "Invalid policy '%s' from environment, valid values: %s. Keeping previous value %r.",
                config.policy,
                VALID_POLICIES,
                previous_values["policy"],
            )

        # Numeric bounds validation after env overrides
        # These fields always exist due to dataclass defaults, direct access is safe
        if config.timeout <= 0 or config.timeout > 3600:
            _restore(
                "timeout",
                "Invalid timeout '%s' from environment, must be > 0 and <= 3600. Keeping previous value %r.",
                config.timeout,
                previous_values["timeout"],
            )

        if config.chunk_size < 100 or config.chunk_size > 50000:
            _restore(
                "chunk_size",
                "Invalid chunk_size '%s' from environment, must be 100-50000. Keeping previous value %r.",
                config.chunk_size,
                previous_values["chunk_size"],
            )

        if config.chunk_overlap < 0 or config.chunk_overlap > 10000:
            _restore(
                "chunk_overlap",
                "Invalid chunk_overlap '%s' from environment, must be 0-10000. Keeping previous value %r.",
                config.chunk_overlap,
                previous_values["chunk_overlap"],
            )

        if config.chunk_overlap >= config.chunk_size:
            _restore(
                "chunk_overlap",
                "chunk_overlap (%s) must be less than chunk_size (%s) after env overrides. Keeping previous value %r.",
                config.chunk_overlap,
                config.chunk_size,
                previous_values["chunk_overlap"],
            )

        if config.domain_request_interval < 0.0:
            _restore(
                "domain_request_interval",
                "Invalid domain_request_interval '%s' from environment, must be >= 0.0. Keeping previous value %r.",
                config.domain_request_interval,
                previous_values["domain_request_interval"],
            )

        if config.circuit_breaker_threshold < 1:
            _restore(
                "circuit_breaker_threshold",
                "Invalid circuit_breaker_threshold '%s' from environment, must be >= 1. Keeping previous value %r.",
                config.circuit_breaker_threshold,
                previous_values["circuit_breaker_threshold"],
            )

        if config.circuit_breaker_open_seconds <= 0.0:
            _restore(
                "circuit_breaker_open_seconds",
                "Invalid circuit_breaker_open_seconds '%s' from environment, must be > 0.0. Keeping previous value %r.",
                config.circuit_breaker_open_seconds,
                previous_values["circuit_breaker_open_seconds"],
            )

        if config.cache_ttl <= 0:
            _restore(
                "cache_ttl",
                "Invalid cache_ttl '%s' from environment, must be > 0. Keeping previous value %r.",
                config.cache_ttl,
                previous_values["cache_ttl"],
            )

        if config.batch_max_concurrent < 1:
            _restore(
                "batch_max_concurrent",
                "Invalid batch_max_concurrent '%s' from environment, must be >= 1. Keeping previous value %r.",
                config.batch_max_concurrent,
                previous_values["batch_max_concurrent"],
            )

        if config.batch_timeout <= 0.0:
            _restore(
                "batch_timeout",
                "Invalid batch_timeout '%s' from environment, must be > 0.0. Keeping previous value %r.",
                config.batch_timeout,
                previous_values["batch_timeout"],
            )

        config._explicit_keys = frozenset(explicit)

        return config

    @staticmethod
    def _str_to_bool(value: str) -> bool:
        """Convert string to boolean.

        Handles common truthy and falsy string representations.
        Raises ValueError for unrecognized values so callers can preserve the
        previous config value instead of silently degrading behavior.
        """
        lower = value.strip().lower()
        if lower in ("true", "1", "yes", "on", "enabled"):
            return True
        if lower in ("false", "0", "no", "off", "disabled"):
            return False
        raise ValueError(
            f"invalid boolean {value!r}; expected one of true/false/1/0/yes/no/on/off/enabled/disabled"
        )

    @classmethod
    def _str_to_bool_or_string(cls, value: str) -> bool | str:
        """Convert env values like true/false into bool, otherwise keep explicit paths."""
        normalized = value.strip()
        lowered = normalized.lower()
        if lowered in ("true", "1", "yes", "on", "enabled"):
            return True
        if lowered in ("false", "0", "no", "off", "disabled"):
            return False
        return normalized

    def save(self, config: Config, filepath: str):
        """
        Save configuration to file.

        Args:
            config: Config object to save
            filepath: Output file path (.json or .yaml)
        """
        path = Path(filepath).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)

        if filepath.endswith(".json"):
            content = config.to_json()
        elif filepath.endswith((".yaml", ".yml")):
            content = config.to_yaml()
        else:
            raise ValueError("Config file must be .json, .yaml, or .yml")

        path.write_text(content)


def load_config(config_path: str | None = None) -> Config:
    """
    Convenience function to load configuration.

    Args:
        config_path: Optional explicit config file path

    Returns:
        Loaded Config object
    """
    loader = ConfigLoader(config_path)
    return loader.load()
