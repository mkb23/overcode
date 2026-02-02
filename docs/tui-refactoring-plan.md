# TUI Refactoring Plan

## Current State

- **File size**: 3,532 lines
- **Test coverage**: 53% (882 uncovered lines)
- **Main classes**:
  - `DaemonStatusBar` (188 lines) - Status bar rendering
  - `StatusTimeline` (198 lines) - Timeline visualization
  - `HelpOverlay` (58 lines) - Help display
  - `DaemonPanel` (132 lines) - Daemon log panel
  - `SessionSummary` (429 lines) - Agent session widget
  - `PreviewPane` (45 lines) - Preview pane
  - `CommandBar` (358 lines) - Input handling
  - `SupervisorTUI` (2,009 lines) - Main app class

## Testing Challenges

1. **Tight coupling to Textual framework** - Widgets inherit from Textual classes
2. **Complex render methods** - Rich text building mixed with business logic
3. **Async operations** - Background workers and timers
4. **External dependencies** - tmux, file system, session manager
5. **State management** - Reactive properties tied to UI updates

## Refactoring Strategy

### Phase 1: Extract Pure Functions (Low Risk, High Impact)

Extract render logic into pure functions that can be tested without Textual:

```python
# New file: src/overcode/tui_render.py

def render_daemon_status_bar(
    monitor_state: Optional[MonitorDaemonState],
    supervisor_running: bool,
    summarizer_available: bool,
    summarizer_enabled: bool,
    summarizer_calls: int,
    asleep_session_ids: set,
    web_running: bool,
    web_url: Optional[str],
) -> Text:
    """Build Rich Text for daemon status bar.

    Pure function - no side effects, easy to test.
    """
    content = Text()
    # ... all the rendering logic from DaemonStatusBar.render()
    return content


def render_session_summary(
    session: Session,
    detected_status: str,
    expanded: bool,
    summary_detail: str,
    claude_stats: Optional[ClaudeSessionStats],
    git_diff_stats: Optional[tuple],
    is_unvisited_stalled: bool,
    has_focus: bool,
    is_list_mode: bool,
    max_repo_info_width: int = 18,
) -> Text:
    """Build Rich Text for session summary line.

    Pure function - no side effects, easy to test.
    """
    # ... all the rendering logic from SessionSummary.render()


def render_timeline(
    presence_history: list,
    agent_history: dict,
    hours: float,
    timeline_width: int,
    label_width: int,
) -> Text:
    """Build Rich Text for status timeline.

    Pure function - easy to test with sample data.
    """
```

**Tests for these functions:**
```python
def test_render_daemon_status_bar_monitor_running():
    state = MonitorDaemonState(status="running", loop_count=42)
    result = render_daemon_status_bar(
        monitor_state=state,
        supervisor_running=True,
        # ... other params
    )
    assert "Monitor: ●" in result.plain
    assert "#42" in result.plain

def test_render_session_summary_expanded():
    session = Session(name="test-agent", ...)
    result = render_session_summary(
        session=session,
        expanded=True,
        summary_detail="full",
        # ... other params
    )
    assert "test-agent" in result.plain
    assert "▼" in result.plain  # expanded indicator
```

### Phase 2: Extract Business Logic (Medium Risk)

Move sorting, filtering, and data transformation to testable modules:

```python
# New file: src/overcode/tui_logic.py

def sort_sessions(
    sessions: List[Session],
    sort_mode: str,  # "alphabetical", "by_status", "by_value"
) -> List[Session]:
    """Sort sessions based on mode.

    Pure function - returns new sorted list.
    """
    if sort_mode == "alphabetical":
        return sorted(sessions, key=lambda s: s.name.lower())
    elif sort_mode == "by_status":
        status_order = {...}
        return sorted(sessions, key=lambda s: (
            status_order.get(s.stats.current_state or "running", 4),
            s.name.lower()
        ))
    # ... etc


def filter_visible_sessions(
    sessions: List[Session],
    show_terminated: bool,
    hide_asleep: bool,
    asleep_session_ids: set,
) -> List[Session]:
    """Filter sessions based on visibility preferences."""


def calculate_spin_stats(
    sessions: List[SessionDaemonState],
    asleep_session_ids: set,
) -> dict:
    """Calculate spin rate statistics.

    Returns dict with green_count, total_count, mean_spin, total_tokens.
    """
```

