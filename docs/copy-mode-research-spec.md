# Deep Research Spec: Textual TUI Copy Mode / Mouse Capture Toggle

## Problem Statement

In a Textual-based TUI application, we need to implement a "copy mode" that temporarily disables mouse capture so users can:
1. Select text with native terminal mouse selection
2. Copy text using Cmd+C (macOS) or Ctrl+C (Linux)
3. Press a key to re-enable TUI mouse handling

Currently, pressing 'y' triggers the copy mode toggle, but text selection and copying don't work as expected in macOS Terminal.

## Environment

- **Framework**: Textual (Python TUI framework built on Rich)
- **Primary Terminal**: macOS Terminal.app
- **Secondary Terminals**: iTerm2, VS Code integrated terminal
- **Python Version**: 3.12
- **Textual Version**: Latest (as of Jan 2025)

## Technical Background

### How Textual Handles Mouse Events

Textual uses escape sequences to enable mouse tracking. From inspecting the source code:

```python
# From textual.drivers.linux_driver.LinuxDriver

def _enable_mouse_support(self) -> None:
    """Enable reporting of mouse events."""
    if not self._mouse:
        return
    write = self.write
    write("\x1b[?1000h")  # SET_VT200_MOUSE
    write("\x1b[?1003h")  # SET_ANY_EVENT_MOUSE
    write("\x1b[?1015h")  # SET_VT200_HIGHLIGHT_MOUSE
    write("\x1b[?1006h")  # SET_SGR_EXT_MODE_MOUSE
    self.flush()

def _disable_mouse_support(self) -> None:
    """Disable reporting of mouse events."""
    if not self._mouse:
        return
    write = self.write
    write("\x1b[?1000l")
    write("\x1b[?1003l")
    write("\x1b[?1015l")
    write("\x1b[?1006l")
    self.flush()
```

Key findings:
- The `_mouse` flag is set in the base `Driver.__init__()` and defaults to `True`
- Both enable/disable methods check `if not self._mouse: return` - they bail early if the flag is False
- `start_application_mode()` does NOT accept a `mouse` parameter (contrary to what we initially tried)
- `start_application_mode()` calls `_enable_mouse_support()` TWICE (once early, once at the end)
- The driver writes to `self._file` which is `sys.__stderr__`

### Mouse Tracking Escape Sequences

| Sequence | Enable | Disable | Description |
|----------|--------|---------|-------------|
| 1000 | `\x1b[?1000h` | `\x1b[?1000l` | Basic mouse tracking (clicks) |
| 1002 | `\x1b[?1002h` | `\x1b[?1002l` | Cell motion tracking |
| 1003 | `\x1b[?1003h` | `\x1b[?1003l` | All motion tracking |
| 1006 | `\x1b[?1006h` | `\x1b[?1006l` | SGR extended mode |
| 1015 | `\x1b[?1015h` | `\x1b[?1015l` | urxvt extended mode |

## Experiments Conducted

### Experiment 1: stop/start_application_mode with mouse parameter

**Approach:**
```python
def action_toggle_copy_mode(self) -> None:
    self._copy_mode = not self._copy_mode
    if self._copy_mode:
        self._driver.stop_application_mode()
        self._driver.start_application_mode(mouse=False)  # WRONG: no mouse param
    else:
        self._driver.stop_application_mode()
        self._driver.start_application_mode(mouse=True)
```

**Result:** Failed. `start_application_mode()` doesn't accept a `mouse` parameter. The method signature is just `start_application_mode(self)`.

### Experiment 2: Direct escape sequences to sys.stdout

**Approach:**
```python
if self._copy_mode:
    sys.stdout.write("\x1b[?1000l")
    sys.stdout.write("\x1b[?1003l")
    # etc...
    sys.stdout.flush()
```

**Result:** Failed. Textual redirects stdout, so escape sequences don't reach the terminal.

### Experiment 3: Direct escape sequences to driver's file (stderr)

**Approach:**
```python
if self._copy_mode:
    driver_file = self._driver._file  # This is sys.__stderr__
    driver_file.write("\x1b[?1000l")
    driver_file.write("\x1b[?1002l")
    driver_file.write("\x1b[?1003l")
    driver_file.write("\x1b[?1015l")
    driver_file.write("\x1b[?1006l")
    driver_file.flush()
```

**Result:** Partial success. The escape sequences are sent, the notification shows "COPY MODE", but mouse selection still doesn't work reliably in macOS Terminal. Need more testing to determine if this is a Terminal.app limitation.

### Experiment 4: Calling _disable_mouse_support() directly

