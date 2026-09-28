"""Skill profiles (#499): named sets of skills switched on per agent at launch.

Agent CLIs treat installed skills as always on: anything in ~/.claude/skills
is offered to every Claude session, and opencode reads the same folder. So
people avoid installing skills at all. Profiles turn that around:

- The **library** is where skills live *installed but off*: ~/.overcode/skills
  plus any folders listed under ``skills.library_paths`` in config.yaml (for
  example skill repos checked out in your code root). No agent CLI looks
  there on its own.
- A **profile** is a named list of skill names. Launching an agent with a
  profile builds ~/.overcode/skill-profiles/<profile>/, a folder of links to
  the chosen skills, and hands it to the CLI for that session only (Claude:
  ``--plugin-dir``; opencode: ``OPENCODE_CONFIG_DIR``).
- Personal skills installed globally for that CLI and not in the profile are
  hidden for the session (Claude: ``skillOverrides`` in ``--settings``;
  opencode: ``skill`` deny rules in ``OPENCODE_PERMISSION``). Project skills
  (a repo's own .claude/skills) and the CLI's built-ins are left alone.
- A folder can be **pinned** to a profile, so new agents started in it get
  that profile by default. The pin lives in config.yaml, never in the folder.

Config format in ~/.overcode/config.yaml:
    skills:
      library_paths:
        - ~/Code/team-skills
      profiles:
        research: [shirka, dataviz]
        ios: [swift-helper]
      folders:
        ~/Code/myapp: ios
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import yaml

# Profile names become plugin names (Claude shows skills as "<profile>:<skill>")
# and folder names, so keep them to lowercase kebab-case.
PROFILE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")

# Values of --skills / the launch dialog that mean "no profile, even if the
# folder or parent has one".
NO_PROFILE = "none"

# Skills that were renamed or merged. A profile naming an old one gets the
# new one, but only once the old name no longer matches any skill, so a
# profile written before the rename keeps working either side of it.
RENAMED_SKILLS = {
    "overcode-cli": "overcode",           # frontmatter name before the rewrite
    "delegating-to-agents": "overcode",   # merged into the overcode skill
}

# Walking a code root for SKILL.md files: skip these directory names, and
# don't go deeper than this below each library path.
_SKIP_DIRS = {"node_modules", "__pycache__", "venv", ".venv", "dist", "build", "target"}
_MAX_DEPTH = 6

# Backends that support profiles, and where each one finds *personal*
# (always-on) skills by itself. Verified 2026-09-28: Claude Code 2.1.284 reads
# ~/.claude/skills/<name>/SKILL.md; opencode 1.18.29 scans ~/.claude/skills,
# ~/.agents/skills and ~/.config/opencode/skill(s) recursively.
_PERSONAL_SKILL_DIRS: Dict[str, List[str]] = {
    "claude-code": ["~/.claude/skills"],
    "opencode": ["~/.claude/skills", "~/.agents/skills",
                 "~/.config/opencode/skill", "~/.config/opencode/skills"],
}
# Claude only reads direct children of ~/.claude/skills; opencode recurses.
_PERSONAL_RECURSIVE = {"claude-code": False, "opencode": True}
# What each CLI calls a skill, which is what hide rules must name: Claude
# uses the folder name (~/.claude/skills/overcode is "overcode" even when its
# frontmatter says "overcode-cli"); opencode uses the frontmatter name.
_NAMES_BY_FOLDER = {"claude-code": True, "opencode": False}


@dataclass(frozen=True)
class Skill:
    """One skill folder: a directory containing SKILL.md."""

    name: str          # frontmatter name, else the folder name
    description: str
    path: Path         # the skill's folder
    source: str        # "library", a library path as configured, or a personal dir
    plugin: Optional[str] = None  # enclosing Claude plugin's name, if any

    @property
    def names(self) -> set:
        """Every name a profile may use for this skill: frontmatter and folder."""
        return {self.name, self.path.name}

    def cli_name(self, backend: str) -> str:
        """The name ``backend`` knows this skill by."""
        return self.path.name if _NAMES_BY_FOLDER.get(backend) else self.name


# --- Config --------------------------------------------------------------

def _skills_config() -> dict:
    from .config import load_config
    value = load_config().get("skills")
    return value if isinstance(value, dict) else {}


def _save_skills_config(section: dict) -> None:
    from .config import load_config, save_config
    config = load_config()
    if section:
        config["skills"] = section
    else:
        config.pop("skills", None)
    save_config(config)


def library_dir() -> Path:
    """The library folder overcode owns: skills copied in directly."""
    from .settings import get_overcode_dir
    return get_overcode_dir() / "skills"


def library_paths() -> List[str]:
    """Extra library folders from config, as written (``~`` unexpanded)."""
    paths = _skills_config().get("library_paths") or []
    return [str(p) for p in paths if p]


def library_roots() -> List[Path]:
    """Every folder the library is read from, in precedence order."""
    roots = [library_dir()]
    roots.extend(Path(p).expanduser() for p in library_paths())
    return roots


def add_library_path(path: str) -> None:
    section = _skills_config()
    paths = [str(p) for p in section.get("library_paths") or []]
    if path not in paths:
        paths.append(path)
    section["library_paths"] = paths
    _save_skills_config(section)


def remove_library_path(path: str) -> bool:
    section = _skills_config()
    paths = [str(p) for p in section.get("library_paths") or []]
    target = _norm(path)
    kept = [p for p in paths if p != path and _norm(p) != target]
    if len(kept) == len(paths):
        return False
    if kept:
        section["library_paths"] = kept
    else:
        section.pop("library_paths", None)
    _save_skills_config(section)
    return True


def get_profiles() -> Dict[str, List[str]]:
    """Profile name -> skill names, in config order."""
    raw = _skills_config().get("profiles") or {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): [str(s) for s in (v or [])] for k, v in raw.items()}


def save_profile(name: str, skills: Iterable[str]) -> None:
    if not PROFILE_NAME_RE.match(name) or name == NO_PROFILE:
        raise ValueError(
            f"Profile name '{name}' must be lowercase letters, digits and dashes"
        )
    section = _skills_config()
    profiles = dict(section.get("profiles") or {})
    profiles[name] = list(dict.fromkeys(skills))
    section["profiles"] = profiles
    _save_skills_config(section)


def delete_profile(name: str) -> bool:
    section = _skills_config()
    profiles = dict(section.get("profiles") or {})
    if name not in profiles:
        return False
    del profiles[name]
    if profiles:
        section["profiles"] = profiles
    else:
        section.pop("profiles", None)
    folders = {k: v for k, v in (section.get("folders") or {}).items() if v != name}
    if folders:
        section["folders"] = folders
    else:
        section.pop("folders", None)
    _save_skills_config(section)
    return True


def _norm(path: str) -> str:
    return os.path.normpath(os.path.expanduser(str(path)))


def _display_path(path: str) -> str:
    """``path`` with the home folder written as ``~``, for config and output."""
    home = str(Path.home())
    norm = _norm(path)
    if norm == home or norm.startswith(home + os.sep):
        return "~" + norm[len(home):]
    return norm


def get_folder_pins() -> Dict[str, str]:
    """Folder (as written in config) -> profile name."""
    raw = _skills_config().get("folders") or {}
    return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}


def pin_folder(folder: str, profile: str) -> str:
    """Pin ``folder`` to ``profile``; returns the folder as recorded."""
    key = _display_path(folder)
    section = _skills_config()
    folders = {k: v for k, v in (section.get("folders") or {}).items()
               if _norm(k) != _norm(key)}
    folders[key] = profile
    section["folders"] = folders
    _save_skills_config(section)
    return key


def unpin_folder(folder: str) -> bool:
    section = _skills_config()
    folders = dict(section.get("folders") or {})
    kept = {k: v for k, v in folders.items() if _norm(k) != _norm(folder)}
    if len(kept) == len(folders):
        return False
    if kept:
        section["folders"] = kept
    else:
        section.pop("folders", None)
    _save_skills_config(section)
    return True


def profile_for_folder(directory: Optional[str]) -> Optional[str]:
    """The profile pinned to ``directory`` or its nearest pinned ancestor."""
    if not directory:
        return None
    target = _norm(directory)
    best, best_len = None, -1
    for folder, profile in get_folder_pins().items():
        root = _norm(folder)
        if (target == root or target.startswith(root.rstrip(os.sep) + os.sep)) \
                and len(root) > best_len:
            best, best_len = profile, len(root)
    return best


# --- Scanning ------------------------------------------------------------

def read_skill(skill_md: Path, source: str, plugin: Optional[str] = None) -> Optional[Skill]:
    """Parse one SKILL.md's frontmatter; None when unreadable."""
    try:
        text = skill_md.read_text(errors="replace")
    except OSError:
        return None
    meta: dict = {}
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            try:
                loaded = yaml.safe_load(text[3:end])
                if isinstance(loaded, dict):
                    meta = loaded
            except yaml.YAMLError:
                pass
    name = str(meta.get("name") or skill_md.parent.name).strip()
    description = " ".join(str(meta.get("description") or "").split())
    return Skill(name=name, description=description, path=skill_md.parent,
                 source=source, plugin=plugin)


