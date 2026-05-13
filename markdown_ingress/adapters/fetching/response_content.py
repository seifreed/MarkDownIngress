"""Response reading and result assembly for the HTTPX fetcher."""

import logging
from typing import Any

import httpx

from markdown_ingress.adapters.fetching.http_support import (
    ResponseSizeLimitError,
    parse_content_length,
)
from markdown_ingress.models import FetchResult

logger = logging.getLogger(__name__)


class ResponseContentMixin:
    """HTTP response body helpers shared by sync and async fetch paths."""

    max_response_size: int | None

    @staticmethod
    def _decode_content(content: bytes, charset_encoding: str | None) -> str:
        encoding = charset_encoding or "utf-8"
        try:
            return content.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            return content.decode("utf-8", errors="replace")

    def _enforce_declared_response_size(self, response: httpx.Response) -> None:
        if self.max_response_size is None:
            return
        content_length = response.headers.get("content-length")
        parsed_length = parse_content_length(content_length)
        if parsed_length is not None and parsed_length > self.max_response_size:
            raise ResponseSizeLimitError(
                f"Response size {parsed_length} exceeds "
                f"max_response_size {self.max_response_size}"
            )

    async def _read_async_response_content(self, response: httpx.Response) -> bytes:
        chunks: list[bytes] = []
        total_size = 0
        async for chunk in response.aiter_bytes():
            total_size += len(chunk)
            if self.max_response_size is not None and total_size > self.max_response_size:
                raise ResponseSizeLimitError(
                    f"Response content size {total_size} exceeds "
                    f"max_response_size {self.max_response_size}"
                )
            chunks.append(chunk)
        return b"".join(chunks)

    async def _drain_async_response_for_reuse(
        self, response: httpx.Response, log_message: str
    ) -> None:
        total_size = 0
        try:
            async for chunk in response.aiter_bytes():
                total_size += len(chunk)
                if self.max_response_size is not None and total_size > self.max_response_size:
                    raise ResponseSizeLimitError(
                        f"Response content size {total_size} exceeds "
                        f"max_response_size {self.max_response_size}"
                    )
        except ResponseSizeLimitError:
            raise
        except Exception:
            logger.debug(log_message, exc_info=True)

    def _read_sync_response_content(self, response: httpx.Response) -> bytes:
        chunks: list[bytes] = []
        total_size = 0
        for chunk in response.iter_bytes():
            total_size += len(chunk)
            if self.max_response_size is not None and total_size > self.max_response_size:
                raise ResponseSizeLimitError(
                    f"Response content size {total_size} exceeds "
                    f"max_response_size {self.max_response_size}"
                )
            chunks.append(chunk)
        return b"".join(chunks)

    def _drain_sync_response_for_reuse(self, response: httpx.Response, log_message: str) -> None:
        total_size = 0
        try:
            for chunk in response.iter_bytes():
                total_size += len(chunk)
                if self.max_response_size is not None and total_size > self.max_response_size:
                    raise ResponseSizeLimitError(
                        f"Response content size {total_size} exceeds "
                        f"max_response_size {self.max_response_size}"
                    )
        except ResponseSizeLimitError:
            raise
        except Exception:
            logger.debug(log_message, exc_info=True)

    def _make_fetch_result(
        self,
        content: bytes,
        requested_url: str,
        final_url: str,
        response: Any,
        elapsed_ms: float,
        ua: str,
        attempt: int,
        *,
        ssl_bypass: bool = False,
        total_attempt: int | None = None,
    ) -> FetchResult:
        html = self._decode_content(content, response.charset_encoding)
        metadata: dict[str, object] = {
            "fetcher": "httpx",
            "user_agent": ua,
            "attempt": total_attempt or attempt + 1,
        }
        if ssl_bypass:
            metadata["ssl_bypass"] = True
        return FetchResult(
            html=html,
            url=requested_url,
            status_code=response.status_code,
            final_url=final_url,
            headers=response.headers,
            timing_ms=elapsed_ms,
            metadata=metadata,
        )