### Phase 3: Split Widget Files (Medium Risk)

Split into multiple files as the TODO suggests:

```
src/overcode/
├── tui.py                 # Main SupervisorTUI app (reduced)
├── tui_render.py          # Pure render functions
├── tui_logic.py           # Business logic functions
├── tui_widgets/
│   ├── __init__.py
│   ├── daemon_status.py   # DaemonStatusBar
│   ├── timeline.py        # StatusTimeline
│   ├── session.py         # SessionSummary
│   ├── command_bar.py     # CommandBar
│   ├── preview.py         # PreviewPane
│   └── overlays.py        # HelpOverlay, DaemonPanel
```

### Phase 4: Dependency Injection (Higher Risk)

Modify widgets to accept dependencies as constructor parameters:

```python
class SessionSummary(Static, can_focus=True):
    def __init__(
        self,
        session: Session,
        status_detector: StatusDetector,
        # New: inject render function for testability
        render_fn: Callable = None,
        *args, **kwargs
    ):
        self._render_fn = render_fn or render_session_summary
        # ...

    def render(self) -> Text:
        return self._render_fn(
            session=self.session,
            detected_status=self.detected_status,
            expanded=self.expanded,
            # ... pass all state
        )
```

### Phase 5: Extract Action Handlers (Higher Risk)

Group related actions into handler classes:

```python
# src/overcode/tui_actions/daemon.py
class DaemonActions:
    """Handlers for daemon-related actions."""

    def __init__(self, app: "SupervisorTUI"):
        self.app = app

    def toggle_daemon(self) -> None:
        """Toggle monitor daemon on/off."""
        # ... logic from action_toggle_daemon

    def supervisor_start(self) -> None:
        # ...

    def supervisor_stop(self) -> None:
        # ...

# In SupervisorTUI:
class SupervisorTUI(App):
    def __init__(self, ...):
        self._daemon_actions = DaemonActions(self)

    def action_toggle_daemon(self) -> None:
        self._daemon_actions.toggle_daemon()
```

## Implementation Priority

| Priority | Task | Risk | Lines Testable | Effort | Status |
|----------|------|------|----------------|--------|--------|
| 1 | Extract render functions to `tui_render.py` | Low | ~400 | Medium | ✅ Done |
| 2 | Extract business logic to `tui_logic.py` | Low | ~150 | Low | ✅ Done |
| 3 | Add tests for extracted functions | None | - | Medium | ✅ Done |
| 4 | Split widget files | Medium | - | High | ✅ Done |
| 5 | Extract CSS to external file | Low | - | Low | ✅ Done |
| 6 | Extract action handlers to mixins | Medium | - | Medium | ✅ Done |
| 7 | Add dependency injection | Medium | ~100 | Medium | Skipped |

## Progress

**Completed (2025-01-29):**

Phase 1-3 Complete:
- Created `src/overcode/tui_render.py` with 6 pure render functions (165 lines, 95% coverage)
- Created `src/overcode/tui_logic.py` with sorting, filtering, and calculation functions (73 lines, 100% coverage)
- Created `tests/unit/test_tui_helpers.py` with 67 tests (100% coverage)
- Created `tests/unit/test_tui_logic.py` with 43 tests
- Created `tests/unit/test_tui_render.py` with 25 tests
- Integrated `tui_logic.py` into `tui.py` (replaced `_sort_sessions`, `action_cycle_sort_mode`, `update_session_widgets`)

