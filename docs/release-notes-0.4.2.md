# Overcode 0.4.2 Release Notes

0.4.2 brings overcode up to date with the newest Claude models and takes a big pass over cost and status accuracy — the two numbers you stare at most in the dashboard.

## Claude Fable 5 support

Overcode now recognises Claude Fable 5 (`claude-fable-5`): cost estimates use its $10/$50 per-million-token rates (with the matching cache write/read prices), context gauges know about its 1M-token window, and it shows up in the model column as `Fb5`. Opus 4.8 was also registered along the way.

## Cost model overhaul

The Claude cost model was out of date. Current Opus (4.5 through 4.8) is now priced at its actual $5/$25 rates, with the legacy $15/$75 pricing kept for Opus 4.1 and earlier. Bedrock sessions are detected from their message-ID prefix, and if your organisation has a negotiated Bedrock discount you can now express it in `config.yaml` — either a flat percentage or per-model — and cost estimates will reflect what you actually pay.

## Status display

The status column was split into two: a colour bucket that tells you at a glance whether an agent needs attention, and a detail badge (the column formerly known as SLP, now DTL) that tells you *why* — which tool is running, whether the agent is in a sandbox (🏖️), and so on. The badges got an emoji-compatibility pass for terminals with older emoji fonts, and the burn-rate figure is now window-scoped from the agent's spin baseline, so it reflects what the agent is doing now rather than its lifetime average.

## Navigation and layout

- A VSCode-style **Ctrl+P jump-to-agent modal** — type a few characters, hit enter. (Textual's command palette was disabled to free the key.)
- **Multi-repo focal support** (#170): Ctrl+R cycles the focal repo, with CLI support and sister sync.
- Passthru hotkeys are now configurable, with a new Ctrl+O slot and a configuration modal.
- New per-agent **CPU and RAM columns**, fed by a process-tree sampler.

## Usage and reliability

The usage bar now shows how stale its data is and when the 5-hour window resets, keeps the last good snapshot if a refresh fails, and surfaces repeated daemon timeouts in the status bar instead of failing silently. Hooks-mode auto-detection was fixed for overcode-launched sessions, and the status-detail column now renders in polling mode.

## Tmux fixes

A cluster of tmux papercuts got fixed: the "not in a mode" wheel-scroll loop in the split layout, mismatched wheel-up/wheel-down scroll rates, window resizes not cascading to nested agent windows, and keystrokes being sent while a pane was stuck in copy-mode. Sending keys to the focused session also moved to a background thread, so the dashboard no longer hiccups on slow panes.

## Smaller changes

- `overcode list` warns when the terminal is too narrow for its columns
- Sister machines that go unreachable now show a stale-content banner
- The sync-to-main branch is configurable
- Agent tags with dashboard filtering
- Dead tmux windows show a placeholder instead of an empty pane
- Sleep countdown auto-hides when idle, and CLI-only rows are dropped from the column configurator
