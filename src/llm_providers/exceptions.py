"""Unified LLM provider exception hierarchy.

All provider-layer failures are converted into these domain exceptions so
callers (Agent Core) never depend on transport-level details (httpx, HTTP
status codes). Error messages are safe for logging: they never contain API
keys, authorization headers or full request bodies (see SECURITY.md §3, §8).
"""

from __future__ import annotations


class LLMProviderError(Exception):
    """Base class for all LLM provider failures."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        provider: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.retryable = retryable
        self.provider = provider
        self.status_code = status_code

    def __str__(self) -> str:
        parts = [self.message]
        if self.provider is not None:
            parts.append(f"provider={self.provider}")
        if self.status_code is not None:
            parts.append(f"status_code={self.status_code}")
        return "; ".join(parts)


class LLMConfigurationError(LLMProviderError):
    """Invalid or missing provider configuration (not retryable)."""

    def __init__(self, message: str, provider: str | None = None) -> None:
        super().__init__(message, retryable=False, provider=provider)


class LLMAuthenticationError(LLMProviderError):
    """API rejected credentials (HTTP 401/403). Never retried automatically."""

    def __init__(
        self, message: str, provider: str | None = None, status_code: int | None = None
    ) -> None:
        super().__init__(message, retryable=False, provider=provider, status_code=status_code)


class LLMRateLimitError(LLMProviderError):
    """Provider rate limit hit (HTTP 429). Retryable with backoff."""

    def __init__(
        self,
        message: str,
        provider: str | None = None,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message, retryable=True, provider=provider, status_code=status_code)
        self.retry_after = retry_after


class LLMTimeoutError(LLMProviderError):
    """Request exceeded the configured timeout. Retryable."""

    def __init__(self, message: str, provider: str | None = None) -> None:
        super().__init__(message, retryable=True, provider=provider)


class LLMConnectionError(LLMProviderError):
    """Network/connection failure (DNS, refused, reset). Retryable."""

    def __init__(self, message: str, provider: str | None = None) -> None:
        super().__init__(message, retryable=True, provider=provider)


class LLMInvalidRequestError(LLMProviderError):
    """Provider rejected the request content (HTTP 400/404/422). Not retried."""

    def __init__(
        self, message: str, provider: str | None = None, status_code: int | None = None
    ) -> None:
        super().__init__(message, retryable=False, provider=provider, status_code=status_code)


class LLMProviderResponseError(LLMProviderError):
    """Provider returned a malformed or server-side error response (HTTP 5xx, bad JSON)."""

    def __init__(
        self, message: str, provider: str | None = None, status_code: int | None = None
    ) -> None:
        super().__init__(message, retryable=True, provider=provider, status_code=status_code)


class LLMEmptyResponseError(LLMProviderError):
    """Provider returned HTTP 200 but no usable content. Not retried automatically."""

    def __init__(self, message: str, provider: str | None = None) -> None:
        super().__init__(message, retryable=False, provider=provider)
