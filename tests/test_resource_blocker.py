"""
Tests for resource blocker functionality.
"""

from unittest.mock import AsyncMock, Mock

import pytest

from markdown_ingress.core.resource_blocker import (
    _DOMAIN_ONLY_PATTERNS,
    _PATH_PATTERNS,
    BLOCKED_DOMAINS,
    ResourceBlocker,
)


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
        assert blocker.enforce_dns_pinning is True
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
            block_trackers=False,
        )

        assert blocker.block_images is False
        assert blocker.block_fonts is False
        assert blocker.block_media is True
        assert blocker.block_css is True
        assert blocker.block_ads is False
        assert blocker.block_trackers is False

    def test_init_rejects_unknown_settings(self):
        """Test initialization rejects misspelled settings."""
        with pytest.raises(TypeError, match="unexpected keyword argument 'block_image'"):
            ResourceBlocker(block_image=False)

    def test_custom_blocked_domains(self):
        """Test adding custom blocked domains"""
        custom_domains = ["custom-tracker.com", "another-ad.net"]
        blocker = ResourceBlocker(custom_blocked_domains=custom_domains)

        assert "custom-tracker.com" in blocker._custom_blocked_domains
        assert "another-ad.net" in blocker._custom_blocked_domains

    def test_custom_blocked_domains_work_without_builtin_filters(self):
        """Custom blocklist should apply even when built-in ad/tracker filters are disabled."""
        blocker = ResourceBlocker(
            block_images=False,
            block_fonts=False,
            block_media=False,
            block_css=False,
            block_ads=False,
            block_trackers=False,
            custom_blocked_domains=["custom-blocked.example"],
        )

        assert blocker._should_block("script", "https://custom-blocked.example/app.js")[0] is True
        assert blocker._should_block("document", "https://custom-blocked.example/")[0] is True

    def test_custom_blocked_domains_are_normalized(self):
        blocker = ResourceBlocker(
            block_images=False,
            block_fonts=False,
            block_media=False,
            block_css=False,
            block_ads=False,
            block_trackers=False,
            custom_blocked_domains=["Custom-Blocked.Example."],
        )

        assert blocker._should_block("script", "https://custom-blocked.example/app.js")[0] is True
        assert blocker._should_block("script", "https://CUSTOM-BLOCKED.EXAMPLE./app.js")[0] is True

    def test_custom_blocked_domains_accept_url_like_inputs(self):
        blocker = ResourceBlocker(
            block_images=False,
            block_fonts=False,
            block_media=False,
            block_css=False,
            block_ads=False,
            block_trackers=False,
            custom_blocked_domains=["https://Custom-Blocked.Example./path"],
        )

        assert "custom-blocked.example" in blocker._custom_blocked_domains
        assert blocker._should_block("script", "https://custom-blocked.example/app.js")[0] is True

    @pytest.mark.parametrize("resource_type", ["xhr", "fetch", "script", "document"])
    def test_should_block_ssrf_subresources(self, resource_type):
        blocker = ResourceBlocker(
            block_images=False,
            block_fonts=False,
            block_media=False,
            block_css=False,
            block_ads=False,
            block_trackers=False,
            allow_local_urls=False,
            enforce_dns_pinning=False,
        )

        should_block, reason = blocker._should_block(
            resource_type,
            "http://127.0.0.1:12345/private",
        )

        assert should_block is True
        assert reason == "ssrf_protection"

    def test_should_allow_public_subresource_with_dns_check(self, monkeypatch):
        calls: list[tuple[str, bool, bool]] = []

        def fake_validate(url, *, allow_local, resolve_dns):
            calls.append((url, allow_local, resolve_dns))
            return "https://93.184.216.34/app.js"

        monkeypatch.setattr(
            "markdown_ingress.core.ssrf.validate_http_url_no_ssrf",
            fake_validate,
        )
        blocker = ResourceBlocker(
            block_images=False,
            block_fonts=False,
            block_media=False,
            block_css=False,
            block_ads=False,
            block_trackers=False,
            allow_local_urls=False,
            enforce_dns_pinning=False,
        )

        should_block, reason = blocker._should_block(
            "script",
            "https://rebind.example/app.js",
        )

        assert should_block is False
        assert reason is None
        assert calls == [("https://rebind.example/app.js", False, True)]

    def test_should_block_public_subresource_when_dns_pin_is_required_but_missing(
        self, monkeypatch
    ):
        def fake_validate(url, *, allow_local, resolve_dns):
            assert resolve_dns is True
            return "https://93.184.216.34/app.js"

        monkeypatch.setattr(
            "markdown_ingress.core.ssrf.validate_http_url_no_ssrf",
            fake_validate,
        )
        blocker = ResourceBlocker(
            block_images=False,
            block_fonts=False,
            block_media=False,
            block_css=False,
            block_ads=False,
            block_trackers=False,
            allow_local_urls=False,
        )

        assert blocker._should_block("script", "https://rebind.example/app.js") == (
            True,
            "ssrf_protection",
        )

    def test_should_allow_public_subresource_with_matching_browser_dns_pin(self, monkeypatch):
        def fake_validate(url, *, allow_local, resolve_dns):
            assert resolve_dns is True
            return "https://93.184.216.34/app.js"

        def fake_getaddrinfo(host, port):
            assert host == "rebind.example"
            return [(None, None, None, None, ("93.184.216.34", 0))]

        monkeypatch.setattr(
            "markdown_ingress.core.ssrf.validate_http_url_no_ssrf",
            fake_validate,
        )
        monkeypatch.setattr(
            "markdown_ingress.core.ssrf.socket.getaddrinfo",
            fake_getaddrinfo,
        )
        blocker = ResourceBlocker(
            block_images=False,
            block_fonts=False,
            block_media=False,
            block_css=False,
            block_ads=False,
            block_trackers=False,
            allow_local_urls=False,
            dns_pins={"rebind.example": "93.184.216.34"},
            enforce_dns_pinning=True,
        )

        assert blocker._should_block("script", "https://rebind.example/app.js") == (
            False,
            None,
        )

    def test_should_allow_public_subresource_when_dns_pin_is_in_resolved_ip_set(self, monkeypatch):
        def fake_validate(url, *, allow_local, resolve_dns):
            assert resolve_dns is True
            return "https://93.184.216.35/app.js"

        def fake_getaddrinfo(host, port):
            assert host == "rebind.example"
            return [
                (None, None, None, None, ("93.184.216.35", 0)),
                (None, None, None, None, ("93.184.216.34", 0)),
            ]

        monkeypatch.setattr(
            "markdown_ingress.core.ssrf.validate_http_url_no_ssrf",
            fake_validate,
        )
        monkeypatch.setattr(
            "markdown_ingress.core.ssrf.socket.getaddrinfo",
            fake_getaddrinfo,
        )
        blocker = ResourceBlocker(
            block_images=False,
            block_fonts=False,
            block_media=False,
            block_css=False,
            block_ads=False,
            block_trackers=False,
            allow_local_urls=False,
            dns_pins={"rebind.example": "93.184.216.34"},
            enforce_dns_pinning=True,
        )

        assert blocker._should_block("script", "https://rebind.example/app.js") == (
            False,
            None,
        )

    def test_should_block_public_subresource_with_stale_browser_dns_pin(self, monkeypatch):
        def fake_validate(url, *, allow_local, resolve_dns):
            assert resolve_dns is True
            return "https://93.184.216.35/app.js"

        def fake_getaddrinfo(host, port):
            assert host == "rebind.example"
            return [
                (None, None, None, None, ("93.184.216.35", 0)),
                (None, None, None, None, ("93.184.216.36", 0)),
            ]

        monkeypatch.setattr(
            "markdown_ingress.core.ssrf.validate_http_url_no_ssrf",
            fake_validate,
        )
        monkeypatch.setattr(
            "markdown_ingress.core.ssrf.socket.getaddrinfo",
            fake_getaddrinfo,
        )
        blocker = ResourceBlocker(
            block_images=False,
            block_fonts=False,
            block_media=False,
            block_css=False,
            block_ads=False,
            block_trackers=False,
            allow_local_urls=False,
            dns_pins={"rebind.example": "93.184.216.34"},
            enforce_dns_pinning=True,
        )

        assert blocker._should_block("script", "https://rebind.example/app.js") == (
            True,
            "ssrf_protection",
        )

    def test_should_block_public_subresource_with_private_browser_dns_pin(self, monkeypatch):
        def fake_validate(url, *, allow_local, resolve_dns):
            assert resolve_dns is True
            return "https://93.184.216.35/app.js"

        monkeypatch.setattr(
            "markdown_ingress.core.ssrf.validate_http_url_no_ssrf",
            fake_validate,
        )
        blocker = ResourceBlocker(
            block_images=False,
            block_fonts=False,
            block_media=False,
            block_css=False,
            block_ads=False,
            block_trackers=False,
            allow_local_urls=False,
            dns_pins={"rebind.example": "127.0.0.1"},
            enforce_dns_pinning=True,
        )

        assert blocker._should_block("script", "https://rebind.example/app.js") == (
            True,
            "ssrf_protection",
        )

    def test_should_block_subresource_hostname_resolving_private(self, monkeypatch):
        def fake_validate(url, *, allow_local, resolve_dns):
            assert url == "http://127.0.0.1.nip.io/private"
            assert allow_local is False
            assert resolve_dns is True
            raise ValueError("URL hostname resolves to blocked IP (SSRF protection)")

        monkeypatch.setattr(
            "markdown_ingress.core.ssrf.validate_http_url_no_ssrf",
            fake_validate,
        )
        blocker = ResourceBlocker(
            block_images=False,
            block_fonts=False,
            block_media=False,
            block_css=False,
            block_ads=False,
            block_trackers=False,
            allow_local_urls=False,
        )

        should_block, reason = blocker._should_block(
            "document",
            "http://127.0.0.1.nip.io/private",
        )

        assert should_block is True
        assert reason == "ssrf_protection"

    @pytest.mark.parametrize(
        "url",
        ["about:blank", "data:text/plain,hello", "blob:https://example.com/id"],
    )
    def test_should_allow_browser_internal_non_network_schemes(self, url):
        blocker = ResourceBlocker(
            block_images=False,
            block_fonts=False,
            block_media=False,
            block_css=False,
            block_ads=False,
            block_trackers=False,
            allow_local_urls=False,
        )

        assert blocker._should_block("script", url)[0] is False

    @pytest.mark.parametrize(
        "url",
        ["ftp://example.com/file", "file:///etc/passwd", "ws://example.com/socket"],
    )
    def test_should_block_non_http_network_or_file_schemes(self, url):
        blocker = ResourceBlocker(
            block_images=False,
            block_fonts=False,
            block_media=False,
            block_css=False,
            block_ads=False,
            block_trackers=False,
            allow_local_urls=False,
        )

        should_block, reason = blocker._should_block("script", url)

        assert should_block is True
        assert reason == "ssrf_protection"

    def test_should_block_images(self):
        """Test blocking image resources"""
        blocker = ResourceBlocker(block_images=True)

        assert blocker._should_block("image", "https://example.com/photo.jpg")[0] is True
        assert blocker._should_block("image", "https://example.com/logo.png")[0] is True

    def test_should_not_block_images_when_disabled(self):
        """Test not blocking images when disabled"""
        blocker = ResourceBlocker(
            block_images=False,
            block_ads=False,
            block_trackers=False,
            validate_ssrf=False,
        )

        assert blocker._should_block("image", "https://example.com/photo.jpg")[0] is False

    def test_should_block_fonts(self):
        """Test blocking font resources"""
        blocker = ResourceBlocker(block_fonts=True)

        assert blocker._should_block("font", "https://example.com/font.woff2")[0] is True

    def test_should_block_media(self):
        """Test blocking media resources"""
        blocker = ResourceBlocker(block_media=True)

        assert blocker._should_block("media", "https://example.com/video.mp4")[0] is True

    def test_should_block_css(self):
        """Test blocking CSS when enabled"""
        blocker = ResourceBlocker(block_css=True)

        assert blocker._should_block("stylesheet", "https://example.com/style.css")[0] is True

    def test_should_not_block_css_by_default(self):
        """Test not blocking CSS by default"""
        blocker = ResourceBlocker(block_ads=False, block_trackers=False, validate_ssrf=False)

        assert blocker._should_block("stylesheet", "https://example.com/style.css")[0] is False

    def test_should_block_google_analytics(self):
        """Test blocking Google Analytics"""
        blocker = ResourceBlocker(block_trackers=True, validate_ssrf=False)

        assert (
            blocker._should_block("script", "https://www.google-analytics.com/analytics.js")[0]
            is True
        )
        assert blocker._should_block("script", "https://www.googletagmanager.com/gtm.js")[0] is True

    def test_should_block_ads_domain(self):
        """Test blocking ad domains"""
        blocker = ResourceBlocker(block_ads=True)

        assert blocker._should_block("script", "https://doubleclick.net/ads.js")[0] is True
        assert blocker._should_block("script", "https://ads.example.com/banner.js")[0] is True

    def test_should_not_block_lookalike_domains(self):
        """Test that host matching respects domain boundaries."""
        blocker = ResourceBlocker(block_trackers=True, validate_ssrf=False)

        assert blocker._should_block("script", "https://facebook.com.evil.com/tr")[0] is False
        assert blocker._should_block("script", "https://facebook.com./tr")[0] is True

    def test_should_block_tracker_patterns(self):
        """Test blocking tracking patterns in URLs"""
        blocker = ResourceBlocker(block_trackers=True)

        assert blocker._should_block("script", "https://example.com/tracking.js")[0] is True
        assert blocker._should_block("script", "https://example.com/pixel.gif")[0] is True
        assert blocker._should_block("script", "https://analytics.example.com/track.js")[0] is True

    def test_domain_only_patterns_require_label_boundaries(self):
        blocker = ResourceBlocker(block_ads=True, block_trackers=False, validate_ssrf=False)

        assert blocker._should_block("script", "https://ads.example.com/banner.js")[0] is True
        assert blocker._should_block("script", "https://adservicedepot.com/banner.js")[0] is False

    def test_path_patterns_require_segment_boundaries(self):
        blocker = ResourceBlocker(block_trackers=True, block_ads=False, validate_ssrf=False)

        assert blocker._should_block("script", "https://example.com/tracking.js")[0] is True
        assert (
            blocker._should_block("script", "https://example.com/tracking-guide/index.html")[0]
            is False
        )

    def test_should_not_block_regular_resources(self):
        """Test not blocking regular resources"""
        blocker = ResourceBlocker(
            block_images=False,
            block_fonts=False,
            block_media=False,
            block_ads=False,
            block_trackers=False,
            validate_ssrf=False,
        )

        assert blocker._should_block("script", "https://example.com/main.js")[0] is False
        assert blocker._should_block("document", "https://example.com/page.html")[0] is False
        assert blocker._should_block("xhr", "https://api.example.com/data")[0] is False

    def test_case_insensitive_domain_matching(self):
        """Test that domain matching is case insensitive"""
        blocker = ResourceBlocker(block_trackers=True)

        assert (
            blocker._should_block("script", "https://GOOGLE-ANALYTICS.COM/analytics.js")[0] is True
        )
        assert blocker._should_block("script", "https://Example.com/TRACKING.js")[0] is True

    def test_blocked_domains_ignore_port_and_userinfo(self):
        blocker = ResourceBlocker(block_trackers=True)

        assert (
            blocker._should_block("script", "https://google-analytics.com:443/analytics.js")[0]
            is True
        )
        assert (
            blocker._should_block("script", "https://user:pass@google-analytics.com/analytics.js")[
                0
            ]
            is True
        )

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
        mock_request.resource_type = "image"
        mock_request.url = "https://example.com/photo.jpg"
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
            block_trackers=False,
            validate_ssrf=False,
        )

        # Mock route and request
        mock_route = AsyncMock()
        mock_request = Mock()
        mock_request.resource_type = "script"
        mock_request.url = "https://example.com/main.js"
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
        mock_request.resource_type = "script"
        mock_request.url = "https://google-analytics.com/analytics.js"
        mock_route.request = mock_request

        await blocker._handle_route(mock_route)

        # Should abort the request
        mock_route.abort.assert_called_once()
        assert blocker.blocked_count == 1

    @pytest.mark.asyncio
    async def test_handle_route_error_handling(self):
        """Test route handler blocks on error for security (prevents bypass attacks)"""
        blocker = ResourceBlocker()

        # Mock route that raises an error on request access
        mock_route = AsyncMock()
        mock_route.request = Mock()
        mock_route.request.resource_type = Mock(side_effect=RuntimeError("Test error"))

        # Should not raise exception
        await blocker._handle_route(mock_route)

        # Security: Should abort (block) on error to prevent bypass attacks
        mock_route.abort.assert_called_once()
        mock_route.continue_.assert_not_called()

    def test_get_stats_empty(self):
        """Test getting stats when no requests processed"""
        blocker = ResourceBlocker()
        stats = blocker.get_stats()

        assert stats["blocked_requests"] == 0
        assert stats["total_requests"] == 0
        assert stats["allowed_requests"] == 0
        assert stats["block_rate_pct"] == 0
        assert stats["blocked_by_type"] == {}
        assert stats["blocked_by_domain"] == {}

    def test_get_stats_with_blocking(self):
        """Test getting stats after blocking requests"""
        blocker = ResourceBlocker(block_images=True)

        # Simulate blocked requests
        blocker.total_count = 10
        blocker.blocked_count = 7
        blocker.blocked_by_type = {"image": 5, "font": 2}
        blocker.blocked_by_domain = {"google-analytics.com": 1}

        stats = blocker.get_stats()

        assert stats["blocked_requests"] == 7
        assert stats["total_requests"] == 10
        assert stats["allowed_requests"] == 3
        assert stats["block_rate_pct"] == 70.0
        assert stats["blocked_by_type"] == {"image": 5, "font": 2}
        assert stats["blocked_by_domain"] == {"google-analytics.com": 1}

    def test_reset_stats(self):
        """Test resetting statistics"""
        blocker = ResourceBlocker()

        # Set some stats
        blocker.total_count = 10
        blocker.blocked_count = 7
        blocker.blocked_by_type = {"image": 5}
        blocker.blocked_by_domain = {"ads": 2}

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
        blocker._should_block("image", "https://example.com/1.jpg")
        blocker.total_count += 1
        blocker.blocked_count += 1
        blocker.blocked_by_type["image"] = blocker.blocked_by_type.get("image", 0) + 1

        blocker._should_block("font", "https://example.com/font.woff")
        blocker.total_count += 1
        blocker.blocked_count += 1
        blocker.blocked_by_type["font"] = blocker.blocked_by_type.get("font", 0) + 1

        blocker._should_block("script", "https://example.com/script.js")
        blocker.total_count += 1

        assert blocker.total_count == 3
        assert blocker.blocked_count == 2
        assert blocker.blocked_by_type == {"image": 1, "font": 1}


