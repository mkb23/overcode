# Overcode 0.3.0 Release Notes

The headline change in 0.3.0 is that overcode is now a tmux-native tool. The new `overcode tmux` command creates a split layout — the compact dashboard on top, the focused agent's real terminal on the bottom — and this is now the recommended way to run overcode. If you've been using the standalone `overcode monitor`, everything still works, but the tmux split is a much better experience and worth switching to.

## Tmux split layout

When you run `overcode tmux`, overcode takes over a tmux window with a two-pane layout. The top pane shows the compact agent dashboard. The bottom pane is the live terminal of whichever agent you have focused — not a preview or emulation, but the actual tmux pane the agent is running in. Navigate with `j/k` and the bottom pane follows. Press `Tab` to toggle focus between the dashboard and the terminal, so you can scroll back through agent output or type directly into an agent's session.

From the terminal pane, `Option+J` and `Option+K` let you navigate agents without switching focus back to the dashboard. The toggle key is configurable in `config.yaml` if `Tab` conflicts with your setup.

When you select a sister (remote) agent, the dashboard auto-zooms to fill the window and shows a preview pane instead, since the remote terminal isn't local. Dialogs and modals also temporarily zoom the dashboard pane so they render properly.

There's a first-run confirmation the first time you use `overcode tmux`, since it modifies your tmux configuration (adding keybindings for the pane navigation). If you want to undo this, `overcode tmux --uninstall` cleanly removes everything.

## Column configuration and display

The dashboard columns are now fully configurable. Press `c` to open the column config modal and choose which columns to show at each detail level. Column headers are now visible, and alignment has been substantially improved — columns no longer flicker or shift width as agent state changes.

A new high-detail level shows everything at once for wide terminals. The cost column now cycles through three modes (press `$`): USD cost, token counts, and energy usage in joules. There's also an emoji-free mode for terminals that don't render emoji well.

## Agent management improvements

**Forking**: Press `F` to fork an agent — this creates a new agent in the same directory with the same context, useful when you want to branch an agent's work in two directions. Also available as `overcode fork` from the CLI.

**Instruction history**: Press `H` to see the history of instructions you've sent to each agent. You can re-inject past instructions, which is handy when an agent goes off track and you want to re-steer it with something that worked before. Instructions sent to remote agents are also recorded.

**Launch defaults**: Press `G` to set default values for new agents (directory, permissions, etc.) so you don't have to fill them in every time.

**Agent selection modal**: The launch flow now shows an agent selection modal, and you can launch agents on remote sister machines directly from the dashboard.

**Budgets**: The `--budget` flag on launch sets a per-agent cost cap. Subtree costs (parent + children) are tracked and displayed.

**Concurrent launch lock**: Only one agent can be launched at a time, preventing accidental double-launches from fast key presses.

## Sister (multi-machine) improvements

Remote agents from sister machines are now intermixed with local agents in all sort modes, rather than being grouped separately at the bottom. Alphabetical sort is tree-aware, keeping parent/child agent groups together. Remote agent timelines are now visible in the TUI, and you can create agents on remote machines directly from the dashboard.

## Sorting

Sort modes now properly intermix local and remote agents. The `overcode list` CLI command gained a `--sort` flag. Alphabetical sorting groups sibling agents together within their parent, so delegated agent hierarchies stay readable.

## Stability and performance

A large audit pass (PR #370) addressed 106 issues across the codebase — DRYing up duplicated logic, extracting functions, tightening exception handling, and fixing lint issues. This was primarily a code health effort, but it resolved a number of subtle bugs along the way.

Status light accuracy was a persistent irritation in 0.2.x — the green "working" indicator would flicker incorrectly during navigation or when prompts were visible. This has been traced to its root cause (a shared polling detector race condition) and fixed. Terminated agents no longer falsely flip to red, and the daemon now re-checks agent state before persisting termination.

Tmux window management was refactored from integer indices to string names, eliminating a class of bugs around window index reuse and collisions. False duplicate-daemon detection caused by zombie processes has been fixed. Thread-safety races in terminal content detection have been addressed.

The `overcode list` CLI now shares the TUI's render loop for consistent output, and column alignment uses visual cell width rather than character count (fixing alignment with unicode and emoji).

## Smaller changes

- Presence tracking now works on Linux (previously macOS-only)
- `overcode web` is simpler — it's now a non-blocking toggle, and `web.port`/`web.host` are configurable in `config.yaml`
- `Ctrl+S` works as an alternative to `Ctrl+Enter` for multiline send
- Supervisor start/stop (`[` and `]`) require a double-press to prevent accidental toggling
- Sleep countdown is now a proper aligned column
- The `b` key jumps to the next agent needing attention
