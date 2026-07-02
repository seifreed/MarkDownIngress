"""
Plugin system for extending MarkDownIngress with custom patterns and extractors
"""

import hashlib
import importlib
import importlib.util
import inspect
import logging
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

_logger = logging.getLogger(__name__)

PLUGIN_HOOK_ERRORS: tuple[type[Exception], ...] = (
    AttributeError,
    LookupError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)


@dataclass
class PluginInfo:
    """Plugin metadata"""

    name: str
    version: str
    description: str
    author: str = ""


class Plugin(ABC):
    """Base class for MarkDownIngress plugins"""

    def __init__(self) -> None:
        self.info = PluginInfo(
            name=self.__class__.__name__, version="0.1.0", description="Custom plugin"
        )

    @abstractmethod
    def get_patterns(self) -> list[str] | list[tuple[str, float]]:
        """
        Return list of regex patterns for injection detection.

        Returns:
            List of regex pattern strings, or (pattern, weight) tuples for weighted patterns.
        """

    def get_config(self) -> dict[str, Any]:
        """
        Optional: Return plugin configuration.

        Returns:
            Configuration dictionary
        """
        return {}

    def on_load(self) -> None:
        """Optional: Called when plugin is loaded"""

    def on_unload(self) -> None:
        """Optional: Called when plugin is unloaded"""


class PluginLoader:
    """Load and manage plugins"""

    def __init__(self) -> None:
        self.plugins: dict[str, Plugin] = {}

    def load_plugin(self, plugin: Plugin) -> None:
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

    def unload_plugin(self, name: str) -> None:
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

    @staticmethod
    def _module_name_for_file(py_file: Path) -> str:
        """Return a stable synthetic module name for a plugin file."""
        digest = hashlib.sha256(str(py_file.resolve(strict=False)).encode()).hexdigest()[:12]
        return f"_mdi_plugin_{py_file.stem}_{digest}"

    def _rollback_file_plugins(self, py_file: Path, plugin_names: list[str]) -> int:
        """Remove plugins loaded from a file that later failed to finish loading."""
        removed = 0
        for plugin_name in reversed(plugin_names):
            plugin = self.plugins.pop(plugin_name, None)
            if plugin is None:
                continue
            removed += 1
            try:
                plugin.on_unload()
            except PLUGIN_HOOK_ERRORS as exc:
                _logger.warning(
                    "Failed to unload partially loaded plugin %s from %s: %s",
                    plugin_name,
                    py_file,
                    exc,
                )
        return removed

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

        # Find all .py files in a deterministic order so plugin pattern
        # numbering and cache identities stay stable across filesystems.
        for py_file in sorted(dir_path.glob("*.py"), key=lambda p: p.name):
            if py_file.name.startswith("_"):
                continue  # Skip __init__.py and _private.py

            module_name = self._module_name_for_file(py_file)
            loaded_from_file: list[str] = []
            try:
                # Import module
                spec = importlib.util.spec_from_file_location(module_name, py_file)
                if spec is None or spec.loader is None:
                    continue
                module: ModuleType = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)

                # Find Plugin subclasses
                for _name, obj in inspect.getmembers(module, inspect.isclass):
                    if (
                        obj.__module__ == module.__name__
                        and issubclass(obj, Plugin)
                        and obj is not Plugin
                    ):
                        # Instantiate and load
                        plugin_instance = obj()
                        self.load_plugin(plugin_instance)
                        loaded_from_file.append(plugin_instance.info.name)
                        loaded_count += 1

            except Exception as exc:  # noqa: BLE001 - bad external plugins are skipped
                sys.modules.pop(module_name, None)
                loaded_count -= self._rollback_file_plugins(py_file, loaded_from_file)
                _logger.warning("Failed to load plugin from %s: %s", py_file, exc)

        return loaded_count

    def get_all_patterns(self) -> list[tuple[str, float]]:
        """
        Get combined patterns from all loaded plugins.

        Returns:
            List of (regex_pattern, weight) tuples
        """
        pairs: list[tuple[str, float]] = []
        for plugin in self.plugins.values():
            raw = plugin.get_patterns()
            for item in raw:
                if isinstance(item, tuple):
                    pairs.append(item)
                else:
                    pairs.append((item, 0.5))
        return pairs

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


def fingerprint_plugin_directories(directories: list[str] | None) -> list[dict[str, Any]]:
    """Return a stable fingerprint for plugin directory paths and contents.

    The ingestion identity needs to reflect the actual plugin code loaded from
    each directory, not just the directory path, otherwise cache hits can
    return stale security results after a plugin file changes in place.
    """
    if not directories:
        return []

    fingerprints: list[dict[str, Any]] = []
    for directory in directories:
        path = Path(directory).expanduser()
        normalized_path = str(path.resolve(strict=False))
        hasher = hashlib.sha256()
        file_entries: list[dict[str, Any]] = []

        if not path.exists():
            hasher.update(b"missing")
            fingerprints.append(
                {
                    "directory": normalized_path,
                    "digest": hasher.hexdigest(),
                    "missing": True,
                }
            )
            continue

        if not path.is_dir():
            hasher.update(b"not_directory")
            fingerprints.append(
                {
                    "directory": normalized_path,
                    "digest": hasher.hexdigest(),
                    "not_directory": True,
                }
            )
            continue

        for py_file in sorted(path.glob("*.py"), key=lambda p: p.name):
            if py_file.name.startswith("_"):
                continue
            try:
                content = py_file.read_bytes()
            except OSError as exc:
                error_tag = type(exc).__name__
                file_entries.append({"name": py_file.name, "error": error_tag})
                hasher.update(f"error:{py_file.name}:{error_tag}".encode())
                continue

            file_hash = hashlib.sha256(content).hexdigest()
            file_entries.append(
                {
                    "name": py_file.name,
                    "sha256": file_hash,
                    "size": len(content),
                }
            )
            hasher.update(f"file:{py_file.name}:{file_hash}:{len(content)}".encode())

        fingerprints.append(
            {
                "directory": normalized_path,
                "digest": hasher.hexdigest(),
                "files": file_entries,
            }
        )

    return fingerprints
