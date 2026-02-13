"""
Test fixtures and factories for overcode unit tests.

This module provides factory functions for creating mock objects
and test data without requiring external dependencies.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional
from uuid import uuid4


def create_mock_session(
    id: str = None,
    name: str = "test-session",
    tmux_session: str = "agents",
    tmux_window: int = 1,
    command: List[str] = None,
    start_directory: Optional[str] = None,
    start_time: str = None,
    repo_name: Optional[str] = None,
    branch: Optional[str] = None,
    status: str = "running",
    permissiveness_mode: str = "normal",
    standing_instructions: str = "",
    standing_orders_complete: bool = False,
    stats: dict = None,
) -> "MockSession":
    """Create a mock Session object for testing.

    Returns a MockSession that has all the same attributes as a real Session
    but doesn't require the actual module import.
    """
    return MockSession(
        id=id or str(uuid4()),
        name=name,
        tmux_session=tmux_session,
        tmux_window=tmux_window,
        command=command or ["claude", "code"],
        start_directory=start_directory,
        start_time=start_time or datetime.now().isoformat(),
        repo_name=repo_name,
        branch=branch,
        status=status,
        permissiveness_mode=permissiveness_mode,
        standing_instructions=standing_instructions,
        standing_orders_complete=standing_orders_complete,
        stats=MockSessionStats(**(stats or {})),
    )


@dataclass
class MockSessionStats:
    """Mock SessionStats for testing"""
    interaction_count: int = 0
    estimated_cost_usd: float = 0.0
    total_tokens: int = 0
    operation_times: List[float] = field(default_factory=list)
    steers_count: int = 0
    last_activity: Optional[str] = None
    current_task: str = "Initializing..."
    current_state: str = "running"
    state_since: Optional[str] = None
    green_time_seconds: float = 0.0
    non_green_time_seconds: float = 0.0


@dataclass
class MockSession:
    """Mock Session for testing without requiring the actual dataclass"""
    id: str
    name: str
    tmux_session: str
    tmux_window: int
    command: List[str]
    start_directory: Optional[str]
    start_time: str
    repo_name: Optional[str] = None
    branch: Optional[str] = None
    status: str = "running"
    permissiveness_mode: str = "normal"
    standing_instructions: str = ""
    standing_orders_complete: bool = False
    stats: MockSessionStats = field(default_factory=MockSessionStats)
    hook_status_detection: bool = False


# =============================================================================
# Sample pane content for testing StatusDetector
# =============================================================================

PANE_CONTENT_WAITING_USER = """
Some previous output from Claude...

⏺ I've finished analyzing the code. Here's what I found:

  1. The main function looks good
  2. Tests are passing

  Would you like me to make any changes?

──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
>
──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  ? for shortcuts
"""

PANE_CONTENT_PERMISSION_PROMPT = """
⏺ I need to write to this file.

  Allow Write to /path/to/file.py?

  Enter to confirm, Esc to reject
"""

PANE_CONTENT_RUNNING_WITH_SPINNER = """
⏺ Searching the codebase...

  ✽ web search for "python dataclass"

  Esc to interrupt
"""

PANE_CONTENT_RUNNING_WITH_TOOL = """
⏺ Reading the configuration file...

  Reading config.json...
"""

PANE_CONTENT_STALLED = """
⏺ Here are 5 jokes:

  1. Why did the chicken cross the road?
  2. Knock knock...

──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
>\xa0Tell me more jokes about programming!
──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  ⏵⏵ don't ask on (shift+tab to cycle)
"""

PANE_CONTENT_ACTIVE_STREAMING = """
⏺ Let me explain how this works:

  First, we need to understand that Python uses
  dynamic typing which means variables don't have
"""

PANE_CONTENT_NO_OUTPUT = """
"""

PANE_CONTENT_THINKING = """
⏺ Let me think about this...

  thinking...
