"""Final-response text validation (docs/ARCHITECTURE.md §7, docs/SECURITY.md).

Checks exist to guarantee the client never receives: empty text, oversized
fragments, leaked credentials, or raw tool/instruction artifacts. On failure
the agent loop regenerates once without tools, then falls back.
"""

from __future__ import annotations

import re

MAX_RESPONSE_CHARS = 2000

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{10,}"),
    re.compile(r"(?i)\b(api[_-]?key|secret|password)\s*[:=]\s*\S"),
)

_ARTIFACT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\btool_call"),
    re.compile(r'"tool_calls"'),
    re.compile(r"\{[\s\S]*\"name\"\s*:"),
    re.compile(r"</?\s*(system|assistant|tool)\s*>"),
)


def validate_response(text: str | None) -> list[str]:
    """Return a list of validation issue codes; empty list means acceptable."""
    if not text or not text.strip():
        return ["empty"]
    issues: list[str] = []
    if len(text) > MAX_RESPONSE_CHARS:
        issues.append("too_long")
    if any(pattern.search(text) for pattern in _SECRET_PATTERNS):
        issues.append("credential_leak")
    if any(pattern.search(text) for pattern in _ARTIFACT_PATTERNS):
        issues.append("tool_artifact")
    return issues