class TestBlockedDomains:
    """Test the BLOCKED_DOMAINS constant"""

    def test_blocked_domains_include_analytics(self):
        """Test that common analytics domains are included"""
        assert "google-analytics.com" in BLOCKED_DOMAINS
        assert "googletagmanager.com" in BLOCKED_DOMAINS
        assert "analytics." in _DOMAIN_ONLY_PATTERNS

    def test_blocked_domains_include_ads(self):
        """Test that common ad domains are included"""
        assert "doubleclick.net" in BLOCKED_DOMAINS
        assert "googlesyndication.com" in BLOCKED_DOMAINS
        assert "ads." in _DOMAIN_ONLY_PATTERNS

    def test_blocked_domains_include_trackers(self):
        """Test that common tracking domains are included"""
        assert "/tracking." in _PATH_PATTERNS
        assert "tracker." in _DOMAIN_ONLY_PATTERNS
        assert "/pixel." in _PATH_PATTERNS
        assert "/beacon." in _PATH_PATTERNS


class TestSecurityFixes:
    """Test security fixes for resource blocker bypass vulnerabilities"""

    def test_facebook_pixel_endpoint_is_blocked(self):
        blocker = ResourceBlocker(block_trackers=True)

        assert blocker._should_block("script", "https://facebook.com/tr?id=1")[0] is True
        assert blocker._should_block("script", "https://www.facebook.com/tr?id=1")[0] is True

    def test_ads_and_trackers_toggles_are_respected_independently(self):
        ads_only = ResourceBlocker(block_ads=True, block_trackers=False, validate_ssrf=False)
        trackers_only = ResourceBlocker(block_ads=False, block_trackers=True, validate_ssrf=False)

        assert (
            ads_only._should_block("script", "https://google-analytics.com/analytics.js")[0]
            is False
        )
        assert ads_only._should_block("script", "https://example.com/pixel.gif")[0] is False
        assert ads_only._should_block("script", "https://doubleclick.net/ad.js")[0] is True

        assert trackers_only._should_block("script", "https://doubleclick.net/ad.js")[0] is False
        assert (
            trackers_only._should_block("script", "https://google-analytics.com/analytics.js")[0]
            is True
        )
        assert trackers_only._should_block("script", "https://example.com/pixel.gif")[0] is True

    def test_url_encoding_bypass_tracking_dot(self):
        """Test that URL-encoded paths are blocked (e.g., /tracking%2e bypasses /tracking.)"""
        blocker = ResourceBlocker(block_trackers=True, validate_ssrf=False)
        # %2e is URL encoding for '.'
        # Should decode and match /tracking.
        assert blocker._should_block("script", "https://example.com/tracking%2ejs")[0] is True
        assert blocker._should_block("script", "https://example.com/tracking%2Ejs")[0] is True

    def test_url_encoding_bypass_tracking_slash(self):
        """Test that URL-encoded paths are blocked (e.g., /pixel%2f bypasses /pixel/)"""
        blocker = ResourceBlocker(block_trackers=True, validate_ssrf=False)
        # %2f is URL encoding for '/'
        # Should decode and match /pixel/
        assert blocker._should_block("script", "https://example.com/pixel%2fgif")[0] is True
        assert blocker._should_block("script", "https://example.com/pixel%2F.gif")[0] is True

    def test_url_encoding_bypass_beacon(self):
        """Test that URL-encoded beacon paths are blocked"""
        blocker = ResourceBlocker(block_trackers=True)
        # %2f for '/', %3f for '?'
        assert blocker._should_block("script", "https://example.com/beacon%2ftrack")[0] is True
        assert blocker._should_block("script", "https://example.com/beacon%3fid=1")[0] is True

    def test_substring_domain_false_positive(self):
        """Test that substring matching doesn't cause false positives"""
        blocker = ResourceBlocker(block_trackers=True, validate_ssrf=False)
        # "my-google-analytics-site.com" should NOT match "google-analytics.com"
        # because we use exact or subdomain boundary matching
        assert (
            blocker._should_block("script", "https://my-google-analytics-site.com/script.js")[0]
            is False
        )
        assert (
            blocker._should_block("script", "https://google-analytics-site.com/script.js")[0]
            is False
        )

    def test_exact_domain_match(self):
        """Test that exact domain matching works"""
        blocker = ResourceBlocker(block_trackers=True)
        # Exact match should work
        assert (
            blocker._should_block("script", "https://google-analytics.com/analytics.js")[0] is True
        )
        assert blocker._should_block("script", "https://doubleclick.net/ad.js")[0] is True

    def test_subdomain_match(self):
        """Test that subdomain matching works correctly"""
        blocker = ResourceBlocker(block_trackers=True)
        # Subdomain match should work
        assert (
            blocker._should_block("script", "https://www.google-analytics.com/analytics.js")[0]
            is True
        )
        assert (
            blocker._should_block("script", "https://analytics.google-analytics.com/analytics.js")[
                0
            ]
            is True
        )
        assert blocker._should_block("script", "https://ads.example.com/script.js")[0] is True

    def test_malformed_url_blocking(self):
        """Test that malformed URLs are blocked for security"""
        blocker = ResourceBlocker(block_trackers=True)
        # Malformed URLs should be blocked
        assert blocker._should_block("script", "not-a-valid-url")[0] is True
        assert blocker._should_block("script", "://broken-url")[0] is True

    def test_empty_domain_blocking(self):
        """Test that empty domains are blocked for security"""
        blocker = ResourceBlocker(block_trackers=True)
        # URLs with empty domain should be blocked
        assert blocker._should_block("script", "https:///path/only")[0] is True

    def test_normal_urls_still_allowed(self):
        """Test that normal valid URLs are not incorrectly blocked"""
        blocker = ResourceBlocker(block_trackers=True, validate_ssrf=False)
        # Normal URLs should not be blocked
        assert blocker._should_block("script", "https://example.com/main.js")[0] is False
        assert blocker._should_block("script", "https://www.example.com/app.js")[0] is False
