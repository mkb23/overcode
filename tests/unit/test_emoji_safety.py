"""Tests for VS16 emoji safety net used to keep Windows Terminal / Konsole
from drifting on variation-selector-bearing emoji (#174 follow-up).
"""

from unittest.mock import patch


def test_safe_emoji_strips_vs16_when_terminal_untrusted(monkeypatch):
    from overcode import status_constants
    monkeypatch.setattr(status_constants, "_FULL_COLOR_EMOJI", False)
    # 🖥️ = U+1F5A5 U+FE0F → should drop the trailing FE0F
    assert status_constants._safe_emoji("\U0001f5a5️") == "\U0001f5a5"
    assert status_constants._safe_emoji("✏️") == "✏"  # ✏


def test_safe_emoji_keeps_vs16_when_terminal_trusted(monkeypatch):
    from overcode import status_constants
    monkeypatch.setattr(status_constants, "_FULL_COLOR_EMOJI", True)
    assert status_constants._safe_emoji("\U0001f5a5️") == "\U0001f5a5️"


def test_safe_emoji_noop_when_no_vs16(monkeypatch):
    from overcode import status_constants
    monkeypatch.setattr(status_constants, "_FULL_COLOR_EMOJI", False)
    # 📖 = U+1F4D6 — emoji-default base, no VS16 → unchanged
    assert status_constants._safe_emoji("\U0001f4d6") == "\U0001f4d6"


def test_emoji_or_ascii_routes_through_safe_emoji_in_emoji_mode(monkeypatch):
    from overcode import status_constants
    monkeypatch.setattr(status_constants, "_FULL_COLOR_EMOJI", False)
    out = status_constants.emoji_or_ascii("\U0001f5a5️", emoji_free=False)
    assert "️" not in out


def test_emoji_or_ascii_returns_ascii_fallback_in_emoji_free_mode():
    from overcode import status_constants
    # 🖥️ has a known ASCII fallback in EMOJI_ASCII
    out = status_constants.emoji_or_ascii("\U0001f5a5️", emoji_free=True)
    assert out == status_constants.EMOJI_ASCII["\U0001f5a5️"]


def test_detection_respects_explicit_override(monkeypatch):
    from overcode.status_constants import _detect_terminal_emoji_support
    monkeypatch.setenv("OVERCODE_EMOJI_PRESENTATION", "color")
    monkeypatch.setenv("WT_SESSION", "1")  # would normally force False
    assert _detect_terminal_emoji_support() is True

    monkeypatch.setenv("OVERCODE_EMOJI_PRESENTATION", "text")
    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")  # would normally be True
    assert _detect_terminal_emoji_support() is False


def test_detection_whitelist_iterm_yes_wt_no(monkeypatch):
    from overcode.status_constants import _detect_terminal_emoji_support
    monkeypatch.delenv("OVERCODE_EMOJI_PRESENTATION", raising=False)
    monkeypatch.delenv("WT_SESSION", raising=False)
    monkeypatch.delenv("KONSOLE_VERSION", raising=False)
    monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)
    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
    assert _detect_terminal_emoji_support() is True

    monkeypatch.setenv("TERM_PROGRAM", "")
    monkeypatch.setenv("WT_SESSION", "1")
    assert _detect_terminal_emoji_support() is False

    monkeypatch.delenv("WT_SESSION", raising=False)
    monkeypatch.setenv("KONSOLE_VERSION", "240800")
    assert _detect_terminal_emoji_support() is False
