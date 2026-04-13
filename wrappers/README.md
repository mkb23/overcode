# Wrapper Scripts

Reference copies of the wrapper scripts bundled with overcode.

These are automatically installed to `~/.overcode/wrappers/` on first use.
The canonical source is `src/overcode/wrapper.py` (the `BUNDLED_WRAPPERS` dict).

## Available wrappers

- **passthrough** — Identity wrapper, executes claude unchanged. Useful as a template.
- **devcontainer** — Launches claude inside a Docker container (devcontainer-compatible).

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
