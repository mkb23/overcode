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


# Wide astral-plane ranges from @xterm/addon-unicode11, the width table
# VSCode's terminal uses (terminal.integrated.unicodeVersion only offers
# "6" or "11"). Emoji outside these (Unicode 13+: 🪨 🪝 🪛 ...) take one cell
# in VSCode but two in Rich and tmux, so every column after one drifts,
# flickering as tmux redraws parts of the line (#495, #504).
_XTERM_U11_WIDE_ASTRAL = [
    (0x1F004, 0x1F004),
    (0x1F0CF, 0x1F0CF),
    (0x1F18E, 0x1F18E),
    (0x1F191, 0x1F19A),
    (0x1F200, 0x1F202),
    (0x1F210, 0x1F23B),
    (0x1F240, 0x1F248),
    (0x1F250, 0x1F251),
    (0x1F260, 0x1F265),
    (0x1F300, 0x1F320),
    (0x1F32D, 0x1F335),
    (0x1F337, 0x1F37C),
    (0x1F37E, 0x1F393),
    (0x1F3A0, 0x1F3CA),
    (0x1F3CF, 0x1F3D3),
    (0x1F3E0, 0x1F3F0),
    (0x1F3F4, 0x1F3F4),
    (0x1F3F8, 0x1F43E),
    (0x1F440, 0x1F440),
    (0x1F442, 0x1F4FC),
    (0x1F4FF, 0x1F53D),
    (0x1F54B, 0x1F54E),
    (0x1F550, 0x1F567),
    (0x1F57A, 0x1F57A),
    (0x1F595, 0x1F596),
    (0x1F5A4, 0x1F5A4),
    (0x1F5FB, 0x1F64F),
    (0x1F680, 0x1F6C5),
    (0x1F6CC, 0x1F6CC),
    (0x1F6D0, 0x1F6D2),
    (0x1F6D5, 0x1F6D5),
    (0x1F6EB, 0x1F6EC),
    (0x1F6F4, 0x1F6FA),
    (0x1F7E0, 0x1F7EB),
    (0x1F90D, 0x1F971),
    (0x1F973, 0x1F976),
    (0x1F97A, 0x1F9A2),
    (0x1F9A5, 0x1F9AA),
    (0x1F9AE, 0x1F9CA),
    (0x1F9CD, 0x1F9FF),
    (0x1FA70, 0x1FA73),
    (0x1FA78, 0x1FA7A),
    (0x1FA80, 0x1FA82),
    (0x1FA90, 0x1FA95),
]


def test_no_emoji_too_new_for_vscode_terminal():
    import pathlib
    from rich.cells import cell_len
    import overcode

    def vscode_wide(cp: int) -> bool:
        return any(a <= cp <= b for a, b in _XTERM_U11_WIDE_ASTRAL)

    root = pathlib.Path(overcode.__file__).parent
    bad = []
    for path in root.rglob("*.py"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for ch in set(line):
                cp = ord(ch)
                if 0x1F000 <= cp < 0x20000 and cell_len(ch) == 2 and not vscode_wide(cp):
                    bad.append(f"{path.relative_to(root)}:{lineno} {ch} U+{cp:04X}")
    assert not bad, "emoji VSCode renders one cell wide:\n" + "\n".join(bad)
