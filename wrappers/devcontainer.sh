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
#
# To install:
#   cp wrappers/devcontainer.sh ~/.overcode/wrappers/devcontainer.sh
#   chmod +x ~/.overcode/wrappers/devcontainer.sh
#
# Usage:
#   overcode launch -n my-agent --wrapper devcontainer

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
WORK_DIR="${OVERCODE_WRAPPER_DIR:-.}"
CONTAINER_NAME="${DEVCONTAINER_NAME:-overcode-${OVERCODE_SESSION_NAME:-agent}}"
CONTAINER_SHELL="${DEVCONTAINER_SHELL:-/bin/bash}"

# ---------------------------------------------------------------------------
# Resolve image: explicit override > .devcontainer build > default
# ---------------------------------------------------------------------------
IMAGE=""

if [[ -n "${DEVCONTAINER_IMAGE:-}" ]]; then
    IMAGE="$DEVCONTAINER_IMAGE"
elif [[ -f "${WORK_DIR}/.devcontainer/Dockerfile" ]]; then
    IMAGE="overcode-dc-${OVERCODE_SESSION_NAME:-agent}"
    echo "[devcontainer wrapper] Building image from ${WORK_DIR}/.devcontainer/Dockerfile ..."
    docker build -q -t "$IMAGE" \
        -f "${WORK_DIR}/.devcontainer/Dockerfile" \
        "${WORK_DIR}/.devcontainer"
elif [[ -f "${WORK_DIR}/.devcontainer/devcontainer.json" ]]; then
    # Try to extract the image from devcontainer.json (simple cases)
    IMAGE=$(python3 -c "
import json, sys
with open('${WORK_DIR}/.devcontainer/devcontainer.json') as f:
    # Strip comments (// style) for simple JSON-with-comments
    lines = [l for l in f if not l.strip().startswith('//')]
    data = json.loads(''.join(lines))
print(data.get('image', ''))
" 2>/dev/null || true)
    if [[ -z "$IMAGE" ]]; then
        echo "[devcontainer wrapper] Could not determine image from devcontainer.json"
        echo "Set DEVCONTAINER_IMAGE or add a Dockerfile to .devcontainer/"
        exit 1
    fi
else
    # No devcontainer config at all -- use a sensible default
    IMAGE="mcr.microsoft.com/devcontainers/javascript-node:22-bookworm"
    echo "[devcontainer wrapper] No .devcontainer/ found, using default image: $IMAGE"
fi

# ---------------------------------------------------------------------------
# Container lifecycle
# ---------------------------------------------------------------------------
_cleanup() {
    echo "[devcontainer wrapper] Stopping container ${CONTAINER_NAME} ..."
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
}

# Clean up on exit so containers don't accumulate
trap _cleanup EXIT

# Remove stale container with same name
docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true

echo "[devcontainer wrapper] Starting container ${CONTAINER_NAME} (image: ${IMAGE}) ..."
docker run -d \
    --name "$CONTAINER_NAME" \
    -v "${WORK_DIR}:/workspace" \
    -w /workspace \
    "$IMAGE" \
    sleep infinity >/dev/null

# ---------------------------------------------------------------------------
# Install claude CLI if not present
# ---------------------------------------------------------------------------
if ! docker exec "$CONTAINER_NAME" which claude >/dev/null 2>&1; then
    echo "[devcontainer wrapper] Installing Claude Code CLI inside container ..."
    # Ensure npm is available (node images have it; others may not)
    if ! docker exec "$CONTAINER_NAME" which npm >/dev/null 2>&1; then
        echo "[devcontainer wrapper] npm not found -- installing Node.js ..."
        docker exec "$CONTAINER_NAME" $CONTAINER_SHELL -c \
            'apt-get update -qq && apt-get install -y -qq nodejs npm >/dev/null 2>&1' || {
            echo "[devcontainer wrapper] ERROR: Could not install Node.js. Use an image with Node.js pre-installed."
            exit 1
        }
    fi
    docker exec "$CONTAINER_NAME" npm install -g @anthropic-ai/claude-code >/dev/null 2>&1
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
echo "[devcontainer wrapper] Launching claude inside container ..."
exec docker exec -it \
    "${EXEC_ARGS[@]}" \
    -w /workspace \
    "$CONTAINER_NAME" \
    "$@"
