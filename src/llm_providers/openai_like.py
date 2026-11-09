"""OpenAI-compatible Chat Completions adapter.

Implements docs/DEVELOPMENT_PLAN.md Stage 4 (basic chat completion) and
Stage 5 §5.1 (tool calls):
- base URL / API key / model come exclusively from configuration;
- per-request and provider-wide timeouts;
- bounded retries with exponential backoff + jitter for transient errors;
- advertised `tools` are passed through to the payload when present;
- responses normalize into `LLMResponse`; `message.tool_calls` requests are
  mapped into `ToolCall` objects and count as a valid non-empty outcome;
- missing usage stays `None`; secrets, authorization headers and request
  bodies are never logged (docs/SECURITY.md §3, §8).

Streaming and multimodal features are intentionally NOT implemented.
Only a single request/response is performed — the tool-call loop belongs
to Agent Core (Stage 5.2), not to the adapter.
"""

from __future__ import annotations

import asyncio
import json
import random
from typing import Any

import httpx

from src.llm_providers.base import LLMProvider
from src.llm_providers.exceptions import (
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMConnectionError,
    LLMEmptyResponseError,
    LLMInvalidRequestError,
    LLMProviderError,
    LLMProviderResponseError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from src.llm_providers.privacy import NoOpPrivacyFilter, PrivacyFilter
from src.llm_providers.schemas import LLMRequest, LLMResponse, TokenUsage, ToolCall
from src.monitoring.logging import get_logger

logger = get_logger("llm_providers.openai_like")

_RETRYABLE_STATUS_CODES = {500, 502, 503, 504}


class OpenAICompatibleProvider(LLMProvider):
    """Adapter for any OpenAI-compatible /chat/completions endpoint."""

    provider_name = "openai_compatible"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        default_model: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 2,
        backoff_base: float = 0.5,
        backoff_cap: float = 10.0,
        privacy_filter: PrivacyFilter | None = None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        """Validate configuration and (optionally) accept an injected HTTP client for tests."""
        if not base_url or not base_url.strip():
            raise LLMConfigurationError("LLM base URL must not be empty.")
        if not api_key or not api_key.strip():
            raise LLMConfigurationError("LLM API key must not be empty.")
        if max_retries < 0:
            raise LLMConfigurationError("max_retries must be non-negative.")
        if timeout <= 0:
            raise LLMConfigurationError("timeout must be positive.")

        self._base_url = base_url.rstrip("/")
        self._default_model = default_model
        self._default_timeout = timeout
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._backoff_cap = backoff_cap
        self._privacy_filter: PrivacyFilter = (
            privacy_filter if privacy_filter is not None else NoOpPrivacyFilter()
        )
        self._headers = {"Authorization": f"Bearer {api_key.strip()}"}
        self._owns_client = http is None
        self._client = (
            http if http is not None else httpx.AsyncClient(timeout=self._default_timeout)
        )

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Apply the privacy filter, call the endpoint with bounded retries, normalize."""
        effective_request = await self._privacy_filter.apply(request)
        payload = self._build_payload(effective_request)
        timeout = effective_request.timeout or self._default_timeout

        last_error: LLMProviderError | None = None
        for attempt in range(self._max_retries + 1):
            try:
                return await self._send_once(payload, timeout)
            except LLMProviderError as error:
                if not error.retryable or attempt >= self._max_retries:
                    raise
                last_error = error
                delay = self._compute_backoff(attempt, _retryable_retry_after(error))
                logger.warning(
                    "llm.request.retry",
                    attempt=attempt + 1,
                    delay_seconds=round(delay, 3),
                    error_type=type(error).__name__,
                    status_code=error.status_code,
                    model=payload.get("model"),
                )
                await _sleep(delay)
        raise (
            last_error if last_error is not None else LLMProviderError("unreachable")
        )  # pragma: no cover

    # ─── Request building ─────────────────────────────────────────────

    def _build_payload(self, request: LLMRequest) -> dict[str, Any]:
        """Build the JSON payload; secrets are never part of it."""
        model = request.model or self._default_model
        if not model:
            raise LLMConfigurationError(
                "No model specified: set LLM_DEFAULT_MODEL or pass request.model."
            )
        payload: dict[str, Any] = {
            "model": model,
            "messages": request.compiled_messages(),
            "temperature": request.temperature,
        }
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.tools:
            payload["tools"] = [spec.to_vendor_format() for spec in request.tools]
        payload.update(request.extra_params)
        return payload

    # ─── Single HTTP attempt ──────────────────────────────────────────

    async def _send_once(self, payload: dict[str, Any], timeout: float) -> LLMResponse:
        """Perform one HTTP attempt and map all failures onto domain errors.

        Logs contain only event name, error type and status code — never the
        API key, headers or the request/response bodies.
        """
        try:
            response = await self._client.post(
                url=f"{self._base_url}/chat/completions",
                json=payload,
                timeout=timeout,
                headers=self._headers,
            )
        except httpx.TimeoutException as error:
            logger.warning(
                "llm.request.timeout", error_type=type(error).__name__, model=payload.get("model")
            )
            raise LLMTimeoutError(
                f"LLM request timed out after {timeout} seconds.", provider=self.provider_name
            ) from error
        except httpx.HTTPError as error:
            logger.warning(
                "llm.request.connection_error",
                error_type=type(error).__name__,
                model=payload.get("model"),
            )
            raise LLMConnectionError(
                "Could not connect to the LLM endpoint.", provider=self.provider_name
            ) from error

        self._raise_for_status(response.status_code)
        raw = self._parse_json(response)
        return self._normalize(raw)

    def _raise_for_status(self, status_code: int) -> None:
        """Translate HTTP status codes into domain errors."""
        if status_code < 300:
            return
        if status_code in (401, 403):
            raise LLMAuthenticationError(
                "LLM API rejected credentials (check LLM_API_KEY).",
                provider=self.provider_name,
                status_code=status_code,
            )
        if status_code == 429:
            raise LLMRateLimitError(
                "LLM API rate limit exceeded.",
                provider=self.provider_name,
                status_code=status_code,
            )
        if status_code in (400, 404, 422):
            raise LLMInvalidRequestError(
                "LLM API rejected the request (invalid parameters or unknown model).",
                provider=self.provider_name,
                status_code=status_code,
            )
        if status_code >= 500 or status_code in _RETRYABLE_STATUS_CODES:
            raise LLMProviderResponseError(
                "LLM endpoint returned a server error.",
                provider=self.provider_name,
                status_code=status_code,
            )
        raise LLMProviderResponseError(
            "LLM endpoint returned an unexpected status.",
            provider=self.provider_name,
            status_code=status_code,
        )

    def _parse_json(self, response: httpx.Response) -> dict[str, Any]:
        """Parse the response body into a dict without logging its contents."""
        try:
            data = response.json()
        except (ValueError, httpx.DecodingError) as error:
            raise LLMProviderResponseError(
                "LLM endpoint returned a malformed (non-JSON) response.",
                provider=self.provider_name,
                status_code=response.status_code,
            ) from error
        if not isinstance(data, dict):
            raise LLMProviderResponseError(
                "LLM endpoint returned an unexpected response structure.",
                provider=self.provider_name,
                status_code=response.status_code,
            )
        return data

    # ─── Normalization ────────────────────────────────────────────────

    def _normalize(self, data: dict[str, Any]) -> LLMResponse:
        """Convert a vendor JSON object into the normalized `LLMResponse`.

        A response with `message.tool_calls` is valid even with empty content;
        without tool calls, empty content is an `LLMEmptyResponseError`.
        """
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMEmptyResponseError(
                "LLM endpoint returned no choices.", provider=self.provider_name
            )
        choice = choices[0]
        if not isinstance(choice, dict):
            raise LLMEmptyResponseError(
                "LLM endpoint returned an invalid choice structure.", provider=self.provider_name
            )
        message = choice.get("message")
        if not isinstance(message, dict):
            message = {}
        content = message.get("content")
        tool_calls = self._parse_tool_calls(message)

        # Without tool calls, blank content is an empty response (ARCH §11).
        is_empty = not isinstance(content, str) or not content.strip()
        if not tool_calls and is_empty:
            raise LLMEmptyResponseError(
                "LLM endpoint returned an empty response.", provider=self.provider_name
            )

        usage_raw = data.get("usage")
        usage = (
            TokenUsage(
                prompt_tokens=_positive_or_none(usage_raw, "prompt_tokens"),
                completion_tokens=_positive_or_none(usage_raw, "completion_tokens"),
                total_tokens=_positive_or_none(usage_raw, "total_tokens"),
            )
            if isinstance(usage_raw, dict)
            else TokenUsage()
        )

        request_id = data.get("id")
        finish_reason = choice.get("finish_reason")
        return LLMResponse(
            content=content if isinstance(content, str) else "",
            model=str(data.get("model") or "unknown"),
            provider=self.provider_name,
            finish_reason=finish_reason if isinstance(finish_reason, str) else None,
            usage=usage,
            request_id=request_id if isinstance(request_id, str) else None,
            metadata={"created": data.get("created"), "object": data.get("object")},
            tool_calls=tool_calls or None,
        )

    def _parse_tool_calls(self, message: dict[str, Any]) -> list[ToolCall]:
        """Extract and validate `message.tool_calls` into normalized `ToolCall`s.

        The vendor format wraps each call as
        `{"id": ..., "type": "function", "function": {"name": ..., "arguments": "<json str>"}}`.
        Arguments must be a JSON **object**; malformed JSON is a domain error
        (LLMInvalidRequestError) — the response is never silently mangled.
        """
        raw_calls = message.get("tool_calls")
        if raw_calls is None:
            return []
        if not isinstance(raw_calls, list):
            raise LLMInvalidRequestError(
                "LLM endpoint returned an invalid tool_calls structure.",
                provider=self.provider_name,
            )

        normalized: list[ToolCall] = []
        for raw_call in raw_calls:
            if not isinstance(raw_call, dict):
                raise LLMInvalidRequestError(
                    "LLM endpoint returned an invalid tool call entry.",
                    provider=self.provider_name,
                )
            function = raw_call.get("function")
            if not isinstance(function, dict):
                raise LLMInvalidRequestError(
                    "LLM tool call is missing the 'function' object.",
                    provider=self.provider_name,
                )
            name = function.get("name")
            if not isinstance(name, str) or not name.strip():
                raise LLMInvalidRequestError(
                    "LLM tool call is missing a valid function name.",
                    provider=self.provider_name,
                )
            normalized.append(
                ToolCall(
                    id=raw_call.get("id") if isinstance(raw_call.get("id"), str) else None,
                    name=name,
                    arguments=_parse_arguments(function.get("arguments")),
                )
            )
        return normalized

    # ─── Retry backoff ────────────────────────────────────────────────

    def _compute_backoff(self, attempt: int, retry_after: float | None) -> float:
        """Exponential backoff with jitter, capped; honors Retry-After when present."""
        if retry_after is not None:
            return min(retry_after, self._backoff_cap)
        ceiling = min(self._backoff_base * (2**attempt), self._backoff_cap)
        return random.uniform(0.0, ceiling)

    async def aclose(self) -> None:
        """Close the owned HTTP client (not one injected from outside)."""
        if self._owns_client:
            await self._client.aclose()


def _positive_or_none(mapping: dict[str, Any], key: str) -> int | None:
    """Extract a non-negative integer counter; return None on any absence."""
    value = mapping.get(key)
    if isinstance(value, int) and value >= 0:
        return value
    return None


def _parse_arguments(raw_arguments: Any) -> dict[str, Any]:
    """Parse tool call arguments: a JSON object string or an already-parsed dict.

    Non-object values and malformed JSON raise `LLMInvalidRequestError`;
    the raw value is never included in the log or error message (SECURITY §8).
    """
    if raw_arguments is None:
        return {}
    if isinstance(raw_arguments, dict):
        return raw_arguments
    if isinstance(raw_arguments, str):
        try:
            parsed = json.loads(raw_arguments)
        except ValueError as error:
            raise LLMInvalidRequestError(
                "LLM tool call arguments are not valid JSON.",
                provider="openai_compatible",
            ) from error
        if isinstance(parsed, dict):
            return parsed
    raise LLMInvalidRequestError(
        "LLM tool call arguments must be a JSON object.",
        provider="openai_compatible",
    )


async def _sleep(seconds: float) -> None:
    """Async sleep wrapper (kept async-compatible for testability)."""
    await asyncio.sleep(seconds)


def _retryable_retry_after(error: LLMProviderError) -> float | None:
    """Return the Retry-After hint if the error carries one."""
    if isinstance(error, LLMRateLimitError):
        return error.retry_after
    return None