**Approach:**
```python
self._driver._mouse = True  # Force flag to True so method doesn't bail
self._driver._disable_mouse_support()
```

**Result:** Should work in theory, but untested whether Textual re-enables mouse on refresh cycles.

## Current Implementation

```python
def action_toggle_copy_mode(self) -> None:
    if not hasattr(self, '_copy_mode'):
        self._copy_mode = False

    self._copy_mode = not self._copy_mode

    if self._copy_mode:
        driver_file = self._driver._file
        driver_file.write("\x1b[?1000l")
        driver_file.write("\x1b[?1002l")
        driver_file.write("\x1b[?1003l")
        driver_file.write("\x1b[?1015l")
        driver_file.write("\x1b[?1006l")
        driver_file.flush()
        self.notify("COPY MODE - select with mouse, Cmd+C to copy, 'y' to exit")
    else:
        self._driver._mouse = True
        self._driver._enable_mouse_support()
        self.refresh()
        self.notify("Copy mode OFF")
```

## Known Issues & Observations

1. **TUI keys still work in copy mode** - This is expected; we only disable mouse, not keyboard input

2. **Cmd+C produces a "pip" noise** - This suggests the terminal is still in some capture mode, or the app is intercepting the signal

3. **Text cannot be selected with mouse** - The core problem; escape sequences may not be reaching the terminal or Terminal.app may not support them properly

4. **Possible re-enable on refresh** - Textual's internal refresh/redraw cycles might call `_enable_mouse_support()` again

## Research Questions

1. **How do other Textual applications handle copy/paste?**
   - Are there any Textual apps that successfully implement a copy mode?
   - What patterns do they use?

2. **What is macOS Terminal.app's support for mouse escape sequences?**
   - Does Terminal.app support disabling mouse tracking via escape sequences?
   - Is there a different sequence needed for Terminal.app?
   - Does iTerm2 handle this differently?

3. **Is there a Textual API for temporarily disabling mouse capture?**
   - Any undocumented methods?
   - Any planned features for this use case?
   - GitHub issues discussing this problem?

4. **How does tmux affect mouse escape sequences?**
   - Our TUI runs inside tmux sessions
   - Does tmux intercept or modify mouse escape sequences?
   - Is there a tmux setting that affects this?

5. **Alternative approaches:**
   - **OSC 52**: Can we use OSC 52 escape sequence to copy directly to clipboard? (Note: macOS Terminal doesn't support OSC 52)
   - **Copy dialog**: Would a modal dialog showing copyable text work better?
   - **Textual's Clipboard**: Does Textual have any clipboard integration?
   - **pbcopy**: Can we pipe selected text to pbcopy on macOS?

6. **Textual driver internals:**
   - Is there a way to completely suspend the driver temporarily?
   - Can we unhook the mouse event handlers without stopping application mode?
   - What happens if we set `self._driver._mouse = False` before calling disable?

## Potential Solutions to Investigate

### Solution A: Find the correct Textual API
Research if there's an official or semi-official way to toggle mouse capture in Textual.

### Solution B: Terminal-specific escape sequences
Research if different terminals need different escape sequences. Test on:
- macOS Terminal.app
- iTerm2
- Alacritty
- VS Code integrated terminal

### Solution C: Copy dialog approach
Instead of disabling mouse capture, show a modal with the text content that can be selected and copied using standard UI mechanisms.

### Solution D: OSC 52 clipboard integration
Use OSC 52 to write directly to clipboard (works in iTerm2, not Terminal.app):
```
\x1b]52;c;BASE64_ENCODED_TEXT\x07
```

### Solution E: External clipboard tool
When user wants to copy, capture the visible text and pipe it to `pbcopy` (macOS) or `xclip` (Linux).

### Solution F: Hold modifier key
Some terminals allow holding Option/Alt while clicking to bypass mouse capture. Document this as a workaround.

## References

- [Textual GitHub](https://github.com/Textualize/textual)
- [Textual Documentation](https://textual.textualize.io/)
- [XTerm Control Sequences](https://invisible-island.net/xterm/ctlseqs/ctlseqs.html)
- [ANSI Escape Codes](https://en.wikipedia.org/wiki/ANSI_escape_code)

## Success Criteria

A successful solution should:
1. Allow users to select text with the mouse when copy mode is enabled
2. Allow Cmd+C/Ctrl+C to copy selected text to clipboard
3. Work in at least iTerm2 (macOS Terminal.app support is a nice-to-have)
4. Not break other TUI functionality
5. Be toggleable with a single keypress
6. Provide clear visual feedback about the current mode
