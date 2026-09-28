# Overcode 0.5.8 Release Notes

- **Skill profiles (#499).** Named sets of skills switched on per agent, for Claude Code and opencode. Other personal skills are hidden for that agent. `W` edits profiles, `overcode skills pin` gives a folder a default. See [Skill Profiles](skill-profiles.md).
- **One `overcode` skill.** `delegating-to-agents` is merged into it and rewritten for every backend. Run `overcode skills install` to update.
- **Multi-line first prompts now submit.** A launch prompt with a line break used to sit unsent.
- **Learning journey:** a Skills track, and backends counted per agent CLI.
- **The overagent can launch top-level agents** (`--no-parent`), and its children are ordinary agents.
