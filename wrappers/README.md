# Wrapper Scripts

Reference copies of the wrapper scripts bundled with overcode.

These are automatically installed to `~/.overcode/wrappers/` on first use.
The canonical source is `src/overcode/wrapper.py` (the `BUNDLED_WRAPPERS` dict);
the files here are verbatim copies of it — edit `wrapper.py`, then re-sync.

## Available wrappers

- **passthrough** — Identity wrapper, executes the agent CLI unchanged. Useful as a template.
- **devcontainer** — Launches the agent CLI inside a Docker container (devcontainer-compatible).

Both are backend-neutral: the agent argv arrives as `"$@"`. `devcontainer`
reads `OVERCODE_BACKEND` (exported by the launcher for non-default backends)
to decide which CLI to install — unset means Claude Code, and `opencode`,
`codex`, and `grok` are also recognised. codex installs the same way Claude
Code does (`npm install -g @openai/codex`); grok has no npm package and uses
x.ai's curl installer (`curl -fsSL https://x.ai/cli/install.sh | bash`)
instead. `overcode hooks install` (the Claude Code `.claude/settings.json`
step) is skipped for all three non-Claude backends — none of them use that
protocol (opencode: bundled plugin, codex: per-launch `-c hooks...` argv,
grok: a global `~/.grok/hooks/overcode.json` file).

**grok auth inside a container is unverified.** grok normally authenticates
via a SuperGrok/X Premium+ subscription login that writes `~/.grok/auth.json`
on the host — unlike codex/opencode's API-key-friendly auth, there's no
purely non-interactive path. The wrapper forwards `XAI_API_KEY` if set, but
whether grok's interactive login flow even works inside a container has not
been tested with a live docker build. If you need grok to authenticate
without an interactive login in the pane, mount your host `~/.grok`
(containing `auth.json`) into the container at the same path yourself — the
wrapper does not do this automatically.

## CLI commands

```bash
overcode wrappers list              # Show installed + available wrappers
overcode wrappers install           # Install/update all bundled wrappers
overcode wrappers reset             # Reset all to bundled versions
overcode wrappers reset devcontainer  # Reset a specific wrapper
```

## Usage

```bash
# Per-agent
overcode launch -n my-agent --wrapper devcontainer

# As default for all agents
# ~/.overcode/config.yaml:
#   new_agent_defaults:
#     wrapper: devcontainer
```
