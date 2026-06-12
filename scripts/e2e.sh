#!/usr/bin/env bash
# Containerized E2E test runner for OverCode.
# Design: docs/design/e2e-devcontainer-testing.md
#
# Usage:
#   scripts/e2e.sh                       # tier 1+2 (workflows + visual)
#   scripts/e2e.sh tests/container/workflows/test_lifecycle.py -k kill
#   scripts/e2e.sh --real                # tier 3 (needs CLAUDE_CODE_OAUTH_TOKEN)
#   scripts/e2e.sh --shell               # interactive shell in the container
#   scripts/e2e.sh --rw                  # mount repo read-write (snapshot updates)
#   scripts/e2e.sh --no-build            # skip image rebuild
#
# Everything after the flags is passed to pytest inside the container.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${OVERCODE_E2E_IMAGE:-overcode-e2e:local}"

MOUNT_MODE="ro"
BUILD=1
SHELL_MODE=0
REAL=0
PYTEST_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --rw) MOUNT_MODE="rw"; shift ;;
        --no-build) BUILD=0; shift ;;
        --shell) SHELL_MODE=1; shift ;;
        --real) REAL=1; shift ;;
        *) PYTEST_ARGS+=("$1"); shift ;;
    esac
done

if [[ $BUILD -eq 1 ]]; then
    docker build --target e2e -t "$IMAGE" -f "$ROOT/docker/e2e/Dockerfile" "$ROOT"
fi

ARTIFACTS="$ROOT/artifacts/e2e"
mkdir -p "$ARTIFACTS"

RUN_ARGS=(
    --rm
    --init
    -v "$ROOT:/workspace:$MOUNT_MODE"
    -v "$ARTIFACTS:/artifacts"
    -e E2E_ARTIFACTS=/artifacts
)

# Interactive TTY when available (CI has none)
if [[ -t 1 ]]; then
    RUN_ARGS+=(-it)
fi

if [[ $REAL -eq 1 ]]; then
    if [[ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]]; then
        echo "error: --real requires CLAUDE_CODE_OAUTH_TOKEN in the environment" >&2
        exit 2
    fi
    RUN_ARGS+=(-e CLAUDE_CODE_OAUTH_TOKEN -e OVERCODE_E2E_REAL_LLM=1)
fi

if [[ $SHELL_MODE -eq 1 ]]; then
    exec docker run "${RUN_ARGS[@]}" "$IMAGE" shell
fi

# Default to the container suite unless the caller named a path; flags like
# -q/-k alone must not fall through to pytest's testpaths (which would collect
# the legacy host suites too).
HAS_PATH=0
for arg in "${PYTEST_ARGS[@]+"${PYTEST_ARGS[@]}"}"; do
    [[ -e "$arg" || "$arg" == tests/* ]] && HAS_PATH=1
done
if [[ $HAS_PATH -eq 0 ]]; then
    PYTEST_ARGS=(tests/container "${PYTEST_ARGS[@]+"${PYTEST_ARGS[@]}"}")
fi
if [[ $REAL -eq 1 ]]; then
    PYTEST_ARGS+=(-m real_llm)
fi

exec docker run "${RUN_ARGS[@]}" "$IMAGE" "${PYTEST_ARGS[@]}"