"""

# Bug reproduction: permission prompt with "Web Search" text that falsely matches
# "web search" active indicator
PANE_CONTENT_WEB_SEARCH_PERMISSION = """
⏺ I'll research the best hiking trails across the USA by region. Let me search
  for information on trails in different areas.

⏺ Web Search("best hiking trails West Coast California Oregon 2024")

⏺ Web Search("best hiking trails Pacific Northwest Washington state 2024")

────────────────────────────────────────────────────────────────────────────────
 Tool use

   Web Search("best hiking trails West Coast California Oregon 2024")
   Claude wants to search the web for: best hiking trails West Coast
   California Oregon 2024

 Do you want to proceed?
 ❯ 1. Yes
   2. Yes, and don't ask again for Web Search commands in
      /home/user/myproject
   3. No, and tell Claude what to do differently (esc)
"""

# Similar bug: Bash command permission prompt
PANE_CONTENT_BASH_PERMISSION = """
⏺ Let me run the tests to verify the changes.

⏺ Bash("pytest tests/ -v")

────────────────────────────────────────────────────────────────────────────────
 Tool use

   Bash("pytest tests/ -v")
   Claude wants to run a bash command: pytest tests/ -v

 Do you want to proceed?
 ❯ 1. Yes
   2. Yes, and don't ask again for Bash commands in
      /home/user/myproject
   3. No, and tell Claude what to do differently (esc)
"""

# Bug reproduction: Read permission prompt
PANE_CONTENT_READ_PERMISSION = """
⏺ Let me look at the configuration file.

⏺ Read("config.json")

────────────────────────────────────────────────────────────────────────────────
 Tool use

   Read("config.json")
   Claude wants to read: config.json

 Do you want to proceed?
 ❯ 1. Yes
   2. Yes, and don't ask again for Read commands in
      /home/user/myproject
   3. No, and tell Claude what to do differently (esc)
"""

# Bug reproduction: Autocomplete suggestion showing "↵ send" at end
# This should NOT be detected as "stalled - no response to user input"
PANE_CONTENT_AUTOCOMPLETE_SUGGESTION = """
⏺ Write(supervisor-test2.md)
  ⎿  Wrote 2 lines to supervisor-test2.md
     Second test worked!

⏺ Created supervisor-test2.md with the text "Second test worked!"

────────────────────────────────────────────────────────────────────────────────
> delete both test files                                                                                 ↵ send
────────────────────────────────────────────────────────────────────────────────
  ? for shortcuts
"""


# =============================================================================
# Helper functions
# =============================================================================

def create_mock_tmux_with_content(session: str, window: int, content: str):
    """Create a MockTmux with pre-configured pane content.

    Usage:
        mock_tmux = create_mock_tmux_with_content("agents", 1, PANE_CONTENT_WAITING_USER)
        detector = StatusDetector("agents", tmux=mock_tmux)
    """
    from overcode.interfaces import MockTmux

    mock = MockTmux()
    mock.new_session(session)
    mock.sessions[session][window] = content
    return mock

# =============================================================================
# Error state fixtures (#216)
# Real Claude Code error output formats
# =============================================================================

# API Error with retry (529 overloaded)
PANE_CONTENT_ERROR_API_OVERLOADED = """
⏺ Let me read that file for you.