def _plugin_name(directory: Path) -> Optional[str]:
    manifest = directory / ".claude-plugin" / "plugin.json"
    try:
        return str(json.loads(manifest.read_text()).get("name") or directory.name)
    except (OSError, ValueError, AttributeError):
        return None


def scan_dir(root: Path, source: str, recursive: bool = True) -> List[Skill]:
    """Every skill under ``root``. A skill's own folder is not searched further."""
    found: List[Skill] = []
    if not root.is_dir():
        return found

    def walk(directory: Path, depth: int, plugin: Optional[str]) -> None:
        skill_md = directory / "SKILL.md"
        if skill_md.is_file():
            skill = read_skill(skill_md, source, plugin)
            if skill:
                found.append(skill)
            return
        if depth >= (_MAX_DEPTH if recursive else 1):
            return
        plugin = _plugin_name(directory) or plugin
        try:
            children = sorted(directory.iterdir())
        except OSError:
            return
        for child in children:
            if child.name.startswith(".") or child.name in _SKIP_DIRS:
                continue
            if child.is_dir():
                walk(child, depth + 1, plugin)

    walk(root, 0, None)
    return found


def scan_library() -> List[Skill]:
    """All library skills; on a name clash the earlier root wins."""
    seen: Dict[str, Skill] = {}
    for i, root in enumerate(library_roots()):
        source = "library" if i == 0 else library_paths()[i - 1]
        for skill in scan_dir(root, source):
            seen.setdefault(skill.name, skill)
    return sorted(seen.values(), key=lambda s: s.name)


