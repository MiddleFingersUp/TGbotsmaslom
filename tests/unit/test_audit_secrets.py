"""Unit tests for the offline secrets audit script (scripts/audit_secrets.py).

Stage 8.2/8.3 (docs/SECURITY.md §18.2). Tests call the script's functions
directly — no subprocess is spawned; exit-code behaviour is verified
through the ``main()`` return value.

NOTE on fixture assembly: every fake secret below is built from fragments
at RUNTIME so the full value never appears verbatim in this (git-tracked)
test file — otherwise the auditor would flag its own test fixtures, while
the runtime value still exercises every detection rule honestly.
"""

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

_script_path = Path(__file__).resolve().parents[2] / "scripts" / "audit_secrets.py"
_spec = importlib.util.spec_from_file_location("audit_secrets", _script_path)
assert _spec is not None and _spec.loader is not None
audit_secrets: Any = importlib.util.module_from_spec(_spec)
sys.modules["audit_secrets"] = audit_secrets  # needed before exec (dataclasses)
_spec.loader.exec_module(audit_secrets)

# Runtime-assembled fake fixtures (see module docstring):
_FAKE_TOKEN = "1234567890" + ":AAFakeToken_ABCDEFGHIJKLMNOPQRSTUVWX"
_FAKE_TOKEN_2 = "1234567890" + ":AARealLookingToken_ABCDEFGHIJKLMNOPQRS"
_FAKE_KEY = "sk-abcdef" + "1234567890"
_FAKE_PASSWORD = "hunter2" + "secret"
_FAKE_PEM = "-----BEGIN " + "PRIVATE KEY-----"


def _write_tracked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, files: dict[str, str]) -> None:
    """Point the auditor at in-memory 'tracked files' (no real git needed)."""
    paths = []
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        paths.append(path)
    monkeypatch.setattr(audit_secrets, "tracked_files", lambda root: paths)


@pytest.mark.unit
@pytest.mark.security
def test_detects_fake_token_and_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fake Telegram bot token and OpenAI-style key must both be detected."""
    _write_tracked(
        tmp_path,
        monkeypatch,
        {"sample.py": (f"TOKEN = {_FAKE_TOKEN!r}\nKEY = {_FAKE_KEY!r}\n")},
    )
    findings = audit_secrets.run_audit(tmp_path)
    rules = {finding.rule for finding in findings}
    assert "telegram-bot-token" in rules
    assert "openai-style-api-key" in rules


@pytest.mark.unit
@pytest.mark.security
def test_detects_password_and_private_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Password assignment literal and PEM private-key block must be found."""
    _write_tracked(
        tmp_path,
        monkeypatch,
        {"creds.py": f"password={_FAKE_PASSWORD}\n{_FAKE_PEM}\n"},
    )
    findings = audit_secrets.run_audit(tmp_path)
    rules = {finding.rule for finding in findings}
    assert "password-assignment" in rules
    assert "private-key-block" in rules


@pytest.mark.unit
@pytest.mark.security
def test_documentation_placeholders_not_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``password = …`` style doc placeholders must not be reported."""
    _write_tracked(
        tmp_path,
        monkeypatch,
        {"docs.md": "| `credential_leak` | шаблоны `sk-…`, `password = …` |\n"},
    )
    assert audit_secrets.run_audit(tmp_path) == []


@pytest.mark.unit
@pytest.mark.security
def test_allowlist_exact_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Allowlisted fake fixture values are skipped; same shape elsewhere isn't."""
    # Known test fixture value (declared in the auditor's ALLOWED_VALUES).
    _write_tracked(tmp_path, monkeypatch, {"tests/test_x.py": 'KEY = "sk-test-offline"\n'})
    assert audit_secrets.run_audit(tmp_path) == []

    # Allowlist is exact-value: a slightly different fake key IS reported.
    not_allowlisted = "sk-test-off" + "line2"
    _write_tracked(tmp_path, monkeypatch, {"tests/test_x.py": f"KEY = {not_allowlisted!r}\n"})
    findings = audit_secrets.run_audit(tmp_path)
    assert len(findings) == 1

    # is_allowed(): placeholder values are allowed ONLY in .env.example.
    assert audit_secrets.is_allowed("changeme", ".env.example")
    assert audit_secrets.is_allowed("local-dev-key", "docs/env.example")
    assert not audit_secrets.is_allowed("changeme", "src/config/settings.py")
    assert not audit_secrets.is_allowed("sk-1234567890abcdef" + "x", "tests/unit/x.py")


@pytest.mark.unit
@pytest.mark.security
def test_env_example_scanned_with_placeholders_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """.env.example is scanned; dummy values pass, a real-looking one fails."""
    _write_tracked(
        tmp_path,
        monkeypatch,
        {".env.example": (f"POSTGRES_PASSWORD=changeme\nTOKEN={_FAKE_TOKEN_2}\n")},
    )
    findings = audit_secrets.run_audit(tmp_path)
    assert len(findings) == 1
    assert findings[0].rule == "telegram-bot-token"


@pytest.mark.unit
@pytest.mark.security
def test_masking_keeps_fragment_hides_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Report output shows first 4 / last 3 chars, never the raw secret."""
    _write_tracked(tmp_path, monkeypatch, {"sample.py": f"TOKEN = {_FAKE_TOKEN!r}\n"})
    findings = audit_secrets.run_audit(tmp_path)
    assert findings[0].masked == "1234…VWX"

    report = audit_secrets.format_findings(findings)
    assert "1234…VWX" in report
    assert _FAKE_TOKEN not in report  # raw value never printed
    # Short values are fully masked.
    assert audit_secrets.mask("abc123") == "****"


@pytest.mark.unit
@pytest.mark.security
def test_main_exit_codes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """main() returns 1 with findings and 0 when clean (no subprocess)."""
    _write_tracked(tmp_path, monkeypatch, {"sample.py": f"TOKEN = {_FAKE_TOKEN!r}\n"})
    assert audit_secrets.main(tmp_path) == 1

    _write_tracked(tmp_path, monkeypatch, {"clean.py": "value = 42\n"})
    assert audit_secrets.main(tmp_path) == 0