⏺ Read("src/main.py")
  ⎿ API Error (529 {"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}) · Retrying in 1 seconds… (attempt 1/10)
  ⎿ API Error (529 {"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}) · Retrying in 2 seconds… (attempt 2/10)
  ⎿ API Error (529 {"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}) · Retrying in 4 seconds… (attempt 3/10)
"""

# API Error with retry (request timeout)
PANE_CONTENT_ERROR_TIMEOUT = """
⏺ Let me analyze that code.

  ⎿ API Error (Request timed out.) · Retrying in 1 seconds… (attempt 1/10)
  ⎿ API Error (Request timed out.) · Retrying in 2 seconds… (attempt 2/10)
"""

# Final error after retries exhausted
PANE_CONTENT_ERROR_FINAL = """
⏺ Let me check the API.

  ⎿ API Error: 503 no healthy upstream
"""

# Connection error with TypeError
PANE_CONTENT_ERROR_CONNECTION = """
⏺ Searching for relevant files...

  ⎿ API Error (Connection error.) · Retrying in 1 seconds… (attempt 1/10)
  ⎿ TypeError (fetch failed)
"""

# ECONNRESET
PANE_CONTENT_ERROR_ECONNRESET = """
⏺ Working on the implementation...

  ⎿ Unable to connect to API (ECONNRESET)
  Retrying in 7 seconds… (attempt 5/10)
"""

# Rate limit banner
PANE_CONTENT_ERROR_RATE_LIMIT = """
⏺ Here's my analysis of the code...

You've hit your limit · resets 4pm (Europe/Berlin)
"""

# Auth error
PANE_CONTENT_ERROR_AUTH = """
Invalid API key · Please run /login
"""

# FALSE POSITIVE: Claude's response text discusses errors (#216)
# These should NOT trigger error status
PANE_CONTENT_NARRATIVE_ERRORS = """
⏺ Here's my analysis of the error handling:

  The API error handling code catches timeout exceptions and retries
  with exponential backoff. When a 429 rate limit or 503 service
  unavailable response is received, the connection failed handler
  kicks in. The internal server error path is also covered.

  The overloaded endpoint returns a "service unavailable" message.

──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
>
──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  ? for shortcuts
"""

# FALSE POSITIVE: Claude discussing error patterns in code (#216)
# The exact scenario from the bug report - discussing error detection
PANE_CONTENT_NARRATIVE_ERROR_PATTERNS = """
  - "Two tests failed due to connection timeout handling"  — matches timeout
  - "Fixed the API error handling in..."  — matches api.*error
  - "The server returned a 429 status code"  — matches 429
  - "Let me check the rate limit configuration"  — matches rate.*limit

  The detection waterfall hits the error check at line 145 before
  reaching the content-change check, so it returns STATUS_ERROR (purple)

✽ Cooked for 3m 9s

>
⏵⏵ bypass permissions on (shift+tab to cycle)
"""


# Spawn failure: claude command not found (bash style)
# Plan mode: idle session — captured from real `claude --permission-mode plan`
# "plan mode" appears in the ⏸ status bar line, not the banner
PANE_CONTENT_PLAN_MODE_IDLE = """
           Claude Code v2.1.41
 ▐▛███▜▌   Opus 4.6 · Claude Max
▝▜█████▛▘  ~/Code/overcode2
  ▘▘ ▝▝    Opus 4.6 is here

────────────────────────────────────────────────────────────────────
❯
────────────────────────────────────────────────────────────────────
  ⏸ plan mode on (shift+tab to cycle) · PR #257
"""

# Plan approval: Claude has produced a plan and is waiting for user to approve
PANE_CONTENT_PLAN_APPROVAL = """
⏺ I've analyzed the codebase and written my plan. Please review the plan
  and let me know if you'd like to approve this plan or make changes.

────────────────────────────────────────────────────────────────────
❯
────────────────────────────────────────────────────────────────────
  ⏸ plan mode on (shift+tab to cycle)
"""

PANE_CONTENT_SPAWN_FAILED_BASH = """
Last login: Thu Jan 23 10:00:00 on ttys001
user@hostname ~ % claude code
bash: claude: command not found
user@hostname ~ %
"""

# Spawn failure: claude command not found (zsh style)
PANE_CONTENT_SPAWN_FAILED_ZSH = """
Last login: Thu Jan 23 10:00:00 on ttys001
user@hostname ~ % claude code
zsh: command not found: claude
user@hostname ~ %
"""

# Spawn failure: permission denied
PANE_CONTENT_SPAWN_FAILED_PERMISSION = """
Last login: Thu Jan 23 10:00:00 on ttys001
user@hostname ~ % claude code
bash: /usr/local/bin/claude: Permission denied
user@hostname ~ %
"""
