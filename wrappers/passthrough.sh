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
# To install:
#   cp wrappers/passthrough.sh ~/.overcode/wrappers/passthrough.sh
#   chmod +x ~/.overcode/wrappers/passthrough.sh
#
# Usage:
#   overcode launch -n my-agent --wrapper passthrough

exec "$@"
