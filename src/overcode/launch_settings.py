"""
Which settings a new agent launches with (#433, #499).

A pure function of what the caller asked for, the parent agent and the
``new_agent_defaults`` config: no tmux, no files, no session store. The
launcher validates the result (backend known, CLI installed, provider and
wrapper usable) and acts on it.

Precedence, per setting:

- backend: explicit > parent (unless the parent is the overagent) >
  config default > built-in default.
- provider, model: explicit > parent, but only when the child runs on the
  parent's backend (model ids and transports are backend grammar) >
  config default (provider only) > ``"web"`` / the CLI's own default.
- wrapper: explicit > parent > config default.
- agent_teams, permission mode: explicit > parent > off / ``"normal"``.
- skill profile: explicit > parent > the folder's pinned profile > config
  default; ``"none"`` at any step means no profile.

``inherit_parent_settings=False`` skips every parent step.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

from .backends import DEFAULT_BACKEND
from .skill_library import NO_PROFILE


@dataclass(frozen=True)
class LaunchArgs:
    """What the caller of ``AgentLauncher.launch`` asked for (None = not set)."""

    backend: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    wrapper: Optional[str] = None
    agent_teams: bool = False
    skip_permissions: bool = False
    dangerously_skip_permissions: bool = False
    skill_profile: Optional[str] = None
    inherit_parent_settings: bool = True


@dataclass(frozen=True)
class LaunchPlan:
    """The settings a new agent launches with, before validation."""

    backend: str
    provider: str
    model: Optional[str]
    wrapper: Optional[str]  # as named; the launcher resolves it to a path
    agent_teams: bool
    permissiveness_mode: str  # "normal" | "permissive" | "bypass"
    skill_profile: Optional[str]


def _no_folder_profile() -> Optional[str]:
    return None


def resolve_launch_settings(
    args: LaunchArgs,
    parent: Optional[Any],
    defaults: Mapping[str, Any],
    folder_profile: Callable[[], Optional[str]] = _no_folder_profile,
) -> LaunchPlan:
    """Resolve a new agent's settings (see the module docstring).

    Args:
        args: what the caller asked for.
        parent: the parent ``Session``, or None for a top-level agent.
        defaults: ``config.get_new_agent_defaults()``.
        folder_profile: the skill profile pinned to the agent's folder;
            only called when neither the caller nor the parent names one.
    """
    from .overagent import OVERAGENT_BACKEND

    backend = args.backend
    provider = args.provider
    model = args.model
    wrapper = args.wrapper
    agent_teams = args.agent_teams
    skip_permissions = args.skip_permissions
    dangerously_skip = args.dangerously_skip_permissions

    inherit = parent is not None and args.inherit_parent_settings
    if inherit:
        if backend is None:
            backend = getattr(parent, "backend", None) or None
            # The overagent persona is one per install: its children are
            # ordinary agents, not more overagents.
            if backend == OVERAGENT_BACKEND:
                backend = None
        # Model ids and provider transports are backend-specific grammar —
        # a Claude `sonnet`/`claude-fable-5` or a bedrock pin means nothing
        # to codex/grok (and vice versa), so they only flow to a child on
        # the same backend. A cross-backend child falls through to config
        # defaults / the CLI's own default model instead.
        parent_backend = getattr(parent, "backend", None) or DEFAULT_BACKEND
        same_backend = backend is None or backend == parent_backend
        if provider is None and same_backend:
            provider = parent.provider
        if model is None and same_backend:
            model = parent.model
        if wrapper is None:
            wrapper = parent.wrapper
        if not agent_teams:
            agent_teams = parent.agent_teams
        if not skip_permissions and not dangerously_skip:
            if parent.permissiveness_mode == "bypass":
                dangerously_skip = True
            elif parent.permissiveness_mode == "permissive":
                skip_permissions = True

    if provider is None:
        provider = defaults.get("provider") or "web"
    if wrapper is None:
        wrapper = defaults.get("wrapper") or None
    if backend is None:
        backend = defaults.get("backend") or DEFAULT_BACKEND

    if dangerously_skip:
        perm_mode = "bypass"
    elif skip_permissions:
        perm_mode = "permissive"
    else:
        perm_mode = "normal"

    return LaunchPlan(
        backend=backend,
        provider=provider,
        model=model,
        wrapper=wrapper,
        agent_teams=agent_teams,
        permissiveness_mode=perm_mode,
        skill_profile=_resolve_skill_profile(
            args.skill_profile,
            parent.skill_profile if inherit else None,
            folder_profile,
            defaults.get("skill_profile"),
        ),
    )


def _resolve_skill_profile(
    explicit: Optional[str],
    parent_profile: Optional[str],
    folder_profile: Callable[[], Optional[str]],
    default: Optional[str],
) -> Optional[str]:
    """``skill_library.resolve_profile_name``, reading the folder pin only if needed."""
    for candidate in (explicit, parent_profile):
        if candidate:
            return None if candidate == NO_PROFILE else candidate
    for candidate in (folder_profile(), default):
        if candidate:
            return None if candidate == NO_PROFILE else candidate
    return None
