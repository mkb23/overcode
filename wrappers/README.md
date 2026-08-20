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
to decide which CLI to install — unset means Claude Code.

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
