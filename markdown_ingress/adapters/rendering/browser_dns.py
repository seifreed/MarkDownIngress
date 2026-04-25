"""Browser DNS pinning helpers for Playwright renderers."""

from __future__ import annotations

from collections.abc import Mapping


def _format_chromium_address(address: str) -> str:
    if ":" in address and not address.startswith("["):
        return f"[{address}]"
    return address


def chromium_host_resolver_rules(dns_pins: Mapping[str, str]) -> str:
    """Build Chromium ``--host-resolver-rules`` entries from validated pins."""
    rules = [
        f"MAP {hostname} {_format_chromium_address(address)}"
        for hostname, address in sorted(dns_pins.items())
        if hostname and address
    ]
    return ",".join(rules)
