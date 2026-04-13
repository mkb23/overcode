# Wrappers

Wrappers let you run Claude agents in custom environments — Docker containers, VMs, remote machines, or any setup your project needs. A wrapper is a shell script that sits between overcode and the `claude` CLI, receiving the full claude command as arguments and executing it however you choose.

All existing overcode features (attach, send, pane capture, status detection, TUI) work transparently because the wrapper runs in the foreground in the tmux pane.

## Quick Start: Devcontainer

Launch an agent inside a Docker container in one command:

```bash
overcode launch -n my-agent -d ~/project --wrapper devcontainer
```

That's it. On first use, overcode auto-installs the bundled `devcontainer` wrapper to `~/.overcode/wrappers/devcontainer.sh`. The wrapper:

1. Pulls the [Microsoft Node.js devcontainer image](https://github.com/devcontainers/images/tree/main/src/javascript-node) (Node 22 on Debian Bookworm, multi-arch: Intel + Apple Silicon)
2. Starts a container with your project mounted at `/workspace`
3. Installs the Claude Code CLI inside the container
4. Runs claude interactively via `docker exec -it`

**Prerequisites:** Docker must be running. `ANTHROPIC_API_KEY` must be in your environment.

### Step-by-step: Your first containerised agent

1. **Ensure Docker is running:**
   ```bash
   docker info  # Should print server info, not an error
   ```

2. **Set your API key** (if not already exported):
   ```bash
   export ANTHROPIC_API_KEY="sk-ant-..."
   ```

3. **Launch an agent in a container:**
   ```bash
   overcode launch -n container-test -d ~/my-project --wrapper devcontainer -p "What files are in this project?"
   ```

4. **Watch it start** — the tmux pane will show:
   ```
   [devcontainer] No .devcontainer/ found, using default image: mcr.microsoft.com/devcontainers/universal:2
   [devcontainer] Starting container overcode-container-test ...
   [devcontainer] Installing Claude Code CLI inside container ...
   [devcontainer] Launching claude inside container ...
   ```
   Then claude starts normally inside the container.

5. **Interact as usual** — `overcode attach container-test`, `overcode send`, the TUI — everything works exactly as with a local agent.

6. **Container is cleaned up automatically** when the agent exits or is killed.

### Using a project's devcontainer

If your project has a `.devcontainer/` directory, the wrapper uses it automatically:

- **`.devcontainer/Dockerfile`** — Builds the image from your Dockerfile
- **`.devcontainer/devcontainer.json`** with `"image"` field — Uses that image directly
- **Neither** — Falls back to the universal devcontainer image

### Custom image

Override the image with an environment variable:

```bash
DEVCONTAINER_IMAGE=python:3.12-bookworm overcode launch -n py-agent -d ~/project --wrapper devcontainer
```

### Making devcontainer the default

Add to `~/.overcode/config.yaml`:

```yaml
new_agent_defaults:
  wrapper: devcontainer
```

Now every new agent (from CLI or the TUI's `n` dialog) launches in a container. The TUI's new-agent modal shows the wrapper field pre-filled — press `a` to accept or edit it.

## How Wrappers Work

A wrapper is an executable script that receives:

| Input | Description |
|-------|-------------|
| `$@` | The full claude command (e.g., `claude --session-id xyz --model sonnet`) |
| `OVERCODE_WRAPPER_DIR` | The agent's working directory on the host |
| `OVERCODE_SESSION_NAME` | Agent name |
| `OVERCODE_SESSION_ID` | Agent UUID |
| `OVERCODE_TMUX_SESSION` | Tmux session name |
| All other `OVERCODE_*` vars | Parent info, etc. |

The wrapper must execute claude interactively in the foreground. The simplest possible wrapper:

```bash
#!/usr/bin/env bash
exec "$@"
```

This is the bundled `passthrough` wrapper — it runs claude unchanged. Useful as a starting template.

### Wrapper resolution

When you specify `--wrapper <value>`, overcode resolves it in order:

1. **Absolute path** (`/path/to/wrapper.sh`) — used directly
2. **Relative path** (`./my-wrapper.sh`) — resolved from current directory
3. **Bare name** (`devcontainer`) — looked up in `~/.overcode/wrappers/` with extension fallback (`.sh`, `.py`, `.bash`, `.zsh`). If the name matches a bundled wrapper that isn't installed yet, it's auto-installed on first use.

## Managing Wrappers

```bash
# List installed and available wrappers
overcode wrappers list

# Install/update all bundled wrappers explicitly
overcode wrappers install

# Reset a wrapper to the bundled version (undo your edits)
overcode wrappers reset devcontainer

# Reset all bundled wrappers
overcode wrappers reset
```

Wrappers live in `~/.overcode/wrappers/`. You can place custom scripts there and reference them by name.

## Writing a Custom Wrapper

Create an executable script in `~/.overcode/wrappers/`:

```bash
#!/usr/bin/env bash
# ~/.overcode/wrappers/my-sandbox.sh

# Your custom setup here
echo "Setting up sandbox..."

# Must exec claude with $@ to keep it interactive in the tmux pane
exec "$@"
```

```bash
chmod +x ~/.overcode/wrappers/my-sandbox.sh
overcode launch -n test --wrapper my-sandbox
```

### Examples of what wrappers can do

- **Run in a Docker container** (the bundled `devcontainer` wrapper)
- **Run in a VM** (e.g., via `ssh` or `lima`)
- **Set up environment variables** (API keys, credentials, PATH)
- **Activate a virtualenv or conda environment** before running claude
- **Run with resource limits** (e.g., `cgroups`, `ulimit`)
- **Log or audit** the claude invocation

### Tips

- Always use `exec "$@"` (not just `"$@"`) so the wrapper process is replaced by claude. This ensures signals (Ctrl-C, kill) reach claude directly.
- The wrapper runs inside the tmux pane. Anything it prints to stdout/stderr is visible to the user and to overcode's pane capture.
- `OVERCODE_WRAPPER_DIR` is the directory the agent should work in. For container wrappers, mount this as the workspace.
- Test your wrapper standalone first: `OVERCODE_WRAPPER_DIR=. OVERCODE_SESSION_NAME=test ./my-wrapper.sh echo hello`

## Devcontainer Wrapper Reference

The bundled `devcontainer.sh` wrapper supports these environment variables for customisation:

| Variable | Description | Default |
|----------|-------------|---------|
| `DEVCONTAINER_IMAGE` | Override the Docker image (skips `.devcontainer/` detection) | - |
| `DEVCONTAINER_NAME` | Override the container name | `overcode-<agent-name>` |
| `DEVCONTAINER_SHELL` | Shell inside the container | `/bin/bash` |
| `ANTHROPIC_API_KEY` | Forwarded into the container for claude authentication | - |

The container is named `overcode-<agent-name>` and is automatically removed when the wrapper exits (via `trap EXIT`). If a container with the same name already exists (e.g., from a crashed session), it's removed before starting a new one.
