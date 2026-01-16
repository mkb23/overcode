"""
Realistic full-capture test fixtures for StatusDetector.

These fixtures represent complete tmux pane captures as they actually appear,
including the Claude Code welcome banner, multiple interactions, and full
UI chrome. This provides more realistic testing than minimal snippets.

Captured from real Claude Code v2.0.75 sessions.
"""

# =============================================================================
# Full capture: /exit command with menu displayed
# Expected: WAITING_USER (agent typed /exit, menu is showing, waiting for input)
# Bug: Was detected as RUNNING because menu lines pushed prompt out of view
# =============================================================================

REALISTIC_EXIT_COMMAND_MENU = """\
> Thanks for the thorough research! The Wales Bonner Superstar recommendation seems spot on - it hits the budget, the
brown trend, and feels like a proper gift. I think we're good here. You can wrap up.

⏺ Glad that landed well. Quick summary for the purchase:

  Wales Bonner x Adidas Superstar
  - Price: £240
  - UK Size 5: In stock
  - Link: https://example.com/product/12345

  Great choice - the brown leather, satin lining, and gold details make it feel genuinely special. Hope your friend loves t
hem!

> Perfect, thanks!

⏺ You're welcome - happy birthday to your friend!

───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
>\xa0/exit

───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  /exit            Exit the REPL
  /extra-usage     Configure extra usage to keep working when limits are hit
  /context         Visualize current context usage as a colored grid
  /memory          Edit Claude memory files
  /vim             Toggle between Vim and Normal editing modes
  /clear           Clear conversation history and free up context
  /compact         Clear conversation history but keep a summary in context. Optional: /compact [instructions for
                   summarization]
"""

# =============================================================================
# Full capture: Idle with autocomplete suggestion showing
# Expected: WAITING_USER (agent is idle, waiting for input)
# =============================================================================

REALISTIC_AUTOCOMPLETE_IDLE = """\
╭─── Claude Code v2.0.75 ──────────────────────────────────────────────────────╮
│                                                    │ Recent activity         │
│                 Welcome back!                 │ 10m ago  Create a file… │
│                                                    │ 33m ago  Research 3 gr… │
│                                                    │ 34m ago  Research 3 gr… │
│                     * ▐▛███▜▌ *                    │ /resume for more        │
│                    * ▝▜█████▛▘ *                   │ ─────────────────────── │
│                     *  ▘▘ ▝▝  *                    │ What's new              │
│                                                    │ Added LSP (Language Se… │
│     Opus 4.5 · Claude Max · user@example.com's     │ Added `/terminal-setup… │
│     Organization                                   │ Added ctrl+t shortcut … │
│             ~/hiking-research/uk-hikes             │ /release-notes for more │
╰──────────────────────────────────────────────────────────────────────────────╯

> Create a file called supervisor-test2.md with the text 'Second test
worked\\!'

⏺ Write(supervisor-test2.md)
  ⎿  Wrote 2 lines to supervisor-test2.md
     Second test worked!

⏺ Created supervisor-test2.md with the text "Second test worked!"

────────────────────────────────────────────────────────────────────────────────────────────────────────────────
> delete both test files                                                                                 ↵ send
────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  ? for shortcuts
"""

# =============================================================================
# Full capture: Empty prompt waiting for input
# Expected: WAITING_USER
# =============================================================================

REALISTIC_EMPTY_PROMPT = """\
╭─── Claude Code v2.0.75 ──────────────────────────────────────────────────────╮
│                                                    │ Recent activity         │
│                 Welcome back!                 │ 5m ago   Fix the bug…   │
│                                                    │ 12m ago  Review code…   │
│                                                    │ 15m ago  Run tests…     │
│                     * ▐▛███▜▌ *                    │ /resume for more        │
│                    * ▝▜█████▛▘ *                   │ ─────────────────────── │
│                     *  ▘▘ ▝▝  *                    │ What's new              │
│                                                    │ Added LSP (Language Se… │
│     Opus 4.5 · Claude Max · user@example.com's     │ Added `/terminal-setup… │
│     Organization                                   │ Added ctrl+t shortcut … │
│             ~/Code/myproject                       │ /release-notes for more │
╰──────────────────────────────────────────────────────────────────────────────╯

> Fix the authentication bug in login.py

⏺ Read(src/auth/login.py)
  ⎿  Read 145 lines from src/auth/login.py

⏺ I found the issue. The token validation was checking expiry incorrectly.
  Let me fix it.

⏺ Edit(src/auth/login.py)
  ⎿  Updated src/auth/login.py (3 edits)

⏺ Fixed the authentication bug. The issue was that the token expiry check
  was using `<` instead of `<=`, causing tokens to be rejected one second
  too early.

────────────────────────────────────────────────────────────────────────────────
>
────────────────────────────────────────────────────────────────────────────────
  ? for shortcuts
"""

