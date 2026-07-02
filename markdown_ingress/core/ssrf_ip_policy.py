"""Blocked host and IP range policy for SSRF validation."""

from __future__ import annotations

import ipaddress

from markdown_ingress.core.ssrf_normalization import normalize_hostname

UNSPECIFIED_IPV4_HOST = ".".join(("0", "0", "0", "0"))

EXTRA_BLOCKED_NETWORKS = (
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("fec0::/10"),
)

BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
        "metadata.google.internal",
        "metadata.azure.internal",
        "metadata.azure.net",
        "metadata.oracle.internal",
        "instance-data.ec2.internal",
        "metadata.packet.net",
        "metadata.scaleway.internal",
        "metadata.aliyun.internal",
        "169.254.169.254",
        UNSPECIFIED_IPV4_HOST,
        "::1",  # IPv6 loopback
        "0:0:0:0:0:0:0:1",  # IPv6 loopback (long form)
    }
)


def normalize_ip_for_ssrf(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """Collapse IPv4-mapped IPv6 addresses to IPv4 for network checks."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def is_blocked_hostname(hostname: str) -> bool:
    """Return whether a hostname is explicitly blocked."""
    return normalize_hostname(hostname) in BLOCKED_HOSTNAMES


def is_blocked_ip_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return whether an IP belongs to a non-routable or internal range."""
    normalized = normalize_ip_for_ssrf(ip)
    if (
        normalized.is_private
        or normalized.is_loopback
        or normalized.is_link_local
        or normalized.is_multicast
        or normalized.is_reserved
        or normalized.is_unspecified
    ):
        return True
    return any(
        normalized in network
        for network in EXTRA_BLOCKED_NETWORKS
        if network.version == normalized.version
    )
