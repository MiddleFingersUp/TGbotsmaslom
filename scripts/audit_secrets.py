"""Offline secrets audit for the TGbots repository (Stage 8.2).

Scans every git-tracked text file for patterns that look like committed
secrets (OpenAI-style keys, Telegram bot tokens, long high-entropy hex /
base64 blobs, password assignments, private-key blocks) and exits with
code 1 (and a list of ``file:line`` findings, secrets MASKED) when anything
suspicious is found.

Pure stdlib, fully offline: no network, no third-party imports, safe to run
in CI (see .github/workflows/tests.yml). Usage:

    python scripts/audit_secrets.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# ─── Scan scope ───────────────────────────────────────────────────────────
# Only text files are scanned (binary files are skipped by extension);
# additionally any file named like ``.env.example`` is scanned regardless
# of extension.
SCAN_EXTENSIONS: frozenset[str] = frozenset(
    {".py", ".yml", ".yaml", ".md", ".ps1", ".toml", ".ini", ".txt", ".json"}
)
PLACEHOLDER_FILENAMES: frozenset[str] = frozenset({"env.example", ".env.example"})

# ─── Detection rules ──────────────────────────────────────────────────────
# (rule name, compiled pattern). Group 1 (when present) is the extracted
# secret value used for masking; otherwise the whole match is the value.
RULES: list[tuple[str, re.Pattern[str], int]] = [
    (
        "openai-style-api-key",
        # OpenAI-like keys: sk- followed by 10+ token characters.
        # Documentation placeholders like "sk-…" do not match (ellipsis is
        # not in the character class).
        re.compile(r"sk-[A-Za-z0-9_-]{10,}"),
        0,
    ),
    (
        "telegram-bot-token",
        # Telegram bot tokens look like "<bot_id 9-10 digits>:<35 chars>".
        re.compile(r"\d{9,10}:[A-Za-z0-9_-]{30,}"),
        0,
    ),
    (
        "high-entropy-hex",
        # Long hex runs (40+ chars) — hashed secrets, API keys, DSAR tokens.
        re.compile(r"\b[0-9a-fA-F]{40,}\b"),
        0,
    ),
    (
        "high-entropy-base64",
        # Long base64-looking runs (40+ chars), optional = padding.
        re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b"),
        0,
    ),
    (
        "password-assignment",
        # password=<literal>; value group is checked against placeholders.
        re.compile(r"(?i)\bpassword\s*=\s*[\"']?([^\s\"']+)", re.IGNORECASE),
        1,
    ),
    (
        "private-key-block",
        # PEM private key headers (RSA / EC / OPENSSH / PKCS8 ...).
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        0,
    ),
]

# ─── Allowlist ────────────────────────────────────────────────────────────
# Exact benign values found in the repository. Each entry documents WHY it
# is allowed. Keeping the list EXACT-VALUE based (not path-based) means a
# real secret committed even into tests would still be reported.
ALLOWED_VALUES: dict[str, str] = {
    # tests/unit/test_agent_loop.py, test_logging.py: fake key used to
    # verify the response-validator blocks credential-shaped output.
    "sk-1234567890abcdef": "fake fixture: response validator / log masking tests",
    # tests/unit/test_agent_fallback.py: fake key for fallback detection.
    "sk-abcdef123456": "fake fixture: fallback validator test",
    # tests/unit/test_llm_tool_calls.py, test_llm_openai_like.py:
    # unittest fake provider key (mock transport never sends it anywhere).
    "sk-test-not-a-real-secret-0001": "fake fixture: LLM provider unit tests",
    # tests/unit/test_llm_no_secrets_in_logs.py: fake key for the
    # no-secrets-in-logs canary test.
    "sk-very-secret-test-key-do-not-log-777": "fake fixture: log-masking test",
    # tests/unit/test_llm_factory.py: offline factory wiring test.
    "sk-test-offline": "fake fixture: LLM factory test",
}

# Placeholder values allowed in .env.example only (it is the documented
# template — by design it contains dummy assignments, never real secrets).
PLACEHOLDER_VALUES: dict[str, str] = {
    "changeme": ".env.example placeholder for POSTGRES password",
    "local-dev-key": ".env.example placeholder for a local llama-server key",
}


@dataclass(frozen=True)
class Finding:
    """A single suspicious secret occurrence (value is already masked)."""

    file: str
    line: int
    rule: str
    masked: str


def mask(value: str) -> str:
    """Mask a secret: keep first 4 / last 3 characters, hide the middle.

    Short values (<= 9 chars) are fully masked so they cannot be recovered
    by combining the mask with the detection pattern.
    """
    if len(value) <= 9:
        return "****"
    return f"{value[:4]}…{value[-3:]}"


def _is_placeholder_value(value: str) -> bool:
    """Values that are documentation placeholders, not real secrets."""
    return value.startswith(("…", "...", "***", "<", "$", "{", "["))


def is_allowed(value: str, file: str) -> bool:
    """Return True when a finding is explicitly allowlisted (documented)."""
    return value in ALLOWED_VALUES or (
        Path(file).name in PLACEHOLDER_FILENAMES and value in PLACEHOLDER_VALUES
    )


def scan_text(text: str, file: str) -> list[tuple[int, str, str]]:
    """Scan text content; return (line_number, rule, raw_value) triples."""
    findings: list[tuple[int, str, str]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for rule_name, pattern, group in RULES:
            for match in pattern.finditer(line):
                value = match.group(group) if group else match.group(0)
                if _is_placeholder_value(value):
                    continue
                findings.append((line_number, rule_name, value))
    return findings


def tracked_files(root: Path) -> list[Path]:
    """List git-tracked files under ``root`` (relative to it)."""
    result = subprocess.run(  # noqa: S603 - fixed arguments, no shell
        ["git", "ls-files"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0:
        msg = f"git ls-files failed: {result.stderr.strip()}"
        raise RuntimeError(msg)
    return [root / line for line in result.stdout.splitlines() if line.strip()]


def _should_scan(path: Path) -> bool:
    """Text files only, selected by extension or well-known filename."""
    if path.name in PLACEHOLDER_FILENAMES:
        return True
    return path.suffix.lower() in SCAN_EXTENSIONS


def run_audit(root: Path | None = None) -> list[Finding]:
    """Run the audit and return the list of findings (empty list = clean)."""
    root = root if root is not None else Path.cwd()
    findings: list[Finding] = []
    for path in tracked_files(root):
        if not _should_scan(path):
            continue
        rel_path = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError as error:
            findings.append(Finding(rel_path, 0, "unreadable-file", f"{type(error).__name__}"))
            continue
        if "\x00" in text[:8192]:  # binary safeguard
            continue
        for line_number, rule_name, value in scan_text(text, rel_path):
            if is_allowed(value, rel_path):
                continue
            findings.append(Finding(rel_path, line_number, rule_name, mask(value)))
    return findings


def format_findings(findings: list[Finding]) -> str:
    """Render findings as a ``file:line`` report with masked fragments."""
    lines = ["SECRETS AUDIT: FAILED — suspicious values found:", ""]
    for finding in findings:
        lines.append(f"  {finding.file}:{finding.line}  [{finding.rule}]  {finding.masked}")
    lines.append("")
    lines.append("Resolve the values above, or add a documented allowlist entry")
    lines.append("in scripts/audit_secrets.py if the value is provably benign.")
    return "\n".join(lines)


def main(root: Path | None = None) -> int:
    """Entry point; returns process exit code (0 clean, 1 findings, 2 error)."""
    try:
        findings = run_audit(root)
        file_count = len(tracked_files(root or Path.cwd()))
    except RuntimeError as error:
        print(f"SECRETS AUDIT: ERROR — {error}", file=sys.stderr)
        return 2
    if findings:
        print(format_findings(findings), file=sys.stderr)
        return 1
    print(
        f"SECRETS AUDIT: OK — no suspicious secrets in tracked files ({file_count} files scanned)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
