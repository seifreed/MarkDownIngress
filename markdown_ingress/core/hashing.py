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
        Generate structural hash based on headings and key content.
        Useful for detecting content changes while ignoring minor edits.
        
        Args:
            markdown: Markdown content
            
        Returns:
            Structural hash string
        """
        import re
        
        # Extract headings
        headings = re.findall(r'^#{1,6}\s+(.+)$', markdown, re.MULTILINE)
        
        # Extract first sentence of each paragraph (simplified)
        paragraphs = [p.strip() for p in markdown.split('\n\n') if p.strip() and not p.strip().startswith('#')]
        first_sentences = []
        
        for para in paragraphs[:10]:  # Limit to first 10 paragraphs
            # Get first sentence (simplified - split on . ! ?)
            match = re.match(r'^([^.!?]+[.!?])', para)
            if match:
                first_sentences.append(match.group(1))
        
        # Combine into structural representation
        structural_content = '\n'.join(headings + first_sentences)
        
        return self.hash_content(structural_content)
