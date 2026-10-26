"""AO3 Ingestion & Text Normalization Package."""

from backend.app.ingestion.exceptions import (
    AO3NetworkError,
    AO3ParsingError,
    AO3RateLimitError,
    AO3RestrictedWorkError,
    AO3UnrevealedWorkError,
    AO3WorkNotFoundError,
    IngestionError,
    InvalidAO3UrlError,
)
from backend.app.ingestion.schemas import (
    ChapterMetadata,
    IngestionResult,
    NarrativeChunk,
    NormalizedParagraph,
)
from backend.app.ingestion.service import ingest_ao3_chapter
from backend.app.ingestion.url_parser import build_fetch_url, parse_ao3_url

__all__ = [
    "AO3NetworkError",
    "AO3ParsingError",
    "AO3RateLimitError",
    "AO3RestrictedWorkError",
    "AO3UnrevealedWorkError",
    "AO3WorkNotFoundError",
    "IngestionError",
    "InvalidAO3UrlError",
    "ChapterMetadata",
    "IngestionResult",
    "NarrativeChunk",
    "NormalizedParagraph",
    "ingest_ao3_chapter",
    "parse_ao3_url",
    "build_fetch_url",
]

