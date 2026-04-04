"""
Configuration file support for MarkDownIngress
"""

import copy
import json
import logging
import os
from collections.abc import Callable
from dataclasses import MISSING, dataclass, field, fields
from pathlib import Path
from typing import Any, Literal

import yaml  # type: ignore[import-untyped]

from markdown_ingress.config_models import DomainPolicy, IngestConfig
from markdown_ingress.core.cache import Cache, MemoryCache, SQLiteCache

_logger = logging.getLogger(__name__)

# Valid values for Literal fields
VALID_MODES = ("fast", "render", "auto")
VALID_CACHE_TYPES = ("memory", "sqlite")
VALID_OUTPUT_FORMATS = ("text", "json", "markdown")
VALID_CHUNKING_STRATEGIES = ("none", "heading", "size")
VALID_POLICIES = ("permissive", "normal", "strict", "paranoid", "moderate")


def _collect_config_init_values(cls, args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[dict[str, Any], frozenset[str]]:
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
                raise TypeError(f"{cls.__name__}.__init__() got multiple values for argument '{config_field.name}'")
            values[config_field.name] = args[index]
            explicit.add(config_field.name)
            continue

        if config_field.name in remaining_kwargs:
            values[config_field.name] = remaining_kwargs.pop(config_field.name)
            explicit.add(config_field.name)
            continue

        if config_field.default is not MISSING:
            values[config_field.name] = config_field.default
            continue

        if config_field.default_factory is not MISSING:
            values[config_field.name] = config_field.default_factory()
            continue

        raise TypeError(f"{cls.__name__}.__init__() missing required argument: '{config_field.name}'")

    if remaining_kwargs:
        unexpected = next(iter(remaining_kwargs))
        raise TypeError(f"{cls.__name__}.__init__() got an unexpected keyword argument '{unexpected}'")

    return values, frozenset(explicit)


@dataclass(init=False)
class Config:
    """MarkDownIngress configuration"""

    # Fetching
    mode: Literal["fast", "render", "auto"] = "auto"
    timeout: float = 30.0

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
    domain_request_interval: float = 0.25
    circuit_breaker_threshold: int = 3
    circuit_breaker_open_seconds: float = 30.0

    # Policy
    policy: str = "normal"
    custom_patterns: list[str] = field(default_factory=list)
    domain_policies: list[dict[str, Any]] = field(default_factory=list)

    # Output
    output_format: Literal["text", "json", "markdown"] = "text"
    output_profile: str = "default"
    extract_blocks: bool = False
    chunking_strategy: Literal["none", "heading", "size"] = "none"
    chunk_size: int = 1200
    chunk_overlap: int = 120
    save_reports: bool = False
    reports_dir: str = "reports"
    _cache_backend: Cache | None = field(default=None, init=False, repr=False)
    _explicit_keys: frozenset[str] = field(default_factory=frozenset, init=False, repr=False)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        values, explicit = _collect_config_init_values(type(self), args, kwargs)
        for key, value in values.items():
            setattr(self, key, value)
        self._cache_backend = None
        self.__post_init__()
        self._explicit_keys = explicit

    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        # Validate Literal fields
        if self.mode not in VALID_MODES:
            raise ValueError(f"Invalid mode '{self.mode}'. Must be one of: {', '.join(VALID_MODES)}")
        if self.cache_type not in VALID_CACHE_TYPES:
            raise ValueError(f"Invalid cache_type '{self.cache_type}'. Must be one of: {', '.join(VALID_CACHE_TYPES)}")
        if self.output_format not in VALID_OUTPUT_FORMATS:
            raise ValueError(f"Invalid output_format '{self.output_format}'. Must be one of: {', '.join(VALID_OUTPUT_FORMATS)}")
        if self.chunking_strategy not in VALID_CHUNKING_STRATEGIES:
            raise ValueError(f"Invalid chunking_strategy '{self.chunking_strategy}'. Must be one of: {', '.join(VALID_CHUNKING_STRATEGIES)}")

        # Validate policy
        if self.policy not in VALID_POLICIES:
            raise ValueError(f"Invalid policy '{self.policy}'. Must be one of: {', '.join(VALID_POLICIES)}")

        # Validate positive values
        if self.timeout <= 0:
            raise ValueError(f"timeout must be positive, got {self.timeout}")
        if self.batch_max_concurrent <= 0:
            raise ValueError(f"batch_max_concurrent must be positive, got {self.batch_max_concurrent}")
        if self.batch_timeout <= 0:
            raise ValueError(f"batch_timeout must be positive, got {self.batch_timeout}")
        if self.cache_ttl <= 0:
            raise ValueError(f"cache_ttl must be positive, got {self.cache_ttl}")
        if self.chunk_size < 100 or self.chunk_size > 50000:
            raise ValueError(f"chunk_size must be between 100 and 50000, got {self.chunk_size}")
        if self.chunk_overlap < 0 or self.chunk_overlap > 10000:
            raise ValueError(f"chunk_overlap must be between 0 and 10000, got {self.chunk_overlap}")

        # Validate custom_patterns are valid regex
        import re

        for pattern in self.custom_patterns:
            try:
                re.compile(pattern)
            except re.error as e:
                raise ValueError(f"Invalid regex pattern '{pattern}': {e}")

    def to_dict(self) -> dict[str, Any]:
        """Convert config to dictionary"""
        return {
            config_field.name: copy.deepcopy(getattr(self, config_field.name))
            for config_field in fields(self)
            if not config_field.name.startswith("_")
        }

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
            return None

        if self._cache_backend is not None:
            return self._cache_backend

        if self.cache_type == "sqlite":
            self._cache_backend = SQLiteCache(db_path=self.cache_path, default_ttl=self.cache_ttl)
            return self._cache_backend

        self._cache_backend = MemoryCache(default_ttl=self.cache_ttl)
        return self._cache_backend

    def to_ingest_config(self) -> IngestConfig:
        """Convert legacy Config into the runtime IngestConfig used by the API/CLI."""
        kwargs: dict[str, Any] = dict(
            mode=self.mode,
            strict=self.strict,
            model=self.model,
            timeout=self.timeout,
            cache=self.create_cache(),
            cache_ttl=self.cache_ttl if self.cache_enabled else None,
            allow_local_urls=self.allow_local_urls,
            policy_name=self.normalized_policy(),
            custom_patterns=list(self.custom_patterns),
            domain_policies=[DomainPolicy(**policy) for policy in self.domain_policies],
            output_format=self.output_format,
            output_profile=self.output_profile,
            extract_blocks=self.extract_blocks,
            chunking_strategy=self.chunking_strategy,
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            save_reports=self.save_reports,
            reports_dir=self.reports_dir,
            domain_request_interval=self.domain_request_interval,
            circuit_breaker_threshold=self.circuit_breaker_threshold,
            circuit_breaker_open_seconds=self.circuit_breaker_open_seconds,
        )
        # Forward render-related settings when present on the legacy Config
        for render_field in ("stealth", "disable_http2", "extreme_mode", "screenshot"):
            if hasattr(self, render_field):
                kwargs[render_field] = getattr(self, render_field)

        ingest_config = IngestConfig(**kwargs)
        config_to_runtime_keys: dict[str, tuple[str, ...]] = {
            "mode": ("mode",),
            "timeout": ("timeout",),
            "strict": ("strict",),
            "allow_local_urls": ("allow_local_urls",),
            "model": ("model",),
            "cache_enabled": ("cache", "cache_ttl"),
            "cache_ttl": ("cache_ttl",),
            "policy": ("policy_name",),
            "custom_patterns": ("custom_patterns",),
            "domain_policies": ("domain_policies",),
            "output_format": ("output_format",),
            "output_profile": ("output_profile",),
            "extract_blocks": ("extract_blocks",),
            "chunking_strategy": ("chunking_strategy",),
            "chunk_size": ("chunk_size",),
            "chunk_overlap": ("chunk_overlap",),
            "save_reports": ("save_reports",),
            "reports_dir": ("reports_dir",),
            "stealth": ("stealth",),
            "disable_http2": ("disable_http2",),
            "extreme_mode": ("extreme_mode",),
            "screenshot": ("screenshot",),
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
        env_mapping: dict[str, tuple[str, Callable[[str], object]]] = {
            "MDI_MODE": ("mode", str),
            "MDI_TIMEOUT": ("timeout", float),
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
            "MDI_OUTPUT_FORMAT": ("output_format", str),
            "MDI_OUTPUT_PROFILE": ("output_profile", str),
            "MDI_EXTRACT_BLOCKS": ("extract_blocks", self._str_to_bool),
            "MDI_CHUNKING_STRATEGY": ("chunking_strategy", str),
            "MDI_CHUNK_SIZE": ("chunk_size", int),
            "MDI_CHUNK_OVERLAP": ("chunk_overlap", int),
            "MDI_SAVE_REPORTS": ("save_reports", self._str_to_bool),
            "MDI_REPORTS_DIR": ("reports_dir", str),
            "MDI_STEALTH": ("stealth", self._str_to_bool),
            "MDI_DISABLE_HTTP2": ("disable_http2", self._str_to_bool),
            "MDI_EXTREME_MODE": ("extreme_mode", self._str_to_bool),
            "MDI_SCREENSHOT": ("screenshot", self._str_to_bool_or_string),
            "MDI_DOMAIN_REQUEST_INTERVAL": ("domain_request_interval", float),
            "MDI_CIRCUIT_BREAKER_THRESHOLD": ("circuit_breaker_threshold", int),
            "MDI_CIRCUIT_BREAKER_OPEN_SECONDS": ("circuit_breaker_open_seconds", float),
        }
        previous_values = {
            attr_name: getattr(config, attr_name)
            for _, (attr_name, _) in env_mapping.items()
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
            patterns = [p.strip() for p in custom_patterns_env.split(",") if p.strip()]
            if patterns:
                config.custom_patterns = patterns
                explicit.add("custom_patterns")

        def _restore(attr_name: str, message: str, *args: object) -> None:
            _logger.warning(message, *args)
            setattr(config, attr_name, previous_values[attr_name])
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
