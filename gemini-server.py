#!/usr/bin/env python3
"""Local Gemini proxy for a running MarkDownIngress HTTP API.

The server accepts Gemini requests over TLS, asks MarkDownIngress to ingest the
target HTTP(S) URL, converts the returned Markdown into text/gemini, and rewrites
HTTP(S) links back through this same Gemini proxy.
"""

from __future__ import annotations

import argparse
import base64
import ipaddress
import json
import logging
import mimetypes
import os
import re
import shutil
import socket
import socketserver
import ssl
import subprocess
import sys
import textwrap
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

GEMINI_SUCCESS = 20
GEMINI_INPUT = 10
GEMINI_TEMPORARY_FAILURE = 40
GEMINI_CGI_ERROR = 42
GEMINI_PROXY_ERROR = 43
GEMINI_PERMANENT_FAILURE = 50
GEMINI_PROXY_REFUSED = 53
GEMINI_BAD_REQUEST = 59

MAX_GEMINI_REQUEST_BYTES = 1024
MAX_BINARY_BYTES = 15 * 1024 * 1024
DEFAULT_LISTEN_HOST = "127.0.0.1"
DEFAULT_GEMINI_PORT = 1965
DEFAULT_API_URL = "http://127.0.0.1:8000"
DEFAULT_PEM_DIR = "pem"
DEFAULT_CERT_NAME = "gemini-cert.pem"
DEFAULT_KEY_NAME = "gemini-key.pem"
PROXY_PARAM_PREFIX = "__mdi_"
PROXY_SCHEME_PARAM = "__mdi_scheme"
LEGACY_PROXY_SCHEME_PARAM = "__markdowningress_proxy_scheme"
PROXY_METHOD_PARAM = "__mdi_method"
PROXY_MODE_PARAM = "__mdi_mode"
PROXY_RESOURCE_PARAM = "__mdi_resource"
PROXY_TIMEOUT_PARAM = "__mdi_timeout"
PROXY_STRICT_PARAM = "__mdi_strict"
PROXY_USER_AGENT_PARAMS = frozenset({"__mdi_ua", "__mdi_user_agent"})
IMAGE_EXTENSIONS = frozenset(
    {
        ".apng",
        ".avif",
        ".bmp",
        ".gif",
        ".ico",
        ".jpeg",
        ".jpg",
        ".png",
        ".svg",
        ".webp",
    }
)

LOG = logging.getLogger("gemini-server")