# =============================================================================
# Full capture: Permission prompt (v2 menu format)
# Expected: WAITING_USER with "Permission:" activity
# =============================================================================

REALISTIC_PERMISSION_PROMPT = """\
╭─── Claude Code v2.0.75 ──────────────────────────────────────────────────────╮
│                                                    │ Recent activity         │
│                 Welcome back!                 │ 2m ago   Search for…    │
│                                                    │ 5m ago   Read config…   │
│                                                    │ 8m ago   Analyze code…  │
│                     * ▐▛███▜▌ *                    │ /resume for more        │
│                    * ▝▜█████▛▘ *                   │ ─────────────────────── │
│                     *  ▘▘ ▝▝  *                    │ What's new              │
│                                                    │ Added LSP (Language Se… │
│     Opus 4.5 · Claude Max · user@example.com's     │ Added `/terminal-setup… │
│     Organization                                   │ Added ctrl+t shortcut … │
│             ~/Code/overcode                      │ /release-notes for more │
╰──────────────────────────────────────────────────────────────────────────────╯

> Research the best practices for Python async programming

⏺ I'll search for current best practices on Python async programming.

⏺ Web Search("Python async best practices 2024")

────────────────────────────────────────────────────────────────────────────────
 Tool use

   Web Search("Python async best practices 2024")
   Claude wants to search the web for: Python async best practices 2024

 Do you want to proceed?
 ❯ 1. Yes
   2. Yes, and don't ask again for Web Search commands in
      /home/user/myproject
   3. No, and tell Claude what to do differently (esc)
"""

# =============================================================================
# Full capture: Active tool execution (Reading)
# Expected: RUNNING with "Reading..." activity
# =============================================================================

REALISTIC_TOOL_RUNNING = """\
╭─── Claude Code v2.0.75 ──────────────────────────────────────────────────────╮
│                                                    │ Recent activity         │
│                 Welcome back!                 │ 1m ago   Analyze the…   │
│                                                    │ 3m ago   Read README…   │
│                                                    │ 5m ago   List files…    │
│                     * ▐▛███▜▌ *                    │ /resume for more        │
│                    * ▝▜█████▛▘ *                   │ ─────────────────────── │
│                     *  ▘▘ ▝▝  *                    │ What's new              │
│                                                    │ Added LSP (Language Se… │
│     Opus 4.5 · Claude Max · user@example.com's     │ Added `/terminal-setup… │
│     Organization                                   │ Added ctrl+t shortcut … │
│             ~/Code/bigproject                      │ /release-notes for more │
╰──────────────────────────────────────────────────────────────────────────────╯

> Analyze the database schema and suggest improvements

⏺ I'll analyze the database schema files to understand the current structure.

⏺ Glob("**/schema*.sql")
  ⎿  Found 3 files

⏺ Let me read through these schema files.

  Reading db/schema.sql...
  Reading db/migrations/001_initial_schema.sql...
  Reading db/migrations/002_add_indexes.sql...

  Esc to interrupt
"""

# =============================================================================
# Full capture: Spinner/thinking indicator
# Expected: RUNNING
# =============================================================================

REALISTIC_THINKING = """\
╭─── Claude Code v2.0.75 ──────────────────────────────────────────────────────╮
│                                                    │ Recent activity         │
│                 Welcome back!                 │ 30s ago  Start analy…   │
│                                                    │ 2m ago   Read config…   │
│                                                    │ 5m ago   Initialize…    │
│                     * ▐▛███▜▌ *                    │ /resume for more        │
│                    * ▝▜█████▛▘ *                   │ ─────────────────────── │
│                     *  ▘▘ ▝▝  *                    │ What's new              │
│                                                    │ Added LSP (Language Se… │
│     Opus 4.5 · Claude Max · user@example.com's     │ Added `/terminal-setup… │
│     Organization                                   │ Added ctrl+t shortcut … │
│             ~/Code/complexapp                      │ /release-notes for more │
╰──────────────────────────────────────────────────────────────────────────────╯

> Refactor the payment processing module to use the new API

⏺ I'll analyze the payment processing module to understand the current
  implementation before refactoring.

⏺ Read(src/payments/processor.py)
  ⎿  Read 523 lines from src/payments/processor.py

⏺ Read(src/payments/api_client.py)
  ⎿  Read 234 lines from src/payments/api_client.py

  ✽ thinking about refactoring approach...

  Esc to interrupt
"""

# =============================================================================
# Full capture: Web search in progress
# Expected: RUNNING (has "web search" indicator)
# =============================================================================

