"""Domain exceptions for AO3 Ingestion & Text Normalization."""


class IngestionError(Exception):
    """Base exception for all ingestion-related errors."""
    pass


class InvalidAO3UrlError(IngestionError):
    """Raised when the provided URL does not match supported AO3 patterns."""
    def __init__(self, url: str):
        super().__init__(f"Invalid AO3 URL: '{url}'. Expected format: https://archiveofourown.org/works/<work_id>[/chapters/<chapter_id>]")
        self.url = url


class AO3NetworkError(IngestionError):
    """Raised on network connection errors, timeouts, or unrecoverable transport failures."""
    pass


class AO3RateLimitError(IngestionError):
    """Raised when AO3 returns HTTP 429 and retry attempts are exhausted."""
    def __init__(self, message: str = "AO3 rate limit exceeded. Retries exhausted."):
        super().__init__(message)


class AO3WorkNotFoundError(IngestionError):
    """Raised when AO3 returns HTTP 404 Not Found."""
    def __init__(self, message: str = "AO3 work or chapter not found (HTTP 404)."):
        super().__init__(message)


class AO3RestrictedWorkError(IngestionError):
    """Raised when access is denied because the work is restricted to registered users."""
    def __init__(self, message: str = "This work is restricted to registered AO3 users."):
        super().__init__(message)


class AO3UnrevealedWorkError(IngestionError):
    """Raised when the work is part of an unrevealed collection or mystery challenge."""
    def __init__(self, message: str = "This work is currently unrevealed."):
        super().__init__(message)


class AO3ParsingError(IngestionError):
    """Raised when the DOM structure cannot be parsed or narrative container is missing."""
    pass

