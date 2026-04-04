"""HTTP webhook notifier adapter."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request


# Errors that should NOT be retried (client-side or configuration problems).
# Note: URLError is intentionally NOT included - it includes transient network errors
# like DNS failures, connection refused, and timeouts which SHOULD be retried.
_NON_RETRYABLE = (ValueError, TypeError)


class HTTPWebhookNotifier:
    """Deliver JSON webhook payloads with bounded retries."""

    def __init__(
        self,
        max_retries: int = 2,
        retry_delay_seconds: float = 0.25,
        timeout_seconds: float = 5.0,
    ) -> None:
        self.max_retries = max(1, max_retries)
        self.retry_delay_seconds = max(0.0, retry_delay_seconds)
        self.timeout_seconds = max(1.0, timeout_seconds)

    def notify(self, webhook_url: str, payload: dict[str, object]) -> None:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds):
                    return
            except urllib.error.HTTPError as exc:
                last_error = exc
                # Retry on 429 (Too Many Requests) and 5xx server errors
                # Don't retry other 4xx client errors (they indicate configuration issues)
                if 400 <= exc.code < 500 and exc.code != 429:
                    break
                if attempt == self.max_retries - 1:
                    break
                if self.retry_delay_seconds > 0:
                    time.sleep(self.retry_delay_seconds)
            except _NON_RETRYABLE as exc:
                # Non-retryable: bad URL, bad payload, connection refused, etc.
                raise RuntimeError(
                    f"Webhook delivery failed (non-retryable) for {webhook_url}: {exc}"
                ) from exc
            except Exception as exc:
                last_error = exc
                if attempt == self.max_retries - 1:
                    break
                if self.retry_delay_seconds > 0:
                    time.sleep(self.retry_delay_seconds)
        raise RuntimeError(
            f"Webhook delivery failed after {self.max_retries} attempts for {webhook_url}"
        ) from last_error
