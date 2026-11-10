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

# Claims that a business action was COMPLETED (or is being performed now).
# Such claims are valid ONLY when the corresponding backend tool actually
# ran within the same turn (docs/DEVELOPMENT_PLAN.md Stage 7, ARCHITECTURE
# §7: "LLM не является источником бизнес-правды"). Future-tense and
# past-tense promises without a tool call mislead the client ("Ваша заявка
# сохранена!" when nothing was persisted).
_UNBACKED_SAVE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)заявк\w*\s+(?:уже\s+)?(?:сохран|принят|оформлен|передан|созда|записан)"),
    re.compile(
        r"(?i)(?:я\s+)?(?:сохраню|сохраняю|сохранил|оформлю|оформил|запишу|записал|приня[лт])"
        r"\s+(?:вас\s+|вашу\s+|тебя\s+|для\s+вас\s+)?(?:в\s+базу\s+)?заявк"
    ),
)
_BACKED_BY_SAVE_LEAD = "save_lead"
_UNBACKED_HANDOFF_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)передаю?\s+(?:вас|вас\b|диалог|запрос|вывшег\w*)\s+(?:вместе\s+)?сотруднику"),
    re.compile(r"(?i)переда[яе]\s+(?:вас|диалог|запрос|вс[ay])\s+сотруднику"),
    re.compile(r"(?i)сотрудник\s+(?:скоро\s+)?подключит"),
)
_BACKED_BY_HANDOFF = "handoff_request"


def validate_response(text: str | None, executed_tools: list[str] | None = None) -> list[str]:
    """Return a list of validation issue codes; empty list means acceptable.

    `executed_tools` — backend tool names that actually ran in THIS turn;
    completion claims about заявки/передачи are valid only when the
    corresponding tool was executed (docs/ARCHITECTURE.md §7, §11).
    """
    if not text or not text.strip():
        return ["empty"]
    issues: list[str] = []
    if len(text) > MAX_RESPONSE_CHARS:
        issues.append("too_long")
    if any(pattern.search(text) for pattern in _SECRET_PATTERNS):
        issues.append("credential_leak")
    if any(pattern.search(text) for pattern in _ARTIFACT_PATTERNS):
        issues.append("tool_artifact")

    tools_used = executed_tools or []
    if _BACKED_BY_SAVE_LEAD not in tools_used and any(
        pattern.search(text) for pattern in _UNBACKED_SAVE_PATTERNS
    ):
        issues.append("unbacked_save_claim")
    if _BACKED_BY_HANDOFF not in tools_used and any(
        pattern.search(text) for pattern in _UNBACKED_HANDOFF_PATTERNS
    ):
        issues.append("unbacked_handoff_claim")
    return issues