REALISTIC_WEB_SEARCH_RUNNING = """\
╭─── Claude Code v2.0.75 ──────────────────────────────────────────────────────╮
│                                                    │ Recent activity         │
│                 Welcome back!                 │ 10s ago  Start search…  │
│                                                    │ 1m ago   Plan researc…  │
│                                                    │ 3m ago   Read context…  │
│                     * ▐▛███▜▌ *                    │ /resume for more        │
│                    * ▝▜█████▛▘ *                   │ ─────────────────────── │
│                     *  ▘▘ ▝▝  *                    │ What's new              │
│                                                    │ Added LSP (Language Se… │
│     Opus 4.5 · Claude Max · user@example.com's     │ Added `/terminal-setup… │
│     Organization                                   │ Added ctrl+t shortcut … │
│             ~/research/hiking                      │ /release-notes for more │
╰──────────────────────────────────────────────────────────────────────────────╯

> Research the top 10 hiking trails in Scotland

⏺ I'll search for information about the best hiking trails in Scotland.

  ✽ web search for "top hiking trails Scotland 2024"

  Esc to interrupt
"""

# =============================================================================
# Full capture: Stalled (user input with no Claude response)
# Expected: WAITING_USER with "Stalled" activity
# =============================================================================

REALISTIC_STALLED = """\
╭─── Claude Code v2.0.75 ──────────────────────────────────────────────────────╮
│                                                    │ Recent activity         │
│                 Welcome back!                 │ 5m ago   Complete tas…  │
│                                                    │ 8m ago   Write tests…   │
│                                                    │ 12m ago  Fix bug in…    │
│                     * ▐▛███▜▌ *                    │ /resume for more        │
│                    * ▝▜█████▛▘ *                   │ ─────────────────────── │
│                     *  ▘▘ ▝▝  *                    │ What's new              │
│                                                    │ Added LSP (Language Se… │
│     Opus 4.5 · Claude Max · user@example.com's     │ Added `/terminal-setup… │
│     Organization                                   │ Added ctrl+t shortcut … │
│             ~/Code/myproject                       │ /release-notes for more │
╰──────────────────────────────────────────────────────────────────────────────╯

> Write unit tests for the user service

⏺ I've written comprehensive unit tests for the user service, covering:
  - User creation and validation
  - Authentication flows
  - Permission checks
  - Edge cases

  All tests are passing. Would you like me to add any additional test cases?

────────────────────────────────────────────────────────────────────────────────
>\xa0Add tests for the password reset flow
────────────────────────────────────────────────────────────────────────────────
  ⏵⏵ auto-approve on (shift+tab to cycle)
"""

# =============================================================================
# Full capture: Completed task, no standing instructions
# Expected: NO_INSTRUCTIONS (yellow) when no standing_instructions set
# =============================================================================

REALISTIC_IDLE_NO_INSTRUCTIONS = """\
╭─── Claude Code v2.0.75 ──────────────────────────────────────────────────────╮
│                                                    │ Recent activity         │
│                 Welcome back!                 │ 2m ago   Complete ref…  │
│                                                    │ 10m ago  Apply changes… │
│                                                    │ 15m ago  Analyze code…  │
│                     * ▐▛███▜▌ *                    │ /resume for more        │
│                    * ▝▜█████▛▘ *                   │ ─────────────────────── │
│                     *  ▘▘ ▝▝  *                    │ What's new              │
│                                                    │ Added LSP (Language Se… │
│     Opus 4.5 · Claude Max · user@example.com's     │ Added `/terminal-setup… │
│     Organization                                   │ Added ctrl+t shortcut … │
│             ~/Code/webapp                          │ /release-notes for more │
╰──────────────────────────────────────────────────────────────────────────────╯

> Refactor the logging module to use structured logging

⏺ Read(src/utils/logger.py)
  ⎿  Read 89 lines from src/utils/logger.py

⏺ Edit(src/utils/logger.py)
  ⎿  Updated src/utils/logger.py (5 edits)

⏺ Edit(src/config/settings.py)
  ⎿  Updated src/config/settings.py (1 edit)

⏺ Bash("python -m pytest tests/test_logger.py -v")
  ⎿  All 12 tests passed

⏺ Successfully refactored the logging module to use structured logging.
  Changes made:
  - Replaced print statements with structured log calls
  - Added JSON formatter for production
  - Added context injection for request IDs
  - Updated configuration to support log levels

  Let me know if you'd like any adjustments!

────────────────────────────────────────────────────────────────────────────────
>
────────────────────────────────────────────────────────────────────────────────
  ? for shortcuts
"""

# =============================================================================
# Full capture: UI chrome containing "permission" (should NOT trigger)
# Expected: WAITING_USER (empty prompt), NOT permission detection
# =============================================================================

