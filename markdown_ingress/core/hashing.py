"""
Content hashing module for deterministic fingerprinting
"""

import hashlib
import re

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_LIST_RE = re.compile(r"^(\s*)[-*+]\s")
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})", re.MULTILINE)
# Possessive quantifiers (*+) prevent quadratic backtracking on adversarial
# unclosed-paren input; the `)` terminator is excluded from `[^()]`, so they
# never over-consume and the match results are identical to greedy quantifiers.
_LINK_RE = re.compile(r"(?<!\!)\[([^\]]+)\]\(([^()]*+(?:\([^()]*\))*+[^()]*+)\)")


def _heading_structure(lines: list[str]) -> list[str]:
    structural_elements = []
    for line in lines:
        heading_match = _HEADING_RE.match(line)
        if heading_match:
            level = len(heading_match.group(1))
            title = heading_match.group(2).strip()
            normalized_title = re.sub(r"[^\w\s]", "", title.lower())
            structural_elements.append(f"H{level}:{normalized_title}")
    return structural_elements


def _list_structure(lines: list[str]) -> list[str]:
    structural_elements = []
    in_list = False
    list_depth = 0
    for line in lines:
        list_match = _LIST_RE.match(line)
        if list_match:
            depth = len(list_match.group(1).expandtabs(4)) // 4
            if not in_list or depth != list_depth:
                structural_elements.append(f"LIST:{depth}")
                in_list = True
                list_depth = depth
        elif line.strip() and in_list:
            in_list = False
    return structural_elements


def _count_closed_code_blocks(markdown: str) -> int:
    code_block_count = 0
    open_fence_len = 0
    open_fence_char = ""
    for fence_match in _FENCE_RE.finditer(markdown):
        fence_str = fence_match.group(1)
        fence_char = fence_str[0]
        fence_len = len(fence_str)
        if open_fence_len == 0:
            open_fence_len = fence_len
            open_fence_char = fence_char
        elif fence_char == open_fence_char and fence_len >= open_fence_len:
            code_block_count += 1
            open_fence_len = 0
            open_fence_char = ""
    return code_block_count


def _code_block_structure(markdown: str) -> list[str]:
    code_block_count = _count_closed_code_blocks(markdown)
    if code_block_count > 0:
        return [f"CODE_BLOCKS:{code_block_count}"]
    return []


def _link_structure(markdown: str) -> list[str]:
    links = len(_LINK_RE.findall(markdown))
    if links > 0:
        return [f"LINKS:{links}"]
    return []


def _build_structural_content(markdown: str) -> str:
    lines = markdown.split("\n")
    structural_elements = (
        _heading_structure(lines)
        + _list_structure(lines)
        + _code_block_structure(markdown)
        + _link_structure(markdown)
    )
    return "\n".join(structural_elements) or markdown[:200]


class Hasher:
    """Generate deterministic content hashes"""

    def __init__(self, algorithm: str = "sha256"):
        """
        Initialize hasher.

        Args:
            algorithm: Hash algorithm to use (default: sha256)
        """
        self.algorithm = algorithm

    def hash_content(self, content: str) -> str:
        """
        Generate deterministic hash of content.

        Args:
            content: Text content to hash

        Returns:
            Hex digest string with algorithm prefix (e.g., 'sha256:abc123...')
        """
        hasher = hashlib.new(self.algorithm)
        hasher.update(content.encode("utf-8"))
        digest = hasher.hexdigest()

        return f"{self.algorithm}:{digest}"

    def hash_structural(self, markdown: str) -> str:
        """
        Generate structural hash based on headings hierarchy and key content.
        Useful for detecting structural changes while ignoring minor text edits.

        Same document structure (headings + outline) will produce same hash
        even if paragraph content changes.

        Args:
            markdown: Markdown content

        Returns:
            Structural hash string with 'sha256:' prefix
        """
        return self.hash_content(_build_structural_content(markdown))
