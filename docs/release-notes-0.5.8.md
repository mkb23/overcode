# Overcode 0.5.8 Release Notes

- **Agents show their own model and effort.** Another Claude in the same repo (the IDE, a terminal, an agent in another overcode session) could become an agent's conversation, and `/clear` could move an agent back to the one it left. The agent's hooks now decide.
- **Skill profiles (#499).** Named sets of skills per agent, edited with `W`. `delegating-to-agents` is merged into one rewritten `overcode` skill (`overcode skills install` to update).
- **Status colours (#507).** Background shells and subagents show yellow, not red. A re-prompt after an interrupt or an answered permission shows the right colour. Restarting the TUI no longer rings every red agent's bell.
- **`restart` keeps the agent's model (#505)**, and `restart --model` changes it. Light mode, bulk revive, `shutdown` and configurable keys (#508–#510).
- **PR column reads Claude Code's own PR link (#489).** A JOB column per agent (#463), and clicking a row switches the pane to it.
- **Fixes:** multi-line launch prompts submit; no emoji too new for VS Code's terminal (#504); the overagent can launch top-level agents.
