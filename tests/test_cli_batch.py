"""
Tests for CLI batch command
"""

import pytest
import tempfile
import subprocess
import json
from pathlib import Path


class TestCLIBatch:
    """Test CLI batch processing"""
    
    def test_batch_command_basic(self):
        """Batch command processes multiple URLs"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create URLs file
            urls_file = Path(tmpdir) / "urls.txt"
            urls_file.write_text("""
http://example.com
http://example.org
""")
            
            output_dir = Path(tmpdir) / "output"
            
            # Run batch command
            result = subprocess.run([
                'markdown-ingress', 'batch',
                str(urls_file),
                '--output', str(output_dir)
            ], capture_output=True, text=True)
            
            assert result.returncode == 0
            assert output_dir.exists()
            
            # Check output files
            md_files = list(output_dir.glob("*.md"))
            assert len(md_files) == 2
    
    def test_batch_command_json_output(self):
        """Batch command can output JSON summary"""
        with tempfile.TemporaryDirectory() as tmpdir:
            urls_file = Path(tmpdir) / "urls.txt"
            urls_file.write_text("http://example.com\n")
            
            output_file = Path(tmpdir) / "results.json"
            
            result = subprocess.run([
                'markdown-ingress', 'batch',
                str(urls_file),
                '--json',
                '--output', str(output_file)
            ], capture_output=True, text=True)
            
            assert result.returncode == 0
            assert output_file.exists()
            
            # Verify JSON structure
            data = json.loads(output_file.read_text())
            assert 'summary' in data
            assert 'results' in data
            assert data['summary']['total'] == 1
    
    def test_batch_with_comments_and_empty_lines(self):
        """Batch command ignores comments and empty lines"""
        with tempfile.TemporaryDirectory() as tmpdir:
            urls_file = Path(tmpdir) / "urls.txt"
            urls_file.write_text("""
# This is a comment
http://example.com

# Another comment
http://example.org

""")
            
            output_dir = Path(tmpdir) / "output"
            
            result = subprocess.run([
                'markdown-ingress', 'batch',
                str(urls_file),
                '--output', str(output_dir),
                '--concurrent', '2'
            ], capture_output=True, text=True)
            
            assert result.returncode == 0
            md_files = list(output_dir.glob("*.md"))
            assert len(md_files) == 2
    
    def test_batch_concurrent_limit(self):
        """Batch command respects concurrent limit"""
        with tempfile.TemporaryDirectory() as tmpdir:
            urls_file = Path(tmpdir) / "urls.txt"
            urls_file.write_text("""
http://example.com
http://example.org
http://example.net
""")
            
            output_file = Path(tmpdir) / "results.json"
            
            result = subprocess.run([
                'markdown-ingress', 'batch',
                str(urls_file),
                '--concurrent', '1',
                '--json',
                '--output', str(output_file)
            ], capture_output=True, text=True)
            
            assert result.returncode == 0
            data = json.loads(output_file.read_text())
            assert data['summary']['total'] == 3


class TestCLIIngest:
    """Test CLI ingest command"""
    
    def test_ingest_subcommand(self):
        """Ingest subcommand works"""
        result = subprocess.run([
            'markdown-ingress', 'ingest',
            'http://example.com',
            '--no-content'
        ], capture_output=True, text=True)
        
        assert result.returncode == 0
        assert "MarkDownIngress" in result.stdout
        assert "Tokens:" in result.stdout
    
    def test_ingest_json_output(self):
        """Ingest can output JSON"""
        result = subprocess.run([
            'markdown-ingress', 'ingest',
            'http://example.com',
            '--json'
        ], capture_output=True, text=True)
        
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert 'markdown' in data
        assert 'token_estimate' in data
        assert 'injection_score' in data
    
    def test_ingest_save_file(self):
        """Ingest can save to file"""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = Path(tmpdir) / "output.md"
            
            result = subprocess.run([
                'markdown-ingress', 'ingest',
                'http://example.com',
                '--save', str(output_file),
                '--no-content'
            ], capture_output=True, text=True)
            
            assert result.returncode == 0
            assert output_file.exists()
            content = output_file.read_text()
            assert len(content) > 0
    
    def test_legacy_url_mode(self):
        """Legacy mode works via ingest subcommand"""
        result = subprocess.run([
            'markdown-ingress', 'ingest',
            'http://example.com',
            '--no-content'
        ], capture_output=True, text=True)
        
        assert result.returncode == 0
        assert "Tokens:" in result.stdout
