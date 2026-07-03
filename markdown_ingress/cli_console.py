"""Console configuration shared by CLI output helpers."""

from __future__ import annotations

import sys

from rich.console import Console

from markdown_ingress.core.config_env import read_env

_console_force_terminal = sys.stdout.isatty() if hasattr(sys.stdout, "isatty") else False
_console_no_color = (
    read_env("NO_COLOR") is not None
    or (hasattr(sys.stdout, "isatty") and not sys.stdout.isatty())
    or (hasattr(sys.stderr, "isatty") and not sys.stderr.isatty())
)

console = Console(
    force_terminal=_console_force_terminal,
    legacy_windows=sys.platform == "win32",
    no_color=_console_no_color,
)