**Final TUI Module Coverage:**
| Module | Coverage |
|--------|----------|
| tui.py | 44% |
| tui_helpers.py | 100% |
| tui_logic.py | 100% |
| tui_render.py | 95% |

**Test Count:** 839 → 1,028 (+189 tests)
**Overall Coverage:** 58% → 60%

**Phase 4 Complete (Widget Extraction):**
Created `src/overcode/tui_widgets/` package with 7 widget modules:
- `help_overlay.py` - Help overlay widget (68 lines)
- `preview_pane.py` - Preview pane for list+preview mode (61 lines)
- `daemon_panel.py` - Daemon log panel (153 lines)
- `daemon_status_bar.py` - Status bar with daemon metrics (217 lines)
- `status_timeline.py` - Historical status timeline (222 lines)
- `session_summary.py` - Expandable session summary widget (478 lines)
- `command_bar.py` - Command input bar (373 lines)

Updated `tui.py` to import widgets from `tui_widgets` package.

**Phase 5 Complete (CSS Extraction):**
- Created `src/overcode/tui.tcss` with 201 lines of external CSS
- Updated `SupervisorTUI` to use `CSS_PATH = "tui.tcss"`

**Phase 6 Complete (Action Mixins):**
Created `src/overcode/tui_actions/` package with 5 mixin modules:
- `navigation.py` - Navigation actions (106 lines)
- `view.py` - View/display actions (247 lines)
- `daemon.py` - Daemon control actions (182 lines)
- `session.py` - Session/agent actions (233 lines)
- `input.py` - Input sending actions (128 lines)

Updated `SupervisorTUI` to inherit from mixins:
```python
class SupervisorTUI(
    NavigationActionsMixin,
    ViewActionsMixin,
    DaemonActionsMixin,
    SessionActionsMixin,
    InputActionsMixin,
    App,
):
```

**Final File Sizes:**
| File | Lines |
|------|-------|
| tui.py | 1,107 |
| tui.tcss | 201 |
| tui_actions/ (5 files) | 916 |
| tui_widgets/ (7 files) | 1,596 |
| tui_helpers.py | 190 |
| tui_logic.py | 73 |
| tui_render.py | 165 |
| **Total** | **4,248** |

Original `tui.py` was 3,502 lines. Now the largest file is 1,107 lines.

**Progress Summary:**
- Removed 1,439 lines of duplicate widget definitions
- `tui.py` reduced from 3,502 to 2,063 lines (41% reduction)
- Total widget code in `tui_widgets/`: 1,596 lines

## Expected Coverage Improvement

- **Phase 1-2**: +8-10% coverage (extracting ~500 lines of testable logic)
- **Phase 3-4**: +3-5% additional (better widget isolation)
- **Total potential**: 65-68% overall coverage

## Risks and Mitigations

1. **Breaking existing functionality**
   - Mitigation: Extract functions first without changing widget behavior
   - Add integration tests before refactoring

2. **Performance regression**
   - Mitigation: Pure functions should be fast
   - Profile render functions if needed

3. **Increased complexity from indirection**
   - Mitigation: Clear module boundaries
   - Good documentation of module responsibilities

## Quick Wins

1. ✅ **Extract `_sort_sessions`** - Done in `tui_logic.py` with full test coverage
2. **Add tests for existing `tui_helpers.py` functions** - Currently 83% → aim for 100%
3. **Extract format_standing_instructions** - Already at module level, add tests

## Next Steps

1. ✅ ~~**Integrate new modules**: Update `tui.py` to import and use functions from `tui_render.py` and `tui_logic.py`~~
2. ✅ ~~**Add remaining tui_helpers tests**: Cover the 17% of uncovered helper functions~~ (now 100%)
3. ✅ ~~**Split widget files** (Phase 4): Move each widget class to its own file in `tui_widgets/`~~
4. **Add widget tests**: Create unit tests for extracted widgets in `tui_widgets/`
5. **Phase 5**: Add dependency injection to widgets for better testability
6. **Phase 6**: Extract action handlers from SupervisorTUI
