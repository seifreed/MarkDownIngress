"""
Content hashing module for deterministic fingerprinting
"""

import hashlib


class Hasher:
    """Generate deterministic content hashes"""
    
    def __init__(self, algorithm: str = 'sha256'):
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
        hasher.update(content.encode('utf-8'))
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
        
        lines = markdown.split('\n')
        structural_elements = []
        
        # Extract heading hierarchy with levels
        for line in lines:
            heading_match = re.match(r'^(#{1,6})\s+(.+)$', line)
            if heading_match:
                level = len(heading_match.group(1))
                title = heading_match.group(2).strip()
                # Normalize: lowercase, remove punctuation for stability
                normalized_title = re.sub(r'[^\w\s]', '', title.lower())
                structural_elements.append(f"H{level}:{normalized_title}")
        
        # Extract list structure (but not content)
        in_list = False
        list_depth = 0
        for line in lines:
            list_match = re.match(r'^(\s*)[-*+]\s', line)
            if list_match:
                depth = len(list_match.group(1)) // 2
                if not in_list or depth != list_depth:
                    structural_elements.append(f"LIST:{depth}")
                    in_list = True
                    list_depth = depth
            elif line.strip() and in_list:
                in_list = False
        
        # Extract code block presence (not content)
        code_blocks = len(re.findall(r'^```', markdown, re.MULTILINE))
        if code_blocks > 0:
            structural_elements.append(f"CODE_BLOCKS:{code_blocks // 2}")
        
        # Extract link count (structure indicator)
        links = len(re.findall(r'\[([^\]]+)\]\(([^)]+)\)', markdown))
        if links > 0:
            structural_elements.append(f"LINKS:{links}")
        
        # Combine into structural fingerprint
        structural_content = '\n'.join(structural_elements)
        
        # If no structure found, fall back to content hash
        if not structural_content:
            structural_content = markdown[:200]  # First 200 chars
        
        return self.hash_content(structural_content)
