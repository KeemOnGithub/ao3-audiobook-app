"""Unit tests for AO3 URL Parser and Normalizer."""

import pytest
from backend.app.ingestion.exceptions import InvalidAO3UrlError
from backend.app.ingestion.url_parser import build_fetch_url, parse_ao3_url


def test_standard_chapter_url():
    url = "https://archiveofourown.org/works/123456/chapters/7891011"
    work_id, chapter_id = parse_ao3_url(url)
    assert work_id == "123456"
    assert chapter_id == "7891011"
    assert build_fetch_url(work_id, chapter_id) == "https://archiveofourown.org/works/123456/chapters/7891011?view_adult=true"


def test_work_root_url():
    url = "https://archiveofourown.org/works/123456"
    work_id, chapter_id = parse_ao3_url(url)
    assert work_id == "123456"
    assert chapter_id is None
    assert build_fetch_url(work_id, chapter_id) == "https://archiveofourown.org/works/123456?view_adult=true"


def test_work_root_with_trailing_slash():
    url = "https://archiveofourown.org/works/123456/"
    work_id, chapter_id = parse_ao3_url(url)
    assert work_id == "123456"
    assert chapter_id is None


def test_view_full_work_url_stripped():
    # Must strip query parameter and resolve to work root
    url = "https://archiveofourown.org/works/123456?view_full_work=true"
    work_id, chapter_id = parse_ao3_url(url)
    assert work_id == "123456"
    assert chapter_id is None
    assert "view_full_work" not in build_fetch_url(work_id, chapter_id)


def test_fragment_stripped():
    url = "https://archiveofourown.org/works/123456/chapters/7891011#workskin"
    work_id, chapter_id = parse_ao3_url(url)
    assert work_id == "123456"
    assert chapter_id == "7891011"


def test_collection_prefix():
    url = "https://archiveofourown.org/collections/yuletide2024/works/998877/chapters/665544"
    work_id, chapter_id = parse_ao3_url(url)
    assert work_id == "998877"
    assert chapter_id == "665544"


def test_http_and_www_prefixes():
    url = "http://www.archiveofourown.org/works/554433"
    work_id, chapter_id = parse_ao3_url(url)
    assert work_id == "554433"
    assert chapter_id is None


@pytest.mark.parametrize("invalid_url", [
    "https://fanfiction.net/s/12345",
    "https://archiveofourown.org/users/someone",
    "not-a-url",
    "",
    "https://archiveofourown.org/works/notdigits",
    "https://archiveofourown.org/tags/Anime",
])
def test_invalid_ao3_urls_raise_exception(invalid_url):
    with pytest.raises(InvalidAO3UrlError):
        parse_ao3_url(invalid_url)

