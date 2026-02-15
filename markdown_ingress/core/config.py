"""
Configuration file support for MarkDownIngress
"""

import os
import json
import yaml
from pathlib import Path
from typing import Optional, Dict, Any, Literal
from dataclasses import dataclass, field, asdict


@dataclass
class Config:
    """MarkDownIngress configuration"""
    
    # Fetching
    mode: Literal["fast", "render"] = "fast"
    timeout: float = 30.0
    
    # Security
    strict: bool = True
    injection_threshold: float = 0.7
    
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
    
    # Policy
    policy: str = "moderate"
    custom_patterns: list[str] = field(default_factory=list)
    
    # Output
    output_format: Literal["text", "json", "markdown"] = "text"
    save_reports: bool = False
    reports_dir: str = "reports"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert config to dictionary"""
        return asdict(self)
    
    def to_json(self, indent: int = 2) -> str:
        """Export as JSON"""
        return json.dumps(self.to_dict(), indent=indent)
    
    def to_yaml(self) -> str:
        """Export as YAML"""
        return yaml.dump(self.to_dict(), default_flow_style=False)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Config':
        """Create config from dictionary"""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
    
    @classmethod
    def from_json(cls, json_str: str) -> 'Config':
        """Load config from JSON string"""
        data = json.loads(json_str)
        return cls.from_dict(data)
    
    @classmethod
    def from_yaml(cls, yaml_str: str) -> 'Config':
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
    
    def __init__(self, config_path: Optional[str] = None):
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
        if filepath.endswith('.json'):
            return Config.from_json(content)
        elif filepath.endswith(('.yaml', '.yml')):
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
        env_mapping = {
            'MDI_MODE': ('mode', str),
            'MDI_TIMEOUT': ('timeout', float),
            'MDI_STRICT': ('strict', self._str_to_bool),
            'MDI_INJECTION_THRESHOLD': ('injection_threshold', float),
            'MDI_MODEL': ('model', str),
            'MDI_CACHE_ENABLED': ('cache_enabled', self._str_to_bool),
            'MDI_CACHE_TYPE': ('cache_type', str),
            'MDI_CACHE_TTL': ('cache_ttl', int),
            'MDI_CACHE_PATH': ('cache_path', str),
            'MDI_BATCH_MAX_CONCURRENT': ('batch_max_concurrent', int),
            'MDI_BATCH_TIMEOUT': ('batch_timeout', float),
            'MDI_POLICY': ('policy', str),
            'MDI_OUTPUT_FORMAT': ('output_format', str),
            'MDI_SAVE_REPORTS': ('save_reports', self._str_to_bool),
            'MDI_REPORTS_DIR': ('reports_dir', str),
        }
        
        for env_var, (attr_name, converter) in env_mapping.items():
            value = os.getenv(env_var)
            if value is not None:
                try:
                    setattr(config, attr_name, converter(value))
                except (ValueError, TypeError):
                    # Skip invalid values
                    pass
        
        # Handle custom patterns (comma-separated)
        custom_patterns_env = os.getenv('MDI_CUSTOM_PATTERNS')
        if custom_patterns_env:
            patterns = [p.strip() for p in custom_patterns_env.split(',') if p.strip()]
            if patterns:
                config.custom_patterns = patterns
        
        return config
    
    @staticmethod
    def _str_to_bool(value: str) -> bool:
        """Convert string to boolean"""
        return value.lower() in ('true', '1', 'yes', 'on', 'enabled')
    
    def save(self, config: Config, filepath: str):
        """
        Save configuration to file.
        
        Args:
            config: Config object to save
            filepath: Output file path (.json or .yaml)
        """
        path = Path(filepath).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        
        if filepath.endswith('.json'):
            content = config.to_json()
        elif filepath.endswith(('.yaml', '.yml')):
            content = config.to_yaml()
        else:
            raise ValueError("Config file must be .json, .yaml, or .yml")
        
        path.write_text(content)


def load_config(config_path: Optional[str] = None) -> Config:
    """
    Convenience function to load configuration.
    
    Args:
        config_path: Optional explicit config file path
        
    Returns:
        Loaded Config object
    """
    loader = ConfigLoader(config_path)
    return loader.load()
