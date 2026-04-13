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
# Launches claude inside a devcontainer-compatible Docker container.
# The container is built from the project's .devcontainer/ directory
# (or a sensible default), the workspace is bind-mounted, and claude
# runs interactively via `docker exec -it` so the tmux pane sees it
# directly -- all overcode operations (attach, send, capture) work
# transparently.
#
# Interface:
#   $@                    — the full claude command
#   OVERCODE_WRAPPER_DIR  — host directory to mount as /workspace
#   OVERCODE_SESSION_NAME — agent name (used for container naming)
#
# Environment forwarded into the container:
#   ANTHROPIC_API_KEY     — required for claude authentication
#   OVERCODE_*            — all overcode env vars
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

echo "[devcontainer] Starting container ${CONTAINER_NAME} (image: ${IMAGE}) ..."
docker run -d \\
    --name "$CONTAINER_NAME" \\
    -v "${WORK_DIR}:/workspace" \\
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
# Auth: if ANTHROPIC_API_KEY is set it will be forwarded via env vars below.
# Otherwise claude will prompt for login in the tmux pane on first use —
# visit the URL it shows and paste the code.  The session persists inside
# the container for subsequent runs.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Install claude CLI if not present
# ---------------------------------------------------------------------------
if ! docker exec "${USER_FLAG[@]}" "$CONTAINER_NAME" which claude >/dev/null 2>&1; then
    echo "[devcontainer] Installing Claude Code CLI inside container ..."
    # Ensure npm is available (node images have it; others may not)
    if ! docker exec "$CONTAINER_NAME" which npm >/dev/null 2>&1; then
        echo "[devcontainer] npm not found -- installing Node.js ..."
        docker exec "$CONTAINER_NAME" $CONTAINER_SHELL -c \\
            'apt-get update -qq && apt-get install -y -qq nodejs npm >/dev/null 2>&1' || {
            echo "[devcontainer] ERROR: Could not install Node.js. Use an image with Node.js pre-installed."
            exit 1
        }
    fi
    docker exec "${USER_FLAG[@]}" "$CONTAINER_NAME" npm install -g @anthropic-ai/claude-code 2>&1
fi

# ---------------------------------------------------------------------------
# Build docker exec env-var flags
# ---------------------------------------------------------------------------
EXEC_ARGS=()

# Forward ANTHROPIC_API_KEY
if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
    EXEC_ARGS+=(-e "ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}")
fi

# Forward all OVERCODE_* env vars
while IFS='=' read -r key value; do
    EXEC_ARGS+=(-e "${key}=${value}")
done < <(env | grep '^OVERCODE_' || true)

# ---------------------------------------------------------------------------
# Exec claude inside the container
# ---------------------------------------------------------------------------
echo "[devcontainer] Launching claude inside container${CONTAINER_USER:+ (user: $CONTAINER_USER)} ..."
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
