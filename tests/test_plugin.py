"""
Tests for plugin system
"""

import tempfile
from pathlib import Path

import pytest

from markdown_ingress.core.plugin import Plugin, PluginInfo, PluginLoader


class TestPlugin(Plugin):
    """Test plugin implementation"""

    def __init__(self):
        super().__init__()
        self.info = PluginInfo(
            name="TestPlugin", version="1.0.0", description="Test plugin for unit tests"
        )

    def get_patterns(self):
        return [r"test pattern 1", r"test pattern 2"]


class AnotherPlugin(Plugin):
    """Another test plugin"""

    def get_patterns(self):
        return [r"another pattern"]


class TestPluginSystem:
    """Test plugin loading and management"""

    def test_load_plugin_instance(self):
        """Load plugin instance directly"""
        loader = PluginLoader()
        plugin = TestPlugin()

        loader.load_plugin(plugin)

        assert "TestPlugin" in loader.plugins
        assert loader.get_plugin("TestPlugin") is plugin

    def test_get_all_patterns(self):
        """Get patterns from multiple plugins"""
        loader = PluginLoader()
        loader.load_plugin(TestPlugin())
        loader.load_plugin(AnotherPlugin())

        patterns = loader.get_all_patterns()

        assert len(patterns) == 3
        assert "test pattern 1" in patterns
        assert "test pattern 2" in patterns
        assert "another pattern" in patterns

    def test_unload_plugin(self):
        """Unload plugin by name"""
        loader = PluginLoader()
        loader.load_plugin(TestPlugin())

        assert "TestPlugin" in loader.plugins

        loader.unload_plugin("TestPlugin")

        assert "TestPlugin" not in loader.plugins

    def test_list_plugins(self):
        """List all loaded plugins"""
        loader = PluginLoader()
        loader.load_plugin(TestPlugin())

        plugins = loader.list_plugins()

        assert len(plugins) == 1
        assert plugins[0].name == "TestPlugin"
        assert plugins[0].version == "1.0.0"

    def test_load_from_directory(self):
        """Load plugins from directory"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a plugin file
            plugin_file = Path(tmpdir) / "my_plugin.py"
            plugin_file.write_text("""
from markdown_ingress.core.plugin import Plugin

class MyCustomPlugin(Plugin):
    def get_patterns(self):
        return ['custom pattern']
""")

            loader = PluginLoader()
            count = loader.load_from_directory(tmpdir)

            assert count == 1
            assert len(loader.plugins) == 1

            patterns = loader.get_all_patterns()
            assert "custom pattern" in patterns

    def test_plugin_already_loaded_error(self):
        """Loading same plugin twice raises error"""
        loader = PluginLoader()
        plugin = TestPlugin()

        loader.load_plugin(plugin)

        with pytest.raises(ValueError, match="already loaded"):
            loader.load_plugin(TestPlugin())  # Same name
