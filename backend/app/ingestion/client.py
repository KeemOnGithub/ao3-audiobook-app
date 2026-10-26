"""Async HTTP client with anti-blocking cookies, token bucket rate limiter, and retry logic."""

import asyncio
import os
import time
from typing import Dict, Optional
from urllib.parse import urlparse
import httpx

try:
    from curl_cffi.requests import AsyncSession as CurlAsyncSession
    HAVE_CURL_CFFI = True
except ImportError:
    HAVE_CURL_CFFI = False

from backend.app.ingestion.exceptions import (
    AO3NetworkError,
    AO3ParsingError,
    AO3RateLimitError,
    AO3RestrictedWorkError,
    AO3UnrevealedWorkError,
    AO3WorkNotFoundError,
)


class AO3RateLimiter:
    """
    Process-wide async token bucket rate limiter enforcing minimum interval
    between consecutive outgoing requests to archiveofourown.org.
    """
    _instance: Optional["AO3RateLimiter"] = None

    def __init__(self, min_interval_seconds: float = 2.0):
        self.min_interval = min_interval_seconds
        self._lock = asyncio.Lock()
        self._last_request_time: float = 0.0

    @classmethod
    def get_instance(cls, min_interval_seconds: float = 2.0) -> "AO3RateLimiter":
        if cls._instance is None:
            cls._instance = AO3RateLimiter(min_interval_seconds=min_interval_seconds)
        return cls._instance

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < self.min_interval:
                await asyncio.sleep(self.min_interval - elapsed)
            self._last_request_time = time.monotonic()


class AO3HttpClient:
    """Configured Async HTTP client for AO3 chapter fetching with browser TLS impersonation."""

    DEFAULT_HEADERS: Dict[str, str] = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Upgrade-Insecure-Requests": "1",
    }

    DEFAULT_COOKIES: Dict[str, str] = {
        "view_adult": "true",
        "accepted_tos": "20180523",
    }

    def __init__(
        self,
        rate_limiter: Optional[AO3RateLimiter] = None,
        timeout: float = 30.0,
        connect_timeout: float = 10.0,
        max_retries: int = 3,
        client: Optional[httpx.AsyncClient] = None,
    ):
        self.rate_limiter = rate_limiter or AO3RateLimiter.get_instance()
        self.timeout = httpx.Timeout(timeout, connect=connect_timeout)
        self.timeout_sec = timeout
        self.max_retries = max_retries
        self._external_client = client

    def get_cookies(self) -> Dict[str, str]:
        cookies = dict(self.DEFAULT_COOKIES)
        session = os.getenv("AO3_USER_SESSION")
        cf = os.getenv("AO3_CF_CLEARANCE")
        if session:
            cookies["_otwarchive_session"] = session
        if cf:
            cookies["cf_clearance"] = cf
        return cookies

    def _create_client(self):
        cookies = self.get_cookies()
        if HAVE_CURL_CFFI:
            return CurlAsyncSession(
                impersonate="safari17_0",
                timeout=self.timeout_sec,
                cookies=cookies,
                headers=self.DEFAULT_HEADERS,
            )
        return httpx.AsyncClient(
            http2=True,
            timeout=self.timeout,
            follow_redirects=True,
            headers=self.DEFAULT_HEADERS,
            cookies=cookies,
        )

    async def fetch_html(self, url: str) -> str:
        """
        Fetch HTML for a given canonical AO3 URL with rate limiting and exponential backoff.
        """
        client = self._external_client or self._create_client()
        should_close = self._external_client is None

        try:
            for attempt in range(self.max_retries + 1):
                await self.rate_limiter.acquire()
                try:
                    # Bound the await even if the transport's own timeout stalls.
                    response = await asyncio.wait_for(client.get(url), timeout=self.timeout_sec)
                except (AO3WorkNotFoundError, AO3RateLimitError, AO3RestrictedWorkError, AO3UnrevealedWorkError):
                    raise
                except Exception as exc:
                    if attempt < self.max_retries:
                        await asyncio.sleep(5.0 * (2 ** attempt))
                        continue
                    raise AO3NetworkError(
                        f"Network error fetching {url}: {type(exc).__name__}: {exc}"
                    ) from exc

                # Check HTTP status
                if response.status_code == 404:
                    raise AO3WorkNotFoundError(f"AO3 work or chapter not found: {url} (HTTP 404)")

                if response.status_code == 429 or response.status_code in (502, 503, 504):
                    if attempt < self.max_retries:
                        retry_after_hdr = response.headers.get("Retry-After")
                        if retry_after_hdr and retry_after_hdr.isdigit():
                            backoff = min(int(retry_after_hdr), 60)
                        else:
                            backoff = 5.0 * (2 ** attempt)
                        await asyncio.sleep(backoff)
                        continue
                    raise AO3RateLimitError(
                        f"AO3 returned HTTP {response.status_code}. Retry attempts exhausted for {url}."
                    )

                if response.status_code >= 400:
                    raise AO3NetworkError(f"HTTP {response.status_code} error fetching {url}")

                html = response.text
                resp_url = response.url
                if hasattr(resp_url, "path"):
                    final_url_path = resp_url.path
                else:
                    final_url_path = urlparse(str(resp_url)).path

                if "Shields are up!" in html:
                    raise AO3NetworkError(
                        "AO3 is currently protected by Cloudflare anti-bot challenge (Shields are up). "
                        "Please provide your browser 'AO3_CF_CLEARANCE' cookie in .env or try again shortly."
                    )

                # Validate against locked/restricted/unrevealed work pages
                self.verify_work_accessibility(html, final_url_path)

                return html

            raise AO3RateLimitError(f"Exhausted retries fetching {url}")
        finally:
            if should_close:
                if hasattr(client, "aclose"):
                    await client.aclose()
                elif hasattr(client, "close"):
                    res = client.close()
                    if asyncio.iscoroutine(res):
                        await res

    @staticmethod
    def verify_work_accessibility(html_text: str, final_url_path: str = "") -> None:
        """Check whether the returned HTML indicates a restricted or unrevealed work."""
        if final_url_path.startswith("/users/login"):
            raise AO3RestrictedWorkError("This work is restricted to registered AO3 users.")

        if '<div id="signin">' in html_text and 'id="workskin"' not in html_text:
            raise AO3RestrictedWorkError("This work is restricted to registered AO3 users.")

        lower_html = html_text.lower()
        if 'class="notice"' in html_text:
            if "mystery work" in lower_html or "unrevealed collection" in lower_html:
                raise AO3UnrevealedWorkError("This work is currently unrevealed.")

