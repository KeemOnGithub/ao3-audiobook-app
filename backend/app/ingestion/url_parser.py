"""AO3 URL Parsing, Validation, and Canonical URL Resolution."""

import re
from typing import Optional, Tuple
from urllib.parse import urlparse

from backend.app.ingestion.exceptions import InvalidAO3UrlError

AO3_URL_REGEX = re.compile(
    r"^https?://(?:www\.)?archiveofourown\.org"
    r"(?:/collections/[^/]+)?"
    r"/works/(?P<work_id>\d+)"
    r"(?:/chapters/(?P<chapter_id>\d+))?/?$"
)


def parse_ao3_url(raw_url: str) -> Tuple[str, Optional[str]]:
    """
    Parse and validate an arbitrary AO3 URL.

    Strips query parameters and fragment identifiers before regex matching.
    Supports optional www, http/https, and optional /collections/{collection_name} prefixes.

    Returns:
        Tuple of (work_id: str, chapter_id: Optional[str])

    Raises:
        InvalidAO3UrlError: If the URL does not match supported AO3 patterns.
    """
    if not raw_url or not isinstance(raw_url, str):
        raise InvalidAO3UrlError(str(raw_url))

    cleaned = raw_url.strip()
    # Strip fragment and query parameters
    parsed = urlparse(cleaned)
    normalized_path_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

    match = AO3_URL_REGEX.match(normalized_path_url)
    if not match:
        raise InvalidAO3UrlError(raw_url)

    work_id = match.group("work_id")
    chapter_id = match.group("chapter_id")

    return work_id, chapter_id


def build_fetch_url(work_id: str, chapter_id: Optional[str] = None) -> str:
    """
    Construct canonical fetch URL with adult interstitial bypass flag.
    Omits view_full_work=true to enforce chapter-scoped execution.
    """
    if chapter_id and chapter_id != "oneshot":
        return f"https://archiveofourown.org/works/{work_id}/chapters/{chapter_id}?view_adult=true"
    return f"https://archiveofourown.org/works/{work_id}?view_adult=true"

