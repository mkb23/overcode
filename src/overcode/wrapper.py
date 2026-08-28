"""
Wrapper resolution for agent launch.

A wrapper is a user-provided executable that wraps the claude CLI invocation.
Instead of running `claude --session-id xyz` directly, overcode runs
`wrapper.sh claude --session-id xyz`. The wrapper receives the full claude
command as arguments ($@) and all OVERCODE_* environment variables, plus
OVERCODE_WRAPPER_DIR set to the agent's working directory.

Wrappers are resolved by name or path:
  1. Absolute path — used directly
  2. Relative path (contains /) — resolved from cwd
  3. Bare name — looked up in ~/.overcode/wrappers/ (with or without extension)
     If a bare name matches a bundled wrapper that isn't installed yet,
     it is auto-installed on first use.
"""

import os
import stat
from pathlib import Path
from typing import Optional


# ── bundled wrapper content ──────────────────────────────────────────────
# Reference copies shipped with overcode.  Auto-installed to
# ~/.overcode/wrappers/ on first use; `overcode wrappers reset` restores them.

BUNDLED_WRAPPERS: dict[str, str] = {
    "passthrough.sh": """\
#!/usr/bin/env bash
# Wrapper: passthrough
#
# The simplest possible wrapper -- executes the claude command unchanged.
# Useful as a template for custom wrappers.
#
# Interface:
#   $@                    — the full claude command (e.g. claude --session-id xyz)
#   OVERCODE_WRAPPER_DIR  — the agent's intended working directory
#   OVERCODE_SESSION_NAME — agent name
#   OVERCODE_SESSION_ID   — agent UUID
#
# Usage:
#   overcode launch -n my-agent --wrapper passthrough

exec "$@"
""",

    "devcontainer.sh": """\
#!/usr/bin/env bash
set -euo pipefail
# Wrapper: devcontainer
#
# Launches the agent CLI inside a devcontainer-compatible Docker container.
# The container is built from the project's .devcontainer/ directory
# (or a sensible default), the workspace is bind-mounted, and the agent
# runs interactively via `docker exec -it` so the tmux pane sees it
# directly -- all overcode operations (attach, send, capture) work
# transparently.
#
# Interface:
#   $@                    — the full agent command
#   OVERCODE_WRAPPER_DIR  — host directory to mount as /workspace
#   OVERCODE_SESSION_NAME — agent name (used for container naming)
#   OVERCODE_BACKEND      — agent CLI to install: claude-code (default),
#                           opencode, codex, or grok
#
# Environment forwarded into the container:
#   ANTHROPIC_API_KEY     — required for claude authentication
#   OPENAI_API_KEY etc.   — provider credentials for opencode/codex
#   XAI_API_KEY           — provider credential for grok
#   OVERCODE_*            — all overcode env vars
#
# grok auth note: grok normally authenticates via a SuperGrok/X Premium+
# subscription login that writes ~/.grok/auth.json on the host — there is no
# npm-package or API-key-only install path the way codex/opencode have.
# XAI_API_KEY is forwarded above if set, but interactive subscription login
# has not been verified inside a container. Mount your host ~/.grok
# (containing auth.json) into the container at the same path yourself if you
# need grok to authenticate without an interactive login in the pane —
# unverified, not done automatically by this wrapper (see docs/backends.md).
#
# Optional env vars for customisation:
#   DEVCONTAINER_IMAGE    — override the Docker image (skip build)
#   DEVCONTAINER_NAME     — override the container name
#   DEVCONTAINER_SHELL    — shell inside container (default: /bin/bash)
#   DEVCONTAINER_USER     — user inside container (default: auto-detect, then node)
#
# Usage:
#   overcode launch -n my-agent --wrapper devcontainer

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
WORK_DIR="${OVERCODE_WRAPPER_DIR:-.}"
CONTAINER_NAME="${DEVCONTAINER_NAME:-overcode-${OVERCODE_SESSION_NAME:-agent}}"
CONTAINER_SHELL="${DEVCONTAINER_SHELL:-/bin/bash}"

# Which agent CLI to install inside the container. The launcher exports
# OVERCODE_BACKEND only for non-default backends, so an unset value means
# Claude Code and the install step below is unchanged from before.
AGENT_BACKEND="${OVERCODE_BACKEND:-claude-code}"
case "$AGENT_BACKEND" in
    opencode)
        AGENT_BINARY="opencode"
        AGENT_NPM_PACKAGE="opencode-ai@latest"
        AGENT_LABEL="opencode"
        AGENT_INSTALL_METHOD="npm"
        ;;
    codex)
        AGENT_BINARY="codex"
        AGENT_NPM_PACKAGE="@openai/codex"
        AGENT_LABEL="Codex CLI"
        AGENT_INSTALL_METHOD="npm"
        ;;
    grok)
        AGENT_BINARY="grok"
        AGENT_NPM_PACKAGE=""
        AGENT_LABEL="Grok Build"
        AGENT_INSTALL_METHOD="curl"
        ;;
    *)
        AGENT_BINARY="claude"
        AGENT_NPM_PACKAGE="@anthropic-ai/claude-code"
        AGENT_LABEL="Claude Code"
        AGENT_INSTALL_METHOD="npm"
        ;;
esac

# Default image: Microsoft devcontainer with Node.js (required by Claude
# Code) on a Debian Bookworm base.  Multi-arch (amd64 + arm64) so it
# works on both Intel and Apple Silicon.  Python/Go/etc. can be added
# via apt inside the container or by using a project .devcontainer/.
DEFAULT_IMAGE="mcr.microsoft.com/devcontainers/javascript-node:22-bookworm"

# ---------------------------------------------------------------------------
# Resolve image: explicit override > .devcontainer build > .devcontainer.json > default
# ---------------------------------------------------------------------------
IMAGE=""

if [[ -n "${DEVCONTAINER_IMAGE:-}" ]]; then
    IMAGE="$DEVCONTAINER_IMAGE"
elif [[ -f "${WORK_DIR}/.devcontainer/Dockerfile" ]]; then
    IMAGE="overcode-dc-${OVERCODE_SESSION_NAME:-agent}"
    echo "[devcontainer] Building image from ${WORK_DIR}/.devcontainer/Dockerfile ..."
    docker build -q -t "$IMAGE" \\
        -f "${WORK_DIR}/.devcontainer/Dockerfile" \\
        "${WORK_DIR}/.devcontainer"
elif [[ -f "${WORK_DIR}/.devcontainer/devcontainer.json" ]]; then
    # Try to extract the image from devcontainer.json (simple cases)
    IMAGE=$(python3 -c "
import json, sys
with open('${WORK_DIR}/.devcontainer/devcontainer.json') as f:
    lines = [l for l in f if not l.strip().startswith('//')]
    data = json.loads(''.join(lines))
print(data.get('image', ''))
" 2>/dev/null || true)
    if [[ -z "$IMAGE" ]]; then
        echo "[devcontainer] No image in devcontainer.json, using default: $DEFAULT_IMAGE"
        IMAGE="$DEFAULT_IMAGE"
    fi
else
    IMAGE="$DEFAULT_IMAGE"
    echo "[devcontainer] No .devcontainer/ found, using default image: $IMAGE"
fi

# ---------------------------------------------------------------------------
# Container lifecycle
# ---------------------------------------------------------------------------
_cleanup() {
    echo "[devcontainer] Stopping container ${CONTAINER_NAME} ..."
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
}

# Clean up on exit so containers don't accumulate
trap _cleanup EXIT

# Remove stale container with same name
docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true

# ---------------------------------------------------------------------------
# Mount the overcode state exchange directory so hooks inside the container
# can write hook_state/report files directly to the host, and read
# monitor_daemon_state for budget enforcement.  Only JSON state files
# live here — no code, no credentials.
# ---------------------------------------------------------------------------
TMUX_SESSION="${OVERCODE_TMUX_SESSION:-agents}"
STATE_EXCHANGE="${HOME}/.overcode/sessions/${TMUX_SESSION}"
mkdir -p "$STATE_EXCHANGE"
CONTAINER_STATE_DIR="/overcode-state"

echo "[devcontainer] Starting container ${CONTAINER_NAME} (image: ${IMAGE}) ..."
docker run -d \\
    --name "$CONTAINER_NAME" \\
    -v "${WORK_DIR}:/workspace" \\
    -v "${STATE_EXCHANGE}:${CONTAINER_STATE_DIR}/${TMUX_SESSION}" \\
    -w /workspace \\
    "$IMAGE" \\
    sleep infinity >/dev/null

# ---------------------------------------------------------------------------
# Detect non-root user (Claude Code refuses --dangerously-skip-permissions as root)
# ---------------------------------------------------------------------------
CONTAINER_USER="${DEVCONTAINER_USER:-}"
if [[ -z "$CONTAINER_USER" ]]; then
    # Try common devcontainer users: node, vscode, ubuntu, then fall back
    for candidate in node vscode ubuntu; do
        if docker exec "$CONTAINER_NAME" id "$candidate" >/dev/null 2>&1; then
            CONTAINER_USER="$candidate"
            break
        fi
    done
fi
USER_FLAG=()
if [[ -n "$CONTAINER_USER" ]]; then
    USER_FLAG=(-u "$CONTAINER_USER")
fi

# ---------------------------------------------------------------------------
# Auth: provider API keys present in the environment are forwarded via env
# vars below.  Otherwise the agent CLI will prompt for login in the tmux pane
# on first use — visit the URL it shows and paste the code.  The session
# persists inside the container for subsequent runs.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Install the agent CLI if not present
# ---------------------------------------------------------------------------
if ! docker exec "${USER_FLAG[@]}" "$CONTAINER_NAME" which "$AGENT_BINARY" >/dev/null 2>&1; then
    echo "[devcontainer] Installing ${AGENT_LABEL} CLI inside container ..."
    if [[ "$AGENT_INSTALL_METHOD" == "npm" ]]; then
        # Ensure npm is available (node images have it; others may not)
        if ! docker exec "$CONTAINER_NAME" which npm >/dev/null 2>&1; then
            echo "[devcontainer] npm not found -- installing Node.js ..."
            docker exec "$CONTAINER_NAME" $CONTAINER_SHELL -c \\
                'apt-get update -qq && apt-get install -y -qq nodejs npm >/dev/null 2>&1' || {
                echo "[devcontainer] ERROR: Could not install Node.js. Use an image with Node.js pre-installed."
                exit 1
            }
        fi
        docker exec "${USER_FLAG[@]}" "$CONTAINER_NAME" npm install -g "$AGENT_NPM_PACKAGE" 2>&1
    else
        # grok has no npm package -- x.ai ships a curl installer instead.
        if ! docker exec "$CONTAINER_NAME" which curl >/dev/null 2>&1; then
            echo "[devcontainer] curl not found -- installing it ..."
            docker exec "$CONTAINER_NAME" $CONTAINER_SHELL -c \\
                'apt-get update -qq && apt-get install -y -qq curl ca-certificates >/dev/null 2>&1' || {
                echo "[devcontainer] ERROR: Could not install curl. Use an image with curl pre-installed."
                exit 1
            }
        fi
        docker exec "${USER_FLAG[@]}" "$CONTAINER_NAME" $CONTAINER_SHELL -c \\
            'curl -fsSL https://x.ai/cli/install.sh | bash' 2>&1
    fi
fi

# ---------------------------------------------------------------------------
# Install overcode for hook handler (enables status detection, budget, context)
# ---------------------------------------------------------------------------
if ! docker exec "${USER_FLAG[@]}" "$CONTAINER_NAME" which overcode >/dev/null 2>&1; then
    echo "[devcontainer] Installing overcode (hook handler) inside container ..."
    # Ensure pip is available
    if ! docker exec "${USER_FLAG[@]}" "$CONTAINER_NAME" which pip >/dev/null 2>&1; then
        if docker exec "${USER_FLAG[@]}" "$CONTAINER_NAME" which pip3 >/dev/null 2>&1; then
            docker exec "$CONTAINER_NAME" ln -sf "$(docker exec "$CONTAINER_NAME" which pip3)" /usr/local/bin/pip 2>/dev/null || true
        else
            docker exec "$CONTAINER_NAME" $CONTAINER_SHELL -c \\
                'apt-get update -qq && apt-get install -y -qq python3-pip >/dev/null 2>&1' || true
        fi
    fi
    docker exec "${USER_FLAG[@]}" "$CONTAINER_NAME" pip install --break-system-packages overcode 2>&1 || \\
        docker exec "${USER_FLAG[@]}" "$CONTAINER_NAME" pip install overcode 2>&1 || \\
        echo "[devcontainer] Warning: Could not install overcode — hooks will be degraded"
fi

# Install overcode hooks into Claude Code settings inside the container.
# opencode/codex/grok have no settings.json hook protocol: opencode's
# telemetry comes from the bundled plugin the launcher stages into
# <project>/.opencode/plugins/ (rides in on the /workspace mount); codex's
# comes from per-launch `-c 'hooks.<Event>=...'` argv the launcher already
# injects; grok's comes from the global ~/.grok/hooks/overcode.json the
# launcher stages on the host. All three write hook-state files straight
# into the mounted state-exchange dir below without any settings.json step.
if [[ "$AGENT_BACKEND" != "opencode" && "$AGENT_BACKEND" != "codex" && "$AGENT_BACKEND" != "grok" ]] && \\
   docker exec "${USER_FLAG[@]}" "$CONTAINER_NAME" which overcode >/dev/null 2>&1; then
    docker exec "${USER_FLAG[@]}" \\
        -e "OVERCODE_STATE_DIR=${CONTAINER_STATE_DIR}" \\
        "$CONTAINER_NAME" overcode hooks install 2>/dev/null || true
fi

# ---------------------------------------------------------------------------
# Build docker exec env-var flags
# ---------------------------------------------------------------------------
EXEC_ARGS=()

# Forward provider credentials the agent CLI may need
for cred in ANTHROPIC_API_KEY OPENAI_API_KEY OPENROUTER_API_KEY GEMINI_API_KEY XAI_API_KEY; do
    if [[ -n "${!cred:-}" ]]; then
        EXEC_ARGS+=(-e "${cred}=${!cred}")
    fi
done

# Forward all OVERCODE_* env vars
while IFS='=' read -r key value; do
    EXEC_ARGS+=(-e "${key}=${value}")
done < <(env | grep '^OVERCODE_' || true)

# Point hook handler at the mounted state exchange directory
EXEC_ARGS+=(-e "OVERCODE_STATE_DIR=${CONTAINER_STATE_DIR}")

# ---------------------------------------------------------------------------
# Exec the agent inside the container
# ---------------------------------------------------------------------------
echo "[devcontainer] Launching ${AGENT_BINARY} inside container${CONTAINER_USER:+ (user: $CONTAINER_USER)} ..."
exec docker exec -it \\
    "${USER_FLAG[@]}" \\
    "${EXEC_ARGS[@]}" \\
    -w /workspace \\
    "$CONTAINER_NAME" \\
    "$@"
""",
}