class GeminiError(Exception):
    """Error that maps directly to a Gemini status response."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class GeminiResponse:
    status: int
    meta: str
    body: str | bytes = b""

    def to_bytes(self) -> bytes:
        meta = sanitize_meta(self.meta)
        header = f"{self.status} {meta}\r\n".encode("utf-8", errors="replace")
        if isinstance(self.body, bytes):
            return header + self.body
        return header + self.body.encode("utf-8", errors="replace")


@dataclass(frozen=True)
class LinkMatch:
    start: int
    end: int
    label: str
    url: str
    is_image: bool = False


@dataclass
class ConversionResult:
    body: str
    seen_urls: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class RequestOptions:
    target_method: str = "GET"
    resource_type: str | None = None
    ingest_mode: str | None = None
    ingest_timeout: float | None = None
    strict: bool | None = None
    fetcher_user_agent: str | None = None


@dataclass(frozen=True)
class TargetRequest:
    url: str
    options: RequestOptions = field(default_factory=RequestOptions)


@dataclass(frozen=True)
class ServerConfig:
    listen_host: str
    listen_port: int
    public_host: str
    public_port: int
    api_ingest_url: str
    api_key: str | None
    ingest_mode: str
    ingest_timeout: float
    upstream_timeout: float
    strict: bool
    fetcher_user_agent: str
    max_extra_links: int


_BARE_HTTP_RE = re.compile(r"\bhttps?://[^\s<>\]]+", re.IGNORECASE)
_REFERENCE_LINK_RE = re.compile(r"^\s{0,3}\[([^\]]+)\]:\s*(\S+)(?:\s+.*)?$")
_HOSTISH_RE = re.compile(
    r"^(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?::[0-9]{1,5})?(?:[/?#].*)?$"
)
_LEADING_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_WHITESPACE_RE = re.compile(r"\s+")


def sanitize_meta(meta: str) -> str:
    """Return a Gemini header meta string without CR/LF and under the protocol cap."""
    clean = str(meta).replace("\r", " ").replace("\n", " ").strip()
    return clean[:1024] or "OK"


def b64url_encode(value: str) -> str:
    raw = base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii")
    return raw.rstrip("=")


def b64url_decode(value: str) -> str:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode((value + padding).encode("ascii")).decode("utf-8")
    except (UnicodeDecodeError, ValueError) as exc:
        raise GeminiError(GEMINI_BAD_REQUEST, "Bad proxied URL encoding") from exc


def normalize_api_ingest_url(api_url: str) -> str:
    parsed = urllib.parse.urlsplit(api_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("--api-url must be an http(s) URL")
    path = parsed.path.rstrip("/")
    if path.endswith("/api/v1/ingest"):
        return urllib.parse.urlunsplit(parsed)
    root = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    return f"{root}/api/v1/ingest"


def parse_bool_option(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise GeminiError(GEMINI_BAD_REQUEST, f"Invalid {PROXY_STRICT_PARAM} value")


def parse_timeout_option(value: str) -> float:
    try:
        timeout = float(value)
    except ValueError as exc:
        raise GeminiError(GEMINI_BAD_REQUEST, f"Invalid {PROXY_TIMEOUT_PARAM} value") from exc
    if timeout < 1.0 or timeout > 300.0:
        raise GeminiError(GEMINI_BAD_REQUEST, f"{PROXY_TIMEOUT_PARAM} must be between 1 and 300")
    return timeout


def parse_mode_option(value: str) -> str:
    mode = value.strip().lower()
    if mode not in {"fast", "render", "auto"}:
        raise GeminiError(GEMINI_BAD_REQUEST, f"Invalid {PROXY_MODE_PARAM} value")
    return mode


def parse_resource_option(value: str) -> str | None:
    resource = value.strip().lower()
    if resource in {"", "page"}:
        return None
    if resource == "image":
        return resource
    raise GeminiError(GEMINI_BAD_REQUEST, f"Invalid {PROXY_RESOURCE_PARAM} value")


def parse_method_option(value: str) -> str:
    method = value.strip().upper()
    if not method:
        return "GET"
    if method not in {"GET", "POST"}:
        raise GeminiError(GEMINI_BAD_REQUEST, f"Unsupported {PROXY_METHOD_PARAM} value")
    return method


def split_proxy_query(raw_query: str, *, default_scheme: str = "https") -> tuple[str, str, RequestOptions]:
    """Split target query parameters from Gemini-proxy control parameters."""
    scheme = default_scheme
    method = "GET"
    resource_type: str | None = None
    mode: str | None = None
    timeout: float | None = None
    strict: bool | None = None
    user_agent: str | None = None
    target_pairs: list[tuple[str, str]] = []

    for key, value in urllib.parse.parse_qsl(raw_query, keep_blank_values=True):
        if key in {PROXY_SCHEME_PARAM, LEGACY_PROXY_SCHEME_PARAM}:
            if value not in {"http", "https"}:
                raise GeminiError(GEMINI_BAD_REQUEST, f"Invalid {PROXY_SCHEME_PARAM} value")
            scheme = value
        elif key == PROXY_METHOD_PARAM:
            method = parse_method_option(value)
        elif key == PROXY_RESOURCE_PARAM:
            resource_type = parse_resource_option(value)
        elif key == PROXY_MODE_PARAM:
            mode = parse_mode_option(value)
        elif key == PROXY_TIMEOUT_PARAM:
            timeout = parse_timeout_option(value)
        elif key == PROXY_STRICT_PARAM:
            strict = parse_bool_option(value)
        elif key in PROXY_USER_AGENT_PARAMS:
            user_agent = value
        elif key.startswith(PROXY_PARAM_PREFIX) or key.startswith("__markdowningress_"):
            raise GeminiError(GEMINI_BAD_REQUEST, f"Unknown MarkDownIngress proxy option: {key}")
        else:
            target_pairs.append((key, value))

    options = RequestOptions(
        target_method=method,
        resource_type=resource_type,
        ingest_mode=mode,
        ingest_timeout=timeout,
        strict=strict,
        fetcher_user_agent=user_agent,
    )
    return scheme, urllib.parse.urlencode(target_pairs, doseq=True), options


def merge_request_options(base: RequestOptions, override: RequestOptions | None) -> RequestOptions:
    if override is None:
        return base
    return RequestOptions(
        target_method=override.target_method or base.target_method,
        resource_type=(
            override.resource_type if override.resource_type is not None else base.resource_type
        ),
        ingest_mode=override.ingest_mode if override.ingest_mode is not None else base.ingest_mode,
        ingest_timeout=(
            override.ingest_timeout if override.ingest_timeout is not None else base.ingest_timeout
        ),
        strict=override.strict if override.strict is not None else base.strict,
        fetcher_user_agent=(
            override.fetcher_user_agent
            if override.fetcher_user_agent is not None
            else base.fetcher_user_agent
        ),
    )


def serialized_proxy_option_pairs(options: RequestOptions | None) -> list[tuple[str, str]]:
    if options is None:
        return []
    pairs: list[tuple[str, str]] = []
    if options.target_method and options.target_method != "GET":
        pairs.append((PROXY_METHOD_PARAM, options.target_method))
    if options.resource_type is not None:
        pairs.append((PROXY_RESOURCE_PARAM, options.resource_type))
    if options.ingest_mode is not None:
        pairs.append((PROXY_MODE_PARAM, options.ingest_mode))
    if options.ingest_timeout is not None:
        pairs.append((PROXY_TIMEOUT_PARAM, f"{options.ingest_timeout:g}"))
    if options.strict is not None:
        pairs.append((PROXY_STRICT_PARAM, "1" if options.strict else "0"))
    if options.fetcher_user_agent is not None:
        pairs.append(("__mdi_ua", options.fetcher_user_agent))
    return pairs


def clean_label(label: str, fallback: str = "link") -> str:
    label = label.replace("\r", " ").replace("\n", " ")
    label = label.replace("`", "").replace("*", "").replace("_", "")
    label = _WHITESPACE_RE.sub(" ", label).strip()
    if not label:
        label = fallback
    return label[:240]


def split_trailing_punctuation(url: str) -> tuple[str, str]:
    trailing = ""
    while url and url[-1] in ".,;:!?":
        trailing = url[-1] + trailing
        url = url[:-1]
    while url.endswith(")") and url.count("(") < url.count(")"):
        trailing = ")" + trailing
        url = url[:-1]
    return url, trailing


def strip_markdown_destination(raw_destination: str) -> str:
    value = raw_destination.strip()
    if value.startswith("<") and ">" in value:
        return value[1 : value.find(">")].strip()
    if not value:
        return value
    if value[0] in {"'", '"'}:
        return value
    return value.split(None, 1)[0].strip()


def find_unescaped(text: str, needle: str, start: int) -> int:
    pos = start
    while True:
        pos = text.find(needle, pos)
        if pos == -1:
            return -1
        backslashes = 0
        idx = pos - 1
        while idx >= 0 and text[idx] == "\\":
            backslashes += 1
            idx -= 1
        if backslashes % 2 == 0:
            return pos
        pos += 1


def find_matching_paren(text: str, open_paren: int) -> int:
    depth = 0
    in_angle = False
    quote: str | None = None
    idx = open_paren
    while idx < len(text):
        ch = text[idx]
        prev_escape = idx > 0 and text[idx - 1] == "\\"
        if quote:
            if ch == quote and not prev_escape:
                quote = None
        elif ch in {"'", '"'}:
            quote = ch
        elif ch == "<":
            in_angle = True
        elif ch == ">":
            in_angle = False
        elif not in_angle:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return idx
        idx += 1
    return -1


def find_matching_bracket(text: str, open_bracket: int) -> int:
    depth = 0
    idx = open_bracket
    while idx < len(text):
        ch = text[idx]
        prev_escape = idx > 0 and text[idx - 1] == "\\"
        if not prev_escape:
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    return idx
        idx += 1
    return -1


def scan_markdown_links(line: str) -> list[LinkMatch]:
    matches: list[LinkMatch] = []
    idx = 0
    while idx < len(line):
        open_bracket = find_unescaped(line, "[", idx)
        if open_bracket == -1:
            break
        is_image = open_bracket > 0 and line[open_bracket - 1] == "!"
        start = open_bracket - 1 if is_image else open_bracket
        close_bracket = find_matching_bracket(line, open_bracket)
        if close_bracket == -1 or close_bracket + 1 >= len(line) or line[close_bracket + 1] != "(":
            idx = open_bracket + 1
            continue
        close_paren = find_matching_paren(line, close_bracket + 1)
        if close_paren == -1:
            idx = open_bracket + 1
            continue
        label = line[open_bracket + 1 : close_bracket]
        destination = strip_markdown_destination(line[close_bracket + 2 : close_paren])
        if destination:
            matches.append(
                LinkMatch(
                    start=start,
                    end=close_paren + 1,
                    label=clean_label(label, destination),
                    url=destination,
                    is_image=is_image,
                )
            )
        idx = close_paren + 1
    return matches


def embedded_image_link(label: str) -> LinkMatch | None:
    for match in scan_markdown_links(label):
        if match.is_image:
            return match
    return None


def display_label_for_match(match: LinkMatch) -> str:
    if match.is_image:
        return "Image: " + clean_label(match.label, match.url)
    embedded_image = embedded_image_link(match.label)
    if embedded_image is not None:
        return "Image: " + clean_label(embedded_image.label, embedded_image.url)
    return clean_label(match.label, match.url)


def replace_markdown_links_with_labels(line: str, matches: list[LinkMatch]) -> str:
    if not matches:
        return line
    parts: list[str] = []
    cursor = 0
    for match in matches:
        parts.append(line[cursor : match.start])
        parts.append(display_label_for_match(match))
        cursor = match.end
    parts.append(line[cursor:])
    return "".join(parts)


def request_authority(request_url: str, config: ServerConfig) -> str:
    parsed = urllib.parse.urlsplit(request_url)
    if parsed.netloc:
        return parsed.netloc
    if config.public_port == 1965:
        return config.public_host
    return f"{config.public_host}:{config.public_port}"


def proxied_gemini_url(
    source_url: str,
    authority: str,
    options: RequestOptions | None = None,
    *,
    absolute: bool = False,
) -> str:
    """Map an HTTP(S) URL to a visible target-shaped Gemini proxy URL.

    The generated URL keeps the target host immediately after the Gemini server
    authority, for example:

        https://example.com/a?b=c -> gemini://localhost/example.com/a?b=c

    HTTPS is the default. HTTP links carry one reserved query parameter so the
    server can reconstruct the original URL without hiding the target host.
    """
    parsed = urllib.parse.urlsplit(source_url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"cannot proxy non-web URL: {source_url}")

    netloc = urllib.parse.quote(parsed.netloc, safe=":@[]")
    path = urllib.parse.quote(parsed.path or "/", safe="/%:@!$&'()*+,;=")
    proxy_path = f"/{netloc}{path}"
    query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    if parsed.scheme.lower() != "https":
        query_pairs.append((PROXY_SCHEME_PARAM, parsed.scheme.lower()))
    query_pairs.extend(serialized_proxy_option_pairs(options))
    query = urllib.parse.urlencode(query_pairs, doseq=True)
    if absolute:
        return urllib.parse.urlunsplit(("gemini", authority, proxy_path, query, ""))
    if query:
        return f"{proxy_path}?{query}"
    return proxy_path


def should_skip_link(url: str) -> bool:
    scheme = urllib.parse.urlsplit(url).scheme.lower()
    return scheme in {"javascript", "data", "blob", "about"}


def normalize_source_url(raw_url: str, base_url: str | None = None) -> str | None:
    raw_url = raw_url.strip()
    if not raw_url:
        return None
    if should_skip_link(raw_url):
        return None
    if raw_url.startswith("//"):
        raw_url = "https:" + raw_url
    elif base_url and not _LEADING_SCHEME_RE.match(raw_url):
        raw_url = urllib.parse.urljoin(base_url, raw_url)
    elif not _LEADING_SCHEME_RE.match(raw_url):
        if _HOSTISH_RE.match(raw_url):
            raw_url = "https://" + raw_url
        else:
            return None

    parsed = urllib.parse.urlsplit(raw_url)
    if parsed.scheme.lower() not in {"http", "https"}:
        return None
    if not parsed.netloc:
        return None
    normalized = urllib.parse.urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc,
            parsed.path or "/",
            parsed.query,
            parsed.fragment,
        )
    )
    return normalized


def gemini_link_for_url(
    raw_url: str,
    base_url: str,
    authority: str,
    options: RequestOptions | None,
    *,
    resource_type: str | None = None,
) -> tuple[str, str] | None:
    source_url = normalize_source_url(raw_url, base_url)
    if source_url is None:
        return None
    link_options = options
    if resource_type is not None and not (
        resource_type == "image" and is_probable_image_url(source_url)
    ):
        link_options = merge_request_options(
            options or RequestOptions(),
            RequestOptions(resource_type=resource_type),
        )
    return source_url, proxied_gemini_url(source_url, authority, link_options)


def convert_line_to_gemini(
    line: str,
    *,
    base_url: str,
    authority: str,
    options: RequestOptions | None,
    seen_urls: set[str],
) -> list[str]:
    reference_match = _REFERENCE_LINK_RE.match(line)
    if reference_match:
        label = clean_label(reference_match.group(1), "link")
        link = gemini_link_for_url(reference_match.group(2), base_url, authority, options)
        if link is None:
            return []
        source_url, proxy_url = link
        seen_urls.add(source_url)
        return [f"=> {proxy_url} {label}"]

    markdown_links = scan_markdown_links(line)
    display_line = replace_markdown_links_with_labels(line, markdown_links)
    explicit_link_lines: list[str] = []
    for link_match in markdown_links:
        embedded_image = embedded_image_link(link_match.label) if not link_match.is_image else None
        if embedded_image is not None:
            image_link = gemini_link_for_url(
                embedded_image.url,
                base_url,
                authority,
                options,
                resource_type="image",
            )
            if image_link is not None:
                image_source_url, image_proxy_url = image_link
                seen_urls.add(image_source_url)
                image_label = "Image: " + clean_label(embedded_image.label, image_source_url)
                explicit_link_lines.append(f"=> {image_proxy_url} {image_label}")

        link = gemini_link_for_url(
            link_match.url,
            base_url,
            authority,
            options,
            resource_type="image" if link_match.is_image else None,
        )
        if link is None:
            continue
        source_url, proxy_url = link
        seen_urls.add(source_url)
        if embedded_image is not None:
            label = "Open linked page: " + clean_label(link_match.url, source_url)
        else:
            label = clean_label(link_match.label, source_url)
        if link_match.is_image and not label.lower().startswith("image:"):
            label = "Image: " + label
        explicit_link_lines.append(f"=> {proxy_url} {label}")

    bare_link_lines: list[str] = []
    pieces: list[str] = []
    cursor = 0
    for match in _BARE_HTTP_RE.finditer(display_line):
        raw_match = match.group(0)
        raw_url, trailing = split_trailing_punctuation(raw_match)
        link = gemini_link_for_url(raw_url, base_url, authority, options)
        if link is None:
            continue
        source_url, proxy_url = link
        seen_urls.add(source_url)
        pieces.append(display_line[cursor : match.start()])
        pieces.append(source_url)
        pieces.append(trailing)
        cursor = match.end()
        bare_link_lines.append(f"=> {proxy_url} {source_url}")
    if pieces:
        pieces.append(display_line[cursor:])
        display_line = "".join(pieces)

    surrounding_text = display_line.strip()
    link_only = bool(markdown_links or bare_link_lines) and not surrounding_text.strip("-*+> ")
    output: list[str] = []
    if surrounding_text and not link_only:
        output.append(display_line.rstrip())
    output.extend(explicit_link_lines)
    output.extend(bare_link_lines)
    if not output and line.strip():
        output.append(line.rstrip())
    return output


def iter_extra_link_urls(links_payload: Any) -> Iterable[str]:
    if not isinstance(links_payload, dict):
        return []
    collected: list[str] = []
    for key in ("internal", "external", "anchors"):
        values = links_payload.get(key)
        if not isinstance(values, list):
            continue
        for value in values:
            if isinstance(value, str):
                collected.append(value)
    return collected


@dataclass(frozen=True)
class BinaryResource:
    content_type: str
    data: bytes


def is_probable_image_url(url: str) -> bool:
    path = urllib.parse.urlsplit(url).path.lower()
    suffix = Path(path).suffix
    return suffix in IMAGE_EXTENSIONS


def is_blocked_ip_address(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    return any(
        (
            ip.is_loopback,
            ip.is_private,
            ip.is_link_local,
            ip.is_multicast,
            ip.is_reserved,
            ip.is_unspecified,
        )
    )


def validate_binary_fetch_target(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise GeminiError(GEMINI_BAD_REQUEST, "Images must use an http(s) URL")
    host = parsed.hostname
    if host.lower() in {"localhost"} or is_blocked_ip_address(host):
        raise GeminiError(GEMINI_PROXY_REFUSED, "Refusing to fetch private image target")
    try:
        addresses = socket.getaddrinfo(host, parsed.port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise GeminiError(GEMINI_PROXY_ERROR, f"Cannot resolve image host: {host}") from exc
    for address in addresses:
        ip = address[4][0]
        if is_blocked_ip_address(ip):
            raise GeminiError(GEMINI_PROXY_REFUSED, "Refusing to fetch private image target")


def fetch_image_resource(url: str, *, timeout: float, user_agent: str) -> BinaryResource:
    validate_binary_fetch_target(url)
    headers = {
        "Accept": "image/avif,image/webp,image/png,image/svg+xml,image/*;q=0.9,*/*;q=0.1",
        "User-Agent": user_agent or "markdown-ingress-gemini-proxy/1.0",
    }
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read(MAX_BINARY_BYTES + 1)
            content_type = response.headers.get_content_type()
    except urllib.error.HTTPError as exc:
        detail = read_http_error_detail(exc)
        status = GEMINI_TEMPORARY_FAILURE if 500 <= exc.code <= 599 else GEMINI_PERMANENT_FAILURE
        raise GeminiError(status, f"Image HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise GeminiError(GEMINI_PROXY_ERROR, f"Cannot fetch image: {exc.reason}") from exc
    except TimeoutError as exc:
        raise GeminiError(GEMINI_TEMPORARY_FAILURE, "Timed out fetching image") from exc
    if len(data) > MAX_BINARY_BYTES:
        raise GeminiError(GEMINI_PERMANENT_FAILURE, "Image is too large")

    guessed_type, _ = mimetypes.guess_type(urllib.parse.urlsplit(url).path)
    if content_type == "application/octet-stream" and guessed_type:
        content_type = guessed_type
    if content_type in {"text/plain", "application/xml", "text/xml"} and guessed_type:
        content_type = guessed_type
    if not content_type.startswith("image/"):
        raise GeminiError(GEMINI_PERMANENT_FAILURE, "Target did not return an image")
    return BinaryResource(content_type=content_type, data=data)


def markdown_to_gemini(
    markdown: str,
    *,
    base_url: str,
    authority: str,
    options: RequestOptions | None,
    title: str | None,
    links_payload: Any,
    max_extra_links: int,
) -> ConversionResult:
    seen_urls: set[str] = set()
    output: list[str] = []
    stripped_markdown = markdown.lstrip()
    if title and not stripped_markdown.startswith("#"):
        output.append(f"# {clean_label(title, 'Untitled')}")
        output.append("")

    in_preformatted = False
    for raw_line in markdown.splitlines():
        line = raw_line.rstrip("\r")
        if line.lstrip().startswith("```"):
            in_preformatted = not in_preformatted
            output.append(line)
            continue
        if in_preformatted:
            output.append(line)
            continue
        output.extend(
            convert_line_to_gemini(
                line,
                base_url=base_url,
                authority=authority,
                options=options,
                seen_urls=seen_urls,
            )
        )
        if not line.strip():
            output.append("")

    extra_lines: list[str] = []
    for raw_url in iter_extra_link_urls(links_payload):
        if len(extra_lines) >= max_extra_links:
            break
        link = gemini_link_for_url(raw_url, base_url, authority, options)
        if link is None:
            continue
        source_url, proxy_url = link
        if source_url in seen_urls:
            continue
        seen_urls.add(source_url)
        extra_lines.append(f"=> {proxy_url} {source_url}")

    if extra_lines:
        while output and not output[-1].strip():
            output.pop()
        output.extend(["", "## Extracted links", *extra_lines])

    body = "\n".join(output).strip() + "\n"
    return ConversionResult(body=body, seen_urls=seen_urls)


class MarkdownIngressClient:
    def __init__(self, config: ServerConfig) -> None:
        self.config = config

    def ingest(self, url: str, options: RequestOptions) -> dict[str, Any]:
        if options.target_method != "GET":
            raise GeminiError(
                GEMINI_BAD_REQUEST,
                "Target POST bodies are not supported by the current MarkDownIngress ingest API",
            )
        ingest_mode = options.ingest_mode or self.config.ingest_mode
        ingest_timeout = (
            options.ingest_timeout
            if options.ingest_timeout is not None
            else self.config.ingest_timeout
        )
        strict = options.strict if options.strict is not None else self.config.strict
        fetcher_user_agent = (
            options.fetcher_user_agent
            if options.fetcher_user_agent is not None
            else self.config.fetcher_user_agent
        )
        payload: dict[str, Any] = {
            "url": url,
            "mode": ingest_mode,
            "strict": strict,
            "timeout": ingest_timeout,
            "output_formats": ["markdown"],
            "extract_metadata": True,
            "extract_links": True,
        }
        if fetcher_user_agent:
            payload["fetcher_user_agent"] = fetcher_user_agent

        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "markdown-ingress-gemini-proxy/1.0",
        }
        if self.config.api_key:
            headers["X-API-Key"] = self.config.api_key

        request = urllib.request.Request(
            self.config.api_ingest_url,
            data=data,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.upstream_timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = read_http_error_detail(exc)
            status = GEMINI_TEMPORARY_FAILURE if 500 <= exc.code <= 599 else GEMINI_PERMANENT_FAILURE
            raise GeminiError(status, f"MarkDownIngress HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise GeminiError(
                GEMINI_PROXY_ERROR,
                f"Cannot reach MarkDownIngress API at {self.config.api_ingest_url}: {exc.reason}",
            ) from exc
        except TimeoutError as exc:
            raise GeminiError(GEMINI_TEMPORARY_FAILURE, "Timed out waiting for MarkDownIngress") from exc

        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GeminiError(GEMINI_CGI_ERROR, "MarkDownIngress returned invalid JSON") from exc
        if not isinstance(decoded, dict):
            raise GeminiError(GEMINI_CGI_ERROR, "MarkDownIngress returned an unexpected response")
        return decoded


def read_http_error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        raw = exc.read().decode("utf-8", errors="replace")
    except Exception:
        return exc.reason or "request failed"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw.strip()[:240] or (exc.reason or "request failed")
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, str):
        return detail[:240]
    return raw.strip()[:240] or (exc.reason or "request failed")


class GeminiProxy:
    def __init__(self, config: ServerConfig) -> None:
        self.config = config
        self.client = MarkdownIngressClient(config)

    def handle(self, request_url: str) -> GeminiResponse:
        LOG.info("Gemini request: %s", request_url)
        target = self.resolve_target(request_url)
        if target is None:
            return self.home_response(request_url)
        if target.options.target_method != "GET":
            raise GeminiError(
                GEMINI_BAD_REQUEST,
                "Target POST bodies are not supported by the current MarkDownIngress ingest API",
            )
        if target.options.resource_type == "image" or is_probable_image_url(target.url):
            user_agent = target.options.fetcher_user_agent or self.config.fetcher_user_agent
            image = fetch_image_resource(
                target.url,
                timeout=target.options.ingest_timeout or self.config.upstream_timeout,
                user_agent=user_agent,
            )
            return GeminiResponse(GEMINI_SUCCESS, image.content_type, image.data)
        data = self.client.ingest(target.url, target.options)
        markdown = data.get("markdown")
        if not isinstance(markdown, str):
            raise GeminiError(GEMINI_CGI_ERROR, "MarkDownIngress response did not include markdown")

        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        base_url = first_string(
            metadata.get("final_url"),
            metadata.get("url"),
            target.url,
        )
        title = first_string(metadata.get("title"), metadata.get("final_url"), target.url)
        authority = request_authority(request_url, self.config)
        conversion = markdown_to_gemini(
            markdown,
            base_url=base_url,
            authority=authority,
            options=target.options,
            title=title,
            links_payload=data.get("links"),
            max_extra_links=self.config.max_extra_links,
        )
        return GeminiResponse(GEMINI_SUCCESS, "text/gemini; charset=utf-8", conversion.body)

    def resolve_target(self, request_url: str) -> TargetRequest | None:
        if not request_url:
            raise GeminiError(GEMINI_BAD_REQUEST, "Missing Gemini URL")
        parsed = urllib.parse.urlsplit(request_url)
        if parsed.scheme.lower() != "gemini":
            raise GeminiError(GEMINI_PROXY_REFUSED, "Only gemini:// requests are accepted")

        raw_path = parsed.path or "/"
        path = urllib.parse.unquote(raw_path)
        query = urllib.parse.unquote_plus(parsed.query or "")
        if path in {"", "/"} and not query:
            return None

        if path == "/input":
            if not query:
                raise GeminiError(GEMINI_INPUT, "Enter an http(s) URL or hostname")
            return self.normalize_manual_target(query)

        if path.startswith("/fetch/"):
            encoded = path[len("/fetch/") :].strip("/")
            if not encoded:
                raise GeminiError(GEMINI_BAD_REQUEST, "Missing proxied URL")
            target = b64url_decode(encoded)
            return self.normalize_manual_target(target)

        if path == "/url":
            if not query:
                raise GeminiError(GEMINI_INPUT, "Enter an http(s) URL or hostname")
            parameters = urllib.parse.parse_qs(parsed.query)
            value = first_query_value(parameters, "url") or query
            return self.normalize_manual_target(value)

        stripped = path.lstrip("/")
        if stripped.startswith(("http://", "https://")):
            manual = stripped
            if parsed.query:
                manual += "?" + parsed.query
            return self.normalize_manual_target(manual)
        target = self.resolve_target_shaped_path(raw_path, parsed.query)
        if target is not None:
            return target
        if _HOSTISH_RE.match(stripped):
            manual = stripped
            if parsed.query and "?" not in manual:
                manual += "?" + parsed.query
            return self.normalize_manual_target(manual)

        raise GeminiError(GEMINI_BAD_REQUEST, "Use /input or /hostname/path")

    def resolve_target_shaped_path(self, raw_path: str, raw_query: str) -> TargetRequest | None:
        stripped = raw_path.lstrip("/")
        if not stripped:
            return None
        raw_host, slash, rest = stripped.partition("/")
        host = urllib.parse.unquote(raw_host)
        if not _HOSTISH_RE.match(host):
            return None
        scheme, target_query, options = split_proxy_query(raw_query)
        target_path = "/" + rest if slash else "/"
        target_url = normalize_source_url(
            urllib.parse.urlunsplit((scheme, host, target_path, target_query, ""))
        )
        if target_url is None:
            raise GeminiError(GEMINI_BAD_REQUEST, "Expected an http(s) URL or hostname")
        return TargetRequest(target_url, options)

    def normalize_manual_target(self, raw: str) -> TargetRequest:
        target = normalize_source_url(raw)
        if target is None:
            raise GeminiError(GEMINI_BAD_REQUEST, "Expected an http(s) URL or hostname")
        parsed = urllib.parse.urlsplit(target)
        scheme, target_query, options = split_proxy_query(
            parsed.query,
            default_scheme=parsed.scheme.lower(),
        )
        target = urllib.parse.urlunsplit(
            (scheme, parsed.netloc, parsed.path or "/", target_query, parsed.fragment)
        )
        return TargetRequest(target, options)

    def home_response(self, request_url: str) -> GeminiResponse:
        authority = request_authority(request_url, self.config)
        example_url = proxied_gemini_url("https://example.com/", authority)
        body = textwrap.dedent(
            f"""\
            # MarkDownIngress Gemini Proxy

            => /input Open a web URL
            => {example_url} Example Domain
            => /radare.org/con/2025 radare2con 2025

            This local Gemini server fetches HTTP and HTTPS pages through the running MarkDownIngress API at:
            {self.config.api_ingest_url}

            You can also open:
            gemini://{authority}/example.com/
            gemini://{authority}/example.com/path?query=value
            gemini://{authority}/example.com/path?query=value&__mdi_mode=render
            gemini://{authority}/example.com/path?__mdi_scheme=http

            Reserved proxy options: __mdi_scheme=http|https, __mdi_mode=auto|fast|render,
            __mdi_timeout=SECONDS, __mdi_strict=0|1. Target POST bodies are not supported
            by the current MarkDownIngress ingest API.
            """
        )
        return GeminiResponse(GEMINI_SUCCESS, "text/gemini; charset=utf-8", body)


def first_string(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return ""


def first_query_value(parameters: dict[str, list[str]], key: str) -> str | None:
    values = parameters.get(key)
    if values:
        return values[0]
    return None


class GeminiTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 64

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[socketserver.BaseRequestHandler],
        *,
        ssl_context: ssl.SSLContext,
        proxy: GeminiProxy,
    ) -> None:
        self.ssl_context = ssl_context
        self.proxy = proxy
        super().__init__(server_address, handler_class)

    def get_request(self) -> tuple[ssl.SSLSocket, Any]:
        raw_socket, client_address = self.socket.accept()
        try:
            tls_socket = self.ssl_context.wrap_socket(raw_socket, server_side=True)
        except Exception:
            raw_socket.close()
            raise
        return tls_socket, client_address


class GeminiRequestHandler(socketserver.BaseRequestHandler):
    server: GeminiTCPServer

    def handle(self) -> None:
        try:
            request_url = self.read_request_url()
            response = self.server.proxy.handle(request_url)
        except GeminiError as exc:
            response = GeminiResponse(exc.status, exc.message)
        except Exception:
            LOG.exception("Unhandled Gemini request failure")
            response = GeminiResponse(GEMINI_TEMPORARY_FAILURE, "Internal Gemini proxy error")
        try:
            self.request.sendall(response.to_bytes())
        except OSError:
            LOG.debug("Client disconnected before response could be sent", exc_info=True)

    def read_request_url(self) -> str:
        data = bytearray()
        while True:
            chunk = self.request.recv(1)
            if not chunk:
                break
            if chunk == b"\n":
                break
            data.extend(chunk)
            if len(data) > MAX_GEMINI_REQUEST_BYTES:
                raise GeminiError(GEMINI_BAD_REQUEST, "Gemini request line too long")
        if data.endswith(b"\r"):
            data = data[:-1]
        if not data:
            raise GeminiError(GEMINI_BAD_REQUEST, "Empty Gemini request")
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise GeminiError(GEMINI_BAD_REQUEST, "Gemini request must be UTF-8") from exc


def ensure_certificate(
    *,
    pem_dir: Path,
    cert_file: Path,
    key_file: Path,
    common_name: str,
    days: int,
) -> None:
    if cert_file.exists() and key_file.exists():
        return
    pem_dir.mkdir(parents=True, exist_ok=True)
    openssl = shutil.which("openssl")
    if openssl is None:
        raise RuntimeError("openssl is required to create the Gemini TLS certificate")

    LOG.info("Generating self-signed Gemini certificate in %s", pem_dir)
    base_cmd = [
        openssl,
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        str(key_file),
        "-out",
        str(cert_file),
        "-days",
        str(days),
        "-subj",
        f"/CN={common_name}",
    ]
    addext = "subjectAltName=DNS:localhost,IP:127.0.0.1,IP:::1"
    cmd_with_san = [*base_cmd, "-addext", addext]
    result = subprocess.run(cmd_with_san, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        LOG.warning("openssl -addext failed, retrying without subjectAltName")
        result = subprocess.run(base_cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"openssl failed: {result.stderr.strip() or result.stdout.strip()}")
    try:
        key_file.chmod(0o600)
    except OSError:
        LOG.debug("Could not chmod generated key file", exc_info=True)


def build_ssl_context(cert_file: Path, key_file: Path) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=str(cert_file), keyfile=str(key_file))
    return context


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Serve MarkDownIngress output to Gemini clients such as Lagrange.",
    )
    parser.add_argument("--host", default=DEFAULT_LISTEN_HOST, help="Local address to bind")
    parser.add_argument("--port", type=int, default=DEFAULT_GEMINI_PORT, help="Gemini TLS port")
    parser.add_argument(
        "--public-host",
        default=None,
        help="Host name used when constructing Gemini links. Defaults to --host.",
    )
    parser.add_argument(
        "--public-port",
        type=int,
        default=None,
        help="Port used when constructing Gemini links. Defaults to --port.",
    )
    parser.add_argument(
        "--pem-dir",
        default=DEFAULT_PEM_DIR,
        help="Directory containing or receiving the Gemini cert/key PEM files.",
    )
    parser.add_argument("--cert-file", default=None, help="Explicit TLS certificate PEM path")
    parser.add_argument("--key-file", default=None, help="Explicit TLS private key PEM path")
    parser.add_argument("--cert-days", type=int, default=3650, help="Generated cert lifetime")
    parser.add_argument(
        "--cert-common-name",
        default="localhost",
        help="Common Name used for generated self-signed certificates.",
    )
    parser.add_argument(
        "--api-url",
        default=os.environ.get("MDI_API_URL", DEFAULT_API_URL),
        help="MarkDownIngress API root or /api/v1/ingest URL.",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("MDI_API_KEY"),
        help="Optional MarkDownIngress X-API-Key. Defaults to MDI_API_KEY.",
    )
    parser.add_argument(
        "--mode",
        default="auto",
        choices=("fast", "render", "auto"),
        help="MarkDownIngress ingest mode.",
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="Per-page ingest timeout")
    parser.add_argument(
        "--upstream-timeout",
        type=float,
        default=75.0,
        help="HTTP timeout for the local MarkDownIngress API call.",
    )
    parser.add_argument(
        "--no-strict",
        action="store_true",
        help="Disable strict MarkDownIngress security handling.",
    )
    parser.add_argument(
        "--fetcher-user-agent",
        default="",
        help="Optional user agent passed to MarkDownIngress fetching.",
    )
    parser.add_argument(
        "--max-extra-links",
        type=int,
        default=100,
        help="Maximum extracted links appended after converted content.",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable request logging")
    return parser.parse_args(argv)


def run(argv: list[str]) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s: %(message)s",
    )
    pem_dir = Path(args.pem_dir)
    cert_file = Path(args.cert_file) if args.cert_file else pem_dir / DEFAULT_CERT_NAME
    key_file = Path(args.key_file) if args.key_file else pem_dir / DEFAULT_KEY_NAME
    try:
        ensure_certificate(
            pem_dir=pem_dir,
            cert_file=cert_file,
            key_file=key_file,
            common_name=args.cert_common_name,
            days=args.cert_days,
        )
        ssl_context = build_ssl_context(cert_file, key_file)
        api_ingest_url = normalize_api_ingest_url(args.api_url)
    except Exception as exc:
        print(f"gemini-server.py: {exc}", file=sys.stderr)
        return 2

    public_host = args.public_host or args.host
    public_port = args.public_port if args.public_port is not None else args.port
    config = ServerConfig(
        listen_host=args.host,
        listen_port=args.port,
        public_host=public_host,
        public_port=public_port,
        api_ingest_url=api_ingest_url,
        api_key=args.api_key,
        ingest_mode=args.mode,
        ingest_timeout=args.timeout,
        upstream_timeout=args.upstream_timeout,
        strict=not args.no_strict,
        fetcher_user_agent=args.fetcher_user_agent,
        max_extra_links=max(0, args.max_extra_links),
    )
    proxy = GeminiProxy(config)
    address = (config.listen_host, config.listen_port)
    try:
        with GeminiTCPServer(
            address,
            GeminiRequestHandler,
            ssl_context=ssl_context,
            proxy=proxy,
        ) as server:
            print(
                f"Gemini proxy listening on gemini://{config.public_host}:{config.public_port}/ "
                f"(MarkDownIngress: {config.api_ingest_url})"
            )
            server.serve_forever()
    except KeyboardInterrupt:
        print("\nGemini proxy stopped.")
        return 0
    except OSError as exc:
        print(f"gemini-server.py: cannot bind {args.host}:{args.port}: {exc}", file=sys.stderr)
        return 2


def main() -> None:
    raise SystemExit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
