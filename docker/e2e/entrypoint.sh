#!/bin/sh
# E2E container entrypoint.
#
# /workspace is mounted read-only, so everything pytest wants to write
# (cache, basetemp) is redirected to /tmp, and artifacts go to /artifacts.

set -eu

mkdir -p "${E2E_ARTIFACTS:-/artifacts}" /tmp/pytest

# `scripts/e2e.sh --shell` drops into bash instead of pytest
if [ "${1:-}" = "shell" ]; then
    shift
    exec /bin/bash "$@"
fi

exec pytest \
    -o cache_dir=/tmp/pytest/cache \
    --basetemp=/tmp/pytest/basetemp \
    "$@"