def library_duplicates() -> Dict[str, List[Path]]:
    """Skill names found in more than one library folder."""
    by_name: Dict[str, List[Path]] = {}
    for i, root in enumerate(library_roots()):
        source = "library" if i == 0 else library_paths()[i - 1]
        for skill in scan_dir(root, source):
            by_name.setdefault(skill.name, []).append(skill.path)
    return {k: v for k, v in by_name.items() if len(v) > 1}


def personal_skills(backend: str) -> List[Skill]:
    """Skills ``backend`` loads by itself in every session (always on)."""
    skills: List[Skill] = []
    seen = set()
    for folder in _PERSONAL_SKILL_DIRS.get(backend, []):
        root = Path(folder).expanduser()
        for skill in scan_dir(root, folder, recursive=_PERSONAL_RECURSIVE.get(backend, True)):
            if skill.name not in seen:
                seen.add(skill.name)
                skills.append(skill)
    return skills


def overlapping_library_paths() -> List[str]:
    """Library paths some CLI already reads, which would make them always on."""
    watched = {_norm(d) for dirs in _PERSONAL_SKILL_DIRS.values() for d in dirs}
    bad = []
    for path in library_paths():
        p = _norm(path)
        if any(p == w or p.startswith(w + os.sep) or w.startswith(p + os.sep) for w in watched):
            bad.append(path)
    return bad


def supports_profiles(backend: str) -> bool:
    return backend in _PERSONAL_SKILL_DIRS


# --- Launch --------------------------------------------------------------

@dataclass(frozen=True)
class ProfileLaunch:
    """What a backend needs to apply a profile to one launch."""

    profile: str
    skill_dir: Optional[str]   # folder of linked skills, None when nothing to add
    hidden: List[str]          # personal skills to hide for this session
    missing: List[str]         # profile skills found nowhere


def profile_dir(profile: str) -> Path:
    from .settings import get_overcode_dir
    return get_overcode_dir() / "skill-profiles" / profile


def resolve_skill_names(wanted: Iterable[str], known: set) -> List[str]:
    """``wanted`` with renamed skills mapped to their new names (RENAMED_SKILLS)."""
    out: List[str] = []
    for name in wanted:
        if name not in known and name in RENAMED_SKILLS:
            name = RENAMED_SKILLS[name]
        if name not in out:
            out.append(name)
    return out


def resolve_profile_name(
    explicit: Optional[str],
    parent_profile: Optional[str],
    directory: Optional[str],
    default: Optional[str],
) -> Optional[str]:
    """Which profile a new agent gets: explicit, parent, pinned folder, default.

    ``"none"`` at any step means no profile and stops the search.
    """
    for candidate in (explicit, parent_profile, profile_for_folder(directory), default):
        if candidate:
            return None if candidate == NO_PROFILE else candidate
    return None


