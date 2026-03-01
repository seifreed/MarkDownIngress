"""
Plugin system for extending MarkDownIngress with custom patterns and extractors
"""

import importlib
import inspect
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class PluginInfo:
    """Plugin metadata"""

    name: str
    version: str
    description: str
    author: str = ""


class Plugin(ABC):
    """Base class for MarkDownIngress plugins"""

    def __init__(self):
        self.info = PluginInfo(
            name=self.__class__.__name__, version="0.1.0", description="Custom plugin"
        )

    @abstractmethod
    def get_patterns(self) -> list[str]:
        """
        Return list of regex patterns for injection detection.

        Returns:
            List of regex pattern strings
        """
        pass  # pragma: no cover

    def get_config(self) -> dict[str, Any]:
        """
        Optional: Return plugin configuration.

        Returns:
            Configuration dictionary
        """
        return {}

    def on_load(self):
        """Optional: Called when plugin is loaded"""
        pass

    def on_unload(self):
        """Optional: Called when plugin is unloaded"""
        pass


class PluginLoader:
    """Load and manage plugins"""

    def __init__(self):
        self.plugins: dict[str, Plugin] = {}

    def load_plugin(self, plugin: Plugin):
        """
        Register a plugin instance.

        Args:
            plugin: Plugin instance to register
        """
        name = plugin.info.name

        if name in self.plugins:
            raise ValueError(f"Plugin '{name}' already loaded")

        plugin.on_load()
        self.plugins[name] = plugin

    def unload_plugin(self, name: str):
        """
        Unload a plugin by name.

        Args:
            name: Plugin name
        """
        if name not in self.plugins:
            raise KeyError(f"Plugin '{name}' not found")

        plugin = self.plugins[name]
        plugin.on_unload()
        del self.plugins[name]

    def load_from_directory(self, directory: str) -> int:
        """
        Discover and load plugins from a directory.

        Looks for Python files defining Plugin subclasses.

        Args:
            directory: Path to plugin directory

        Returns:
            Number of plugins loaded
        """
        dir_path = Path(directory)

        if not dir_path.exists() or not dir_path.is_dir():
            raise FileNotFoundError(f"Plugin directory not found: {directory}")

        loaded_count = 0

        # Find all .py files
        for py_file in dir_path.glob("*.py"):
            if py_file.name.startswith("_"):
                continue  # Skip __init__.py and _private.py

            try:
                # Import module
                module_name = py_file.stem
                spec = importlib.util.spec_from_file_location(module_name, py_file)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                # Find Plugin subclasses
                for name, obj in inspect.getmembers(module, inspect.isclass):
                    if issubclass(obj, Plugin) and obj is not Plugin:
                        # Instantiate and load
                        plugin_instance = obj()
                        self.load_plugin(plugin_instance)
                        loaded_count += 1

            except Exception:
                # Skip files that fail to import
                pass

        return loaded_count

    def get_all_patterns(self) -> list[str]:
        """
        Get combined patterns from all loaded plugins.

        Returns:
            List of all regex patterns
        """
        patterns = []
        for plugin in self.plugins.values():
            patterns.extend(plugin.get_patterns())
        return patterns

    def get_plugin(self, name: str) -> Plugin | None:
        """
        Get plugin by name.

        Args:
            name: Plugin name

        Returns:
            Plugin instance or None
        """
        return self.plugins.get(name)

    def list_plugins(self) -> list[PluginInfo]:
        """
        List all loaded plugins.

        Returns:
            List of plugin metadata
        """
        return [p.info for p in self.plugins.values()]
