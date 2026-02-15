"""
Tests for configuration file support
"""

import pytest
import tempfile
import os
from pathlib import Path
from markdown_ingress.core.config import Config, ConfigLoader, load_config


class TestConfig:
    """Test Config dataclass"""
    
    def test_config_defaults(self):
        """Config has sensible defaults"""
        config = Config()
        
        assert config.mode == "fast"
        assert config.timeout == 30.0
        assert config.strict is True
        assert config.model == "gpt-4"
        assert config.cache_enabled is False
        assert config.batch_max_concurrent == 5
    
    def test_config_to_dict(self):
        """Convert Config to dictionary"""
        config = Config(mode="render", timeout=60.0, strict=False)
        data = config.to_dict()
        
        assert isinstance(data, dict)
        assert data['mode'] == "render"
        assert data['timeout'] == 60.0
        assert data['strict'] is False
    
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
        
        assert 'mode: fast' in yaml_str
        assert 'timeout:' in yaml_str
    
    def test_config_from_dict(self):
        """Create Config from dictionary"""
        data = {
            'mode': 'render',
            'timeout': 120.0,
            'strict': False,
            'model': 'claude-3',
            'cache_enabled': True
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
            os.environ['MDI_MODE'] = 'render'
            os.environ['MDI_TIMEOUT'] = '120.0'
            os.environ['MDI_STRICT'] = 'false'
            os.environ['MDI_MODEL'] = 'claude-3'
            
            try:
                loader = ConfigLoader(str(config_path))
                config = loader.load()
                
                assert config.mode == "render"  # Overridden
                assert config.timeout == 120.0  # Overridden
                assert config.strict is False  # Overridden
                assert config.model == "claude-3"  # Overridden
            finally:
                # Clean up env vars
                for key in ['MDI_MODE', 'MDI_TIMEOUT', 'MDI_STRICT', 'MDI_MODEL']:
                    os.environ.pop(key, None)
    
    def test_env_bool_conversion(self):
        """Environment variables convert to boolean correctly"""
        os.environ['MDI_STRICT'] = 'true'
        os.environ['MDI_CACHE_ENABLED'] = '1'
        os.environ['MDI_SAVE_REPORTS'] = 'yes'
        
        try:
            loader = ConfigLoader()
            config = loader.load()
            
            assert config.strict is True
            assert config.cache_enabled is True
            assert config.save_reports is True
        finally:
            for key in ['MDI_STRICT', 'MDI_CACHE_ENABLED', 'MDI_SAVE_REPORTS']:
                os.environ.pop(key, None)
    
    def test_env_custom_patterns(self):
        """Custom patterns from environment variable"""
        os.environ['MDI_CUSTOM_PATTERNS'] = 'pattern1, pattern2, pattern3'
        
        try:
            loader = ConfigLoader()
            config = loader.load()
            
            assert config.custom_patterns == ['pattern1', 'pattern2', 'pattern3']
        finally:
            os.environ.pop('MDI_CUSTOM_PATTERNS', None)
    
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