def prepare_profile(profile: str, backend: str) -> Optional[ProfileLaunch]:
    """Build the profile's skill folder for ``backend`` and list what to hide.

    None when the backend doesn't support profiles or the profile is unknown.
    A profile skill the backend already sees as a personal skill is neither
    linked (it's already there) nor hidden.
    """
    if not supports_profiles(backend):
        return None
    wanted = get_profiles().get(profile)
    if wanted is None:
        return None

    personal = personal_skills(backend)
    visible = {n for s in personal for n in s.names}
    library: Dict[str, Skill] = {}
    for skill in scan_library():
        for n in skill.names:
            library.setdefault(n, skill)
    wanted = resolve_skill_names(wanted, visible | set(library))
    wanted_set = set(wanted)

    links: Dict[str, Path] = {}
    missing: List[str] = []
    for name in wanted:
        if name in visible:
            continue
        skill = library.get(name)
        if skill is None:
            missing.append(name)
        else:
            links[skill.name] = skill.path

    hidden = sorted({s.cli_name(backend) for s in personal if not (s.names & wanted_set)})
    skill_dir = str(_write_profile_dir(profile, links)) if links else None
    return ProfileLaunch(profile=profile, skill_dir=skill_dir, hidden=hidden, missing=missing)


def _write_profile_dir(profile: str, links: Dict[str, Path]) -> Path:
    """Make profile_dir(profile) a plugin whose skills/ links to ``links``.

    The same folder serves both CLIs: Claude loads it as a plugin
    (.claude-plugin/plugin.json + skills/), opencode as a config dir
    (skills/). Links are replaced one at a time with os.replace, so a
    concurrent launch of the same profile never sees a half-built folder.
    """
    root = profile_dir(profile)
    skills_dir = root / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)

    manifest = root / ".claude-plugin" / "plugin.json"
    content = json.dumps({
        "name": profile, "version": "1.0.0",
        "description": f"overcode skill profile '{profile}'",
    }, indent=2) + "\n"
    try:
        current = manifest.read_text()
    except OSError:
        current = None
    if current != content:
        manifest.parent.mkdir(parents=True, exist_ok=True)
        tmp = manifest.with_name(f".plugin.json.{os.getpid()}.tmp")
        tmp.write_text(content)
        tmp.replace(manifest)

    # Link names are folder-safe versions of the skill names.
    wanted = {re.sub(r"[^A-Za-z0-9._-]", "-", name): target for name, target in links.items()}
    for entry in skills_dir.iterdir():
        if entry.name not in wanted and entry.is_symlink():
            entry.unlink(missing_ok=True)
    for link_name, target in wanted.items():
        link = skills_dir / link_name
        if link.is_symlink() and os.readlink(link) == str(target):
            continue
        tmp = skills_dir / f".{link_name}.{os.getpid()}.tmp"
        tmp.unlink(missing_ok=True)
        tmp.symlink_to(target, target_is_directory=True)
        os.replace(tmp, link)
    return root


# --- Catalog (the TUI's skills dialog) -----------------------------------

@dataclass(frozen=True)
class CatalogEntry:
    """One row of the skills dialog: a library skill or an always-on one."""

    skill: Skill
    always_on: List[str]   # CLIs that load it in every session ([] for library skills)
    uses: int = 0          # agents that have used it (from recorded skill use)

    @property
    def name(self) -> str:
        return self.skill.name


def catalog(usage: Optional[Dict[str, int]] = None) -> List[CatalogEntry]:
    """Every skill a profile can choose, most used first.

    ``usage`` maps a skill name to how many agents used it; a plugin skill's
    ``plugin:skill`` name counts for ``skill``.
    """
    counts: Dict[str, int] = {}
    for name, n in (usage or {}).items():
        key = name.split(":", 1)[-1]
        counts[key] = counts.get(key, 0) + n

    entries: Dict[str, CatalogEntry] = {}
    for skill in scan_library():
        entries[skill.name] = CatalogEntry(skill, [], 0)
    labels = {"claude-code": "Claude", "opencode": "opencode"}
    for backend in _PERSONAL_SKILL_DIRS:
        for skill in personal_skills(backend):
            if skill.name in entries and entries[skill.name].always_on:
                prev = entries[skill.name]
                entries[skill.name] = CatalogEntry(prev.skill, prev.always_on + [labels[backend]])
            elif skill.name not in entries:
                entries[skill.name] = CatalogEntry(skill, [labels[backend]])

    rows = [CatalogEntry(e.skill, e.always_on, max(counts.get(n, 0) for n in e.skill.names))
            for e in entries.values()]
    return sorted(rows, key=lambda e: (-e.uses, e.name))


def skill_usage(sessions: Iterable) -> Dict[str, int]:
    """How many agents used each skill, from sessions' recorded skill use (#252)."""
    counts: Dict[str, int] = {}
    for session in sessions:
        for name in set(getattr(session, "loaded_skills", None) or []):
            counts[name] = counts.get(name, 0) + 1
    return counts
