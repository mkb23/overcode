# syntax=docker/dockerfile:1
# E2E test container for OverCode.
#
# Design: docs/design/e2e-devcontainer-testing.md
# The container is the cleanup boundary: tmux servers, daemons and (mock or
# real) Claude processes all live and die inside it. The repo is mounted
# read-only at /workspace; the only writable host surface is /artifacts.
#
# Targets:
#   e2e — CI / scripts/e2e.sh test runner (default CMD runs pytest)
#   dev — interactive debugging via .devcontainer/devcontainer.json

FROM python:3.12-slim-bookworm AS base

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        tmux \
        git \
        openssh-client \
        curl \
        procps \
        ca-certificates \
        fonts-dejavu-core \
        nodejs \
        npm \
    && rm -rf /var/lib/apt/lists/*

# Claude Code CLI — pinned via build arg, bumped deliberately
ARG CLAUDE_CODE_VERSION=latest
RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION} \
    && claude --version

# Deps go into the SYSTEM python (no venv): tmux panes get a login-shell PATH
# from /etc/profile, so bare `python` — used by launcher mock handling and the
# overcode/tui-eye shims — must already have every dependency.
ENV PIP_ROOT_USER_ACTION=ignore
COPY docker/e2e/requirements-e2e.txt /tmp/requirements-e2e.txt
RUN pip install --no-cache-dir -r /tmp/requirements-e2e.txt

# Playwright Chromium in a world-readable shared location (installed as root,
# used by the non-root test user)
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers
RUN playwright install --with-deps chromium \
    && chmod -R a+rX "$PLAYWRIGHT_BROWSERS_PATH"

RUN useradd -m -s /bin/bash overcode

# Shims so `overcode` / `tui-eye` resolve like real installs while executing
# straight from the mounted source tree.
RUN printf '#!/bin/sh\nexec python -m overcode.cli "$@"\n' > /usr/local/bin/overcode \
    && printf '#!/bin/sh\nexec python -c "from overcode.testing.tui_eye import main; main()" "$@"\n' > /usr/local/bin/tui-eye \
    && chmod +x /usr/local/bin/overcode /usr/local/bin/tui-eye

ENV PYTHONPATH=/workspace/src:/workspace \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    E2E_ARTIFACTS=/artifacts \
    OVERCODE_E2E_CONTAINER=1
WORKDIR /workspace


FROM base AS e2e
COPY docker/e2e/entrypoint.sh /usr/local/bin/e2e-entrypoint.sh
RUN chmod +x /usr/local/bin/e2e-entrypoint.sh
USER overcode
ENTRYPOINT ["/usr/local/bin/e2e-entrypoint.sh"]
CMD ["tests/container"]


FROM base AS dev
RUN apt-get update && apt-get install -y --no-install-recommends \
        zsh less vim jq \
    && rm -rf /var/lib/apt/lists/*
USER overcode
CMD ["/bin/bash"]