# ── helpers ──────────────────────────────────────────────────────────────

def _wrappers_dir() -> Path:
    """Return the global wrappers directory (~/.overcode/wrappers/)."""
    base = os.environ.get("OVERCODE_DIR", str(Path.home() / ".overcode"))
    return Path(base) / "wrappers"


def _is_executable(path: Path) -> bool:
    """Check that path exists, is a file, and is executable."""
    return path.is_file() and os.access(str(path), os.X_OK)


def _install_bundled(name: str, target_dir: Path) -> Optional[Path]:
    """Install a bundled wrapper to target_dir if it matches a known name.

    Returns the installed path, or None if name doesn't match any bundled wrapper.
    """
    # Match bare name to bundled filename (e.g. "devcontainer" → "devcontainer.sh")
    for filename, content in BUNDLED_WRAPPERS.items():
        stem = Path(filename).stem
        if name == stem or name == filename:
            target_dir.mkdir(parents=True, exist_ok=True)
            dest = target_dir / filename
            dest.write_text(content)
            dest.chmod(dest.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
            return dest
    return None


# ── public API ───────────────────────────────────────────────────────────

def resolve_wrapper(wrapper: str) -> Optional[str]:
    """Resolve a wrapper specification to an absolute executable path.

    For bare names, auto-installs from bundled wrappers on first use
    if the wrapper isn't already in ~/.overcode/wrappers/.

    Args:
        wrapper: An absolute path, relative path, or bare name.

    Returns:
        Absolute path to the executable wrapper, or None if not found/not executable.
    """
    if not wrapper or not wrapper.strip():
        return None

    wrapper = wrapper.strip()
    path = Path(wrapper)

    # Absolute path
    if path.is_absolute():
        return str(path) if _is_executable(path) else None

    # Relative path (contains a slash)
    if "/" in wrapper:
        resolved = Path(wrapper).resolve()
        return str(resolved) if _is_executable(resolved) else None

    # Bare name — search ~/.overcode/wrappers/
    search_dir = _wrappers_dir()

    # Check if already installed
    if search_dir.is_dir():
        for suffix in ("", ".sh", ".bash", ".py", ".zsh"):
            candidate = search_dir / f"{wrapper}{suffix}"
            if _is_executable(candidate):
                return str(candidate)

    # Not found — try auto-installing from bundled wrappers
    installed = _install_bundled(wrapper, search_dir)
    if installed:
        return str(installed)

    return None


def list_available_wrappers() -> list[tuple[str, str]]:
    """List wrappers available in ~/.overcode/wrappers/.

    Returns:
        List of (name, path) tuples for each executable file found.
    """
    search_dir = _wrappers_dir()
    if not search_dir.is_dir():
        return []

    results = []
    for entry in sorted(search_dir.iterdir()):
        if _is_executable(entry):
            results.append((entry.stem, str(entry)))
    return results


def install_all_bundled() -> list[tuple[str, str]]:
    """Install all bundled wrappers. Returns list of (name, status) tuples.

    Status is "installed", "updated", or "unchanged".
    """
    target_dir = _wrappers_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    results = []

    for filename, content in BUNDLED_WRAPPERS.items():
        dest = target_dir / filename
        if dest.exists():
            if dest.read_text() == content:
                results.append((filename, "unchanged"))
                continue
            status = "updated"
        else:
            status = "installed"

        dest.write_text(content)
        dest.chmod(dest.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        results.append((filename, status))

    return results


def reset_wrapper(name: str) -> Optional[str]:
    """Reset a single wrapper to its bundled version.

    Returns status string or None if name doesn't match a bundled wrapper.
    """
    target_dir = _wrappers_dir()

    for filename, content in BUNDLED_WRAPPERS.items():
        stem = Path(filename).stem
        if name == stem or name == filename:
            target_dir.mkdir(parents=True, exist_ok=True)
            dest = target_dir / filename
            dest.write_text(content)
            dest.chmod(dest.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
            return "restored"

    return None
