"""
Content hashing module for deterministic fingerprinting
"""

import hashlib


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
        import re

        lines = markdown.split("\n")
        structural_elements = []

        # Extract heading hierarchy with levels
        for line in lines:
            heading_match = re.match(r"^(#{1,6})\s+(.+)$", line)
            if heading_match:
                level = len(heading_match.group(1))
                title = heading_match.group(2).strip()
                # Normalize: lowercase, remove punctuation for stability
                normalized_title = re.sub(r"[^\w\s]", "", title.lower())
                structural_elements.append(f"H{level}:{normalized_title}")

        # Extract list structure (but not content)
        in_list = False
        list_depth = 0
        for line in lines:
            list_match = re.match(r"^(\s*)[-*+]\s", line)
            if list_match:
                # Normalize list indentation into coarse 4-space buckets so
                # superficial whitespace differences do not change structure.
                depth = len(list_match.group(1).expandtabs(4)) // 4
                if not in_list or depth != list_depth:
                    structural_elements.append(f"LIST:{depth}")
                    in_list = True
                    list_depth = depth
            elif line.strip() and in_list:
                in_list = False

        # Extract code block presence (not content)
        # Track open/close state to handle unbalanced fences correctly.
        # Match both backtick and tilde fences per CommonMark spec.
        code_block_count = 0
        open_fence_len = 0
        open_fence_char = ""
        for fence_match in re.finditer(r"^(`{3,}|~{3,})", markdown, re.MULTILINE):
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
        # Do NOT count unclosed fences — CommonMark treats them as literal text, not code blocks.
        if code_block_count > 0:
            structural_elements.append(f"CODE_BLOCKS:{code_block_count}")

        # Extract link count (structure indicator)
        links = len(re.findall(r"(?<!\!)\[([^\]]+)\]\(([^()]*(?:\([^()]*\))*[^()]*)\)", markdown))
        if links > 0:
            structural_elements.append(f"LINKS:{links}")

        # Combine into structural fingerprint
        structural_content = "\n".join(structural_elements)

        # If no structure found, fall back to content hash
        if not structural_content:
            structural_content = markdown[:200]

        return self.hash_content(structural_content)
