"""Unit tests for AO3 HTTP Client and Rate Limiter."""

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from backend.app.ingestion.client import AO3HttpClient, AO3RateLimiter
from backend.app.ingestion.exceptions import (
    AO3NetworkError,
    AO3RateLimitError,
    AO3RestrictedWorkError,
    AO3UnrevealedWorkError,
    AO3WorkNotFoundError,
)
from backend.tests.fixtures import HTML_RESTRICTED_WORK, HTML_UNREVEALED_WORK


@pytest.mark.asyncio
async def test_verify_restricted_work():
    """Verify restricted work detection from HTML."""
    with pytest.raises(AO3RestrictedWorkError):
        AO3HttpClient.verify_work_accessibility(HTML_RESTRICTED_WORK, "/works/123")

    with pytest.raises(AO3RestrictedWorkError):
        AO3HttpClient.verify_work_accessibility("<html><body>Normal</body></html>", "/users/login?restricted=true")


@pytest.mark.asyncio
async def test_verify_unrevealed_work():
    """Verify unrevealed/mystery work detection from HTML."""
    with pytest.raises(AO3UnrevealedWorkError):
        AO3HttpClient.verify_work_accessibility(HTML_UNREVEALED_WORK, "/works/123")


@pytest.mark.asyncio
async def test_http_404_raises_work_not_found():
    """Verify HTTP 404 response raises AO3WorkNotFoundError."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, request=request)

    transport = httpx.MockTransport(handler)
    mock_async_client = httpx.AsyncClient(transport=transport)
    rate_limiter = AO3RateLimiter(min_interval_seconds=0.0)
    client = AO3HttpClient(rate_limiter=rate_limiter, max_retries=1, client=mock_async_client)

    with pytest.raises(AO3WorkNotFoundError):
        await client.fetch_html("https://archiveofourown.org/works/99999999")


@pytest.mark.asyncio
async def test_http_429_exhausted_retries():
    """Verify HTTP 429 retries are attempted and raise AO3RateLimitError when exhausted."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(429, headers={"Retry-After": "0"}, request=request)

    transport = httpx.MockTransport(handler)
    mock_async_client = httpx.AsyncClient(transport=transport)
    rate_limiter = AO3RateLimiter(min_interval_seconds=0.0)
    client = AO3HttpClient(rate_limiter=rate_limiter, max_retries=2, client=mock_async_client)

    with pytest.raises(AO3RateLimitError):
        await client.fetch_html("https://archiveofourown.org/works/123")

    # Initial attempt + 2 retries = 3 calls
    assert call_count == 3


@pytest.mark.asyncio
async def test_stalled_transport_is_timed_out_and_closed(monkeypatch):
    cancelled = asyncio.Event()

    async def stalled_get(url):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    transport = AsyncMock()
    transport.get.side_effect = stalled_get
    client = AO3HttpClient(
        timeout=0.01, max_retries=0, rate_limiter=AO3RateLimiter(0)
    )
    monkeypatch.setattr(client, "_create_client", lambda: transport)

    with pytest.raises(AO3NetworkError, match="TimeoutError") as error:
        await asyncio.wait_for(client.fetch_html("https://archiveofourown.org/works/123"), 1)

    assert isinstance(error.value.__cause__, TimeoutError)
    assert cancelled.is_set()
    transport.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_fetch_cancellation_is_not_retried_or_wrapped():
    transport = AsyncMock()
    transport.get.side_effect = asyncio.CancelledError
    client = AO3HttpClient(client=transport, rate_limiter=AO3RateLimiter(0))

    with pytest.raises(asyncio.CancelledError):
        await client.fetch_html("https://archiveofourown.org/works/123")

    transport.get.assert_awaited_once()
    transport.aclose.assert_not_awaited()


@pytest.mark.asyncio
async def test_fetch_success_with_request_deadline():
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, text="<html>story</html>", request=request)
    )) as transport:
        client = AO3HttpClient(client=transport, rate_limiter=AO3RateLimiter(0))
        assert await client.fetch_html("https://archiveofourown.org/works/123") == "<html>story</html>"
        assert not transport.is_closed


@pytest.mark.asyncio
async def test_timed_out_attempt_can_retry_successfully(monkeypatch):
    attempts = 0

    async def get(url):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            await asyncio.Event().wait()
        return httpx.Response(200, text="story", request=httpx.Request("GET", url))

    transport = AsyncMock()
    transport.get.side_effect = get
    client = AO3HttpClient(
        client=transport, timeout=0.01, max_retries=1, rate_limiter=AO3RateLimiter(0)
    )
    monkeypatch.setattr("backend.app.ingestion.client.asyncio.sleep", AsyncMock())

    assert await asyncio.wait_for(client.fetch_html("https://archiveofourown.org/works/123"), 1) == "story"
    assert attempts == 2