REALISTIC_UI_CHROME_PERMISSION = """\
╭─── Claude Code v2.0.75 ──────────────────────────────────────────────────────╮
│                                                    │ Recent activity         │
│                 Welcome back!                 │ 1m ago   Write file…    │
│                                                    │ 3m ago   Read config…   │
│                                                    │ 5m ago   Start task…    │
│                     * ▐▛███▜▌ *                    │ /resume for more        │
│                    * ▝▜█████▛▘ *                   │ ─────────────────────── │
│                     *  ▘▘ ▝▝  *                    │ What's new              │
│                                                    │ Added LSP (Language Se… │
│     Opus 4.5 · Claude Max · user@example.com's     │ Added `/terminal-setup… │
│     Organization                                   │ Added ctrl+t shortcut … │
│             ~/Code/testproject                     │ /release-notes for more │
╰──────────────────────────────────────────────────────────────────────────────╯

> Create a test file

⏺ Write(test.md)
  ⎿  Created test.md

⏺ Created test.md successfully.

────────────────────────────────────────────────────────────────────────────────
>
────────────────────────────────────────────────────────────────────────────────
  ⏵⏵ bypass permissions on (shift+tab to cycle)
"""

# =============================================================================
# Full capture: Bash permission prompt
# Expected: WAITING_USER with "Permission:" activity
# =============================================================================

REALISTIC_BASH_PERMISSION = """\
╭─── Claude Code v2.0.75 ──────────────────────────────────────────────────────╮
│                                                    │ Recent activity         │
│                 Welcome back!                 │ 30s ago  Prepare test…  │
│                                                    │ 2m ago   Read test fi…  │
│                                                    │ 5m ago   Analyze code…  │
│                     * ▐▛███▜▌ *                    │ /resume for more        │
│                    * ▝▜█████▛▘ *                   │ ─────────────────────── │
│                     *  ▘▘ ▝▝  *                    │ What's new              │
│                                                    │ Added LSP (Language Se… │
│     Opus 4.5 · Claude Max · user@example.com's     │ Added `/terminal-setup… │
│     Organization                                   │ Added ctrl+t shortcut … │
│             ~/Code/overcode                      │ /release-notes for more │
╰──────────────────────────────────────────────────────────────────────────────╯

> Run the test suite and fix any failures

⏺ I'll run the test suite to see the current state.

⏺ Bash("pytest tests/ -v --tb=short")

────────────────────────────────────────────────────────────────────────────────
 Tool use

   Bash("pytest tests/ -v --tb=short")
   Claude wants to run: pytest tests/ -v --tb=short

 Do you want to proceed?
 ❯ 1. Yes
   2. Yes, and don't ask again for Bash commands in
      /home/user/myproject
   3. No, and tell Claude what to do differently (esc)
"""

# =============================================================================
# Full capture: Fresh session (just welcome banner, no activity)
# Expected: WAITING_USER (empty prompt implied)
# =============================================================================

REALISTIC_FRESH_SESSION = """\
╭─── Claude Code v2.0.75 ──────────────────────────────────────────────────────╮
│                                                    │ Recent activity         │
│                 Welcome back!                 │                         │
│                                                    │ No recent activity      │
│                                                    │                         │
│                     * ▐▛███▜▌ *                    │ ─────────────────────── │
│                    * ▝▜█████▛▘ *                   │ What's new              │
│                     *  ▘▘ ▝▝  *                    │ Added LSP (Language Se… │
│                                                    │ Added `/terminal-setup… │
│     Opus 4.5 · Claude Max · user@example.com's     │ Added ctrl+t shortcut … │
│     Organization                                   │                         │
│             ~/Code/newproject                      │ /release-notes for more │
╰──────────────────────────────────────────────────────────────────────────────╯

────────────────────────────────────────────────────────────────────────────────
>
────────────────────────────────────────────────────────────────────────────────
  ? for shortcuts
"""

# =============================================================================
# Full capture: Terminated (Claude exited, now at shell prompt)
# Expected: TERMINATED
# Captured from real session where user typed /exit and Claude Code exited
# =============================================================================

REALISTIC_TERMINATED_SHELL = """\
⏺ Glad that landed well. Quick summary for the purchase:

  Wales Bonner x Adidas Superstar
  - Price: £240
  - UK Size 5: In stock
  - Link: https://example.com/product/12345

  Great choice - the brown leather, satin lining, and gold details make it feel genuinely special. Hope your friend loves t
hem!

> Perfect, thanks!

⏺ You're welcome - happy birthday to your friend!

> /exit
  ⎿  Catch you later!

───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
>
───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
user@host project % ls
AGENTS.md               docs                    overcode-cli          README.md               SUPERVISOR.md
demo                    Makefile                populate_demo_stats.py  run_e2e_test.sh         tests
demo_multi_agent.py     mcp_servers             pyproject.toml          src                     TUI_DEMO.md
DESIGN.md               MVP_STATUS.md           pytest.ini              supervisor_hooks.yaml   uv.lock
user@host project % pwd
/home/user/myproject
user@host project %
"""
