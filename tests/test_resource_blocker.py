"""
Tests for resource blocker functionality.
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, MagicMock
from markdown_ingress.core.resource_blocker import ResourceBlocker, BLOCKED_DOMAINS


class TestResourceBlocker:
    """Test suite for ResourceBlocker class"""
    
    def test_init_default_settings(self):
        """Test initialization with default settings"""
        blocker = ResourceBlocker()
        
        assert blocker.block_images is True
        assert blocker.block_fonts is True
        assert blocker.block_media is True
        assert blocker.block_css is False
        assert blocker.block_ads is True
        assert blocker.block_trackers is True
        assert blocker.blocked_count == 0
        assert blocker.total_count == 0
    
    def test_init_custom_settings(self):
        """Test initialization with custom settings"""
        blocker = ResourceBlocker(
            block_images=False,
            block_fonts=False,
            block_media=True,
            block_css=True,
            block_ads=False,
            block_trackers=False
        )
        
        assert blocker.block_images is False
        assert blocker.block_fonts is False
        assert blocker.block_media is True
        assert blocker.block_css is True
        assert blocker.block_ads is False
        assert blocker.block_trackers is False
    
    def test_custom_blocked_domains(self):
        """Test adding custom blocked domains"""
        custom_domains = ['custom-tracker.com', 'another-ad.net']
        blocker = ResourceBlocker(custom_blocked_domains=custom_domains)
        
        assert 'custom-tracker.com' in blocker.blocked_domains
        assert 'another-ad.net' in blocker.blocked_domains
        # Default domains should still be present
        assert 'google-analytics.com' in blocker.blocked_domains
    
    def test_should_block_images(self):
        """Test blocking image resources"""
        blocker = ResourceBlocker(block_images=True)
        
        assert blocker._should_block('image', 'https://example.com/photo.jpg') is True
        assert blocker._should_block('image', 'https://example.com/logo.png') is True
    
    def test_should_not_block_images_when_disabled(self):
        """Test not blocking images when disabled"""
        blocker = ResourceBlocker(block_images=False, block_ads=False, block_trackers=False)
        
        assert blocker._should_block('image', 'https://example.com/photo.jpg') is False
    
    def test_should_block_fonts(self):
        """Test blocking font resources"""
        blocker = ResourceBlocker(block_fonts=True)
        
        assert blocker._should_block('font', 'https://example.com/font.woff2') is True
    
    def test_should_block_media(self):
        """Test blocking media resources"""
        blocker = ResourceBlocker(block_media=True)
        
        assert blocker._should_block('media', 'https://example.com/video.mp4') is True
    
    def test_should_block_css(self):
        """Test blocking CSS when enabled"""
        blocker = ResourceBlocker(block_css=True)
        
        assert blocker._should_block('stylesheet', 'https://example.com/style.css') is True
    
    def test_should_not_block_css_by_default(self):
        """Test not blocking CSS by default"""
        blocker = ResourceBlocker(block_ads=False, block_trackers=False)
        
        assert blocker._should_block('stylesheet', 'https://example.com/style.css') is False
    
    def test_should_block_google_analytics(self):
        """Test blocking Google Analytics"""
        blocker = ResourceBlocker(block_trackers=True)
        
        assert blocker._should_block('script', 'https://www.google-analytics.com/analytics.js') is True
        assert blocker._should_block('script', 'https://www.googletagmanager.com/gtm.js') is True
    
    def test_should_block_ads_domain(self):
        """Test blocking ad domains"""
        blocker = ResourceBlocker(block_ads=True)
        
        assert blocker._should_block('script', 'https://doubleclick.net/ads.js') is True
        assert blocker._should_block('script', 'https://ads.example.com/banner.js') is True
    
    def test_should_block_tracker_patterns(self):
        """Test blocking tracking patterns in URLs"""
        blocker = ResourceBlocker(block_trackers=True)
        
        assert blocker._should_block('script', 'https://example.com/tracking.js') is True
        assert blocker._should_block('script', 'https://example.com/pixel.gif') is True
        assert blocker._should_block('script', 'https://analytics.example.com/track.js') is True
    
    def test_should_not_block_regular_resources(self):
        """Test not blocking regular resources"""
        blocker = ResourceBlocker(
            block_images=False,
            block_fonts=False,
            block_media=False,
            block_ads=False,
            block_trackers=False
        )
        
        assert blocker._should_block('script', 'https://example.com/main.js') is False
        assert blocker._should_block('document', 'https://example.com/page.html') is False
        assert blocker._should_block('xhr', 'https://api.example.com/data') is False
    
    def test_case_insensitive_domain_matching(self):
        """Test that domain matching is case insensitive"""
        blocker = ResourceBlocker(block_trackers=True)
        
        assert blocker._should_block('script', 'https://GOOGLE-ANALYTICS.COM/analytics.js') is True
        assert blocker._should_block('script', 'https://Example.com/TRACKING.js') is True
    
    @pytest.mark.asyncio
    async def test_setup_blocking(self):
        """Test setting up request interception on a page"""
        blocker = ResourceBlocker()
        mock_page = AsyncMock()
        
        await blocker.setup_blocking(mock_page)
        
        # Verify route was set up
        mock_page.route.assert_called_once_with("**/*", blocker._handle_route)
    
    @pytest.mark.asyncio
    async def test_handle_route_blocks_image(self):
        """Test route handler blocks image requests"""
        blocker = ResourceBlocker(block_images=True)
        
        # Mock route and request
        mock_route = AsyncMock()
        mock_request = Mock()
        mock_request.resource_type = 'image'
        mock_request.url = 'https://example.com/photo.jpg'
        mock_route.request = mock_request
        
        await blocker._handle_route(mock_route)
        
        # Should abort the request
        mock_route.abort.assert_called_once()
        mock_route.continue_.assert_not_called()
        assert blocker.blocked_count == 1
        assert blocker.total_count == 1
    
    @pytest.mark.asyncio
    async def test_handle_route_allows_script(self):
        """Test route handler allows script requests"""
        blocker = ResourceBlocker(
            block_images=False,
            block_ads=False,
            block_trackers=False
        )
        
        # Mock route and request
        mock_route = AsyncMock()
        mock_request = Mock()
        mock_request.resource_type = 'script'
        mock_request.url = 'https://example.com/main.js'
        mock_route.request = mock_request
        
        await blocker._handle_route(mock_route)
        
        # Should continue the request
        mock_route.continue_.assert_called_once()
        mock_route.abort.assert_not_called()
        assert blocker.blocked_count == 0
        assert blocker.total_count == 1
    
    @pytest.mark.asyncio
    async def test_handle_route_blocks_tracker(self):
        """Test route handler blocks tracking scripts"""
        blocker = ResourceBlocker(block_trackers=True)
        
        # Mock route and request
        mock_route = AsyncMock()
        mock_request = Mock()
        mock_request.resource_type = 'script'
        mock_request.url = 'https://google-analytics.com/analytics.js'
        mock_route.request = mock_request
        
        await blocker._handle_route(mock_route)
        
        # Should abort the request
        mock_route.abort.assert_called_once()
        assert blocker.blocked_count == 1
    
    @pytest.mark.asyncio
    async def test_handle_route_error_handling(self):
        """Test route handler handles errors gracefully"""
        blocker = ResourceBlocker()
        
        # Mock route that raises an error on request access
        mock_route = AsyncMock()
        mock_route.request = Mock()
        mock_route.request.resource_type = Mock(side_effect=Exception("Test error"))
        
        # Should not raise exception
        await blocker._handle_route(mock_route)
        
        # Should attempt to continue despite error
        mock_route.continue_.assert_called_once()
    
    def test_get_stats_empty(self):
        """Test getting stats when no requests processed"""
        blocker = ResourceBlocker()
        stats = blocker.get_stats()
        
        assert stats['blocked_requests'] == 0
        assert stats['total_requests'] == 0
        assert stats['allowed_requests'] == 0
        assert stats['block_rate_pct'] == 0
        assert stats['blocked_by_type'] == {}
        assert stats['blocked_by_domain'] == {}
    
    def test_get_stats_with_blocking(self):
        """Test getting stats after blocking requests"""
        blocker = ResourceBlocker(block_images=True)
        
        # Simulate blocked requests
        blocker.total_count = 10
        blocker.blocked_count = 7
        blocker.blocked_by_type = {'image': 5, 'font': 2}
        blocker.blocked_by_domain = {'google-analytics.com': 1}
        
        stats = blocker.get_stats()
        
        assert stats['blocked_requests'] == 7
        assert stats['total_requests'] == 10
        assert stats['allowed_requests'] == 3
        assert stats['block_rate_pct'] == 70.0
        assert stats['blocked_by_type'] == {'image': 5, 'font': 2}
        assert stats['blocked_by_domain'] == {'google-analytics.com': 1}
    
    def test_reset_stats(self):
        """Test resetting statistics"""
        blocker = ResourceBlocker()
        
        # Set some stats
        blocker.total_count = 10
        blocker.blocked_count = 7
        blocker.blocked_by_type = {'image': 5}
        blocker.blocked_by_domain = {'ads': 2}
        
        # Reset
        blocker.reset_stats()
        
        assert blocker.total_count == 0
        assert blocker.blocked_count == 0
        assert blocker.blocked_by_type == {}
        assert blocker.blocked_by_domain == {}
    
    def test_statistics_tracking(self):
        """Test that statistics are tracked correctly during blocking"""
        blocker = ResourceBlocker(block_images=True, block_fonts=True)
        
        # Simulate blocking different types
        blocker._should_block('image', 'https://example.com/1.jpg')
        blocker.total_count += 1
        blocker.blocked_count += 1
        blocker.blocked_by_type['image'] = blocker.blocked_by_type.get('image', 0) + 1
        
        blocker._should_block('font', 'https://example.com/font.woff')
        blocker.total_count += 1
        blocker.blocked_count += 1
        blocker.blocked_by_type['font'] = blocker.blocked_by_type.get('font', 0) + 1
        
        blocker._should_block('script', 'https://example.com/script.js')
        blocker.total_count += 1
        
        assert blocker.total_count == 3
        assert blocker.blocked_count == 2
        assert blocker.blocked_by_type == {'image': 1, 'font': 1}


class TestBlockedDomains:
    """Test the BLOCKED_DOMAINS constant"""
    
    def test_blocked_domains_include_analytics(self):
        """Test that common analytics domains are included"""
        assert 'google-analytics.com' in BLOCKED_DOMAINS
        assert 'googletagmanager.com' in BLOCKED_DOMAINS
        assert 'analytics' in BLOCKED_DOMAINS
    
    def test_blocked_domains_include_ads(self):
        """Test that common ad domains are included"""
        assert 'doubleclick.net' in BLOCKED_DOMAINS
        assert 'googlesyndication.com' in BLOCKED_DOMAINS
        assert 'ads' in BLOCKED_DOMAINS
    
    def test_blocked_domains_include_trackers(self):
        """Test that common tracking domains are included"""
        assert 'tracking' in BLOCKED_DOMAINS
        assert 'tracker' in BLOCKED_DOMAINS
        assert 'pixel' in BLOCKED_DOMAINS
        assert 'beacon' in BLOCKED_DOMAINS
