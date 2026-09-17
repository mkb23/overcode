"""opencode2 telemetry plugin install — v2 TUI-plugin staging.

Verified against opencode2 v0.0.0-dev-19272 (live, Sep 15 2026): v2's server
scans ``<start_directory>/.opencode/plugins/`` and loads every entry; a TUI
plugin must ship as a **subdirectory** containing a server entrypoint
(``index.js``, validated as ``{id, effect|setup}``) plus the TUI entrypoint the
server registers under ``features.tui`` (``tui.js``, whose default export must
be ``{id, setup}``). Loose ``.js`` files load server-side only — the TUI never
runs them, so telemetry installed that way is inert. The installer therefore
copies the bundled plugin directory ``overcode-telemetry-v2/`` (index.js +
tui.js + the shared reducer core tui.js imports) into the project's plugins
dir.

Same project-local-copy design and marker semantics as the v1 installer
(``backends/opencode.py``): a target file without the marker line is the
user's own and is never touched; an overcode copy is refreshed in place when
the bundled content has moved on. The plugin itself no-ops without the
``OVERCODE_*`` env vars, so a stray copy is inert.
"""

import os
from pathlib import Path
from typing import Optional

# The bundled v2 telemetry plugin ships as a directory (server entrypoint +
# TUI entrypoint + shared core); the installer copies it under the project's
# plugins dir so both entrypoints resolve.
PLUGIN_DIR_NAME_V2 = "overcode-telemetry-v2"
PLUGIN_FILES_V2 = ("index.js", "tui.js", "overcode-telemetry-core.mjs")
PLUGIN_DIR_PARTS = (".opencode", "plugins")
# A line inside every bundled file that identifies it as overcode's. A file
# without it is the user's own and is never touched.
PLUGIN_MARKER_V2 = "OVERCODE-PLUGIN-MARKER: overcode-telemetry-v2"


def bundled_plugin_dir_v2() -> Path:
    """The telemetry plugin directory shipped inside the overcode package."""
    return Path(__file__).parent.parent / "opencode_plugin" / PLUGIN_DIR_NAME_V2


def project_plugin_dir_v2(start_directory: str) -> Path:
    """Where the v2 plugin directory has to live for a project-scoped launch."""
    return Path(start_directory).joinpath(*PLUGIN_DIR_PARTS) / PLUGIN_DIR_NAME_V2


def _ensure_file(source: Path, target: Path) -> bool:
    """Copy ``source`` to ``target`` with the v1 installer's semantics.

    Missing → written; present and ours → rewritten when the bundled content
    has moved on; present and identical → left alone; present and not ours →
    left alone. Returns True when ``target`` ends up carrying our content.
    An unreadable target is NOT missing — only ``FileNotFoundError`` means
    absent, so any other read error aborts (returns False) without writing.
    """
    try:
        content = source.read_text(encoding="utf-8")
    except OSError:
        return False

    try:
        existing = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        existing = None
    except OSError:
        # Unreadable (e.g. PermissionError) in a writable directory:
        # never treat as missing, never clobber.
        return False

    if existing is not None:
        if PLUGIN_MARKER_V2 not in existing:
            return False
        if existing == content:
            return True

    tmp = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        # The staging write or the replace failed: the target keeps its
        # previous content, and the tmp must not be stranded next to it.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True


def ensure_plugin_installed(start_directory: Optional[str]) -> Optional[Path]:
    """Copy the v2 plugin directory into ``<dir>/.opencode/plugins/``.

    Every bundled file is required: index.js is what the server loads (and how
    the TUI discovers the plugin), tui.js is the TUI entrypoint, and the core
    is what tui.js imports by relative path. All carry the marker line and
    follow the same non-destructive semantics as the v1 installer (see
    ``backends.opencode.ensure_plugin_installed``): a target file without the
    marker is the user's own and is never touched.

    Returns the installed plugin directory, or None when any file could not
    be installed (the launch proceeds without telemetry and status falls
    back to pane polling).
    """
    if not start_directory:
        return None
    source_dir = bundled_plugin_dir_v2()
    target_dir = project_plugin_dir_v2(start_directory)
    # Pre-flight: verify EVERY existing target carries our marker before
    # writing anything. Without this, a user-owned file late in the
    # sequence would fail the install only after overcode's earlier files
    # were already (re)written — a partial install next to the user's file.
    # Only FileNotFoundError counts as "missing"; any other read error
    # (e.g. an unreadable file in a writable directory) aborts the
    # install without writing anything.
    for name in PLUGIN_FILES_V2:
        try:
            existing = (target_dir / name).read_text(encoding="utf-8")
        except FileNotFoundError:
            continue  # missing target: will be written below
        except OSError:
            return None  # unreadable: never treat as missing, never clobber
        if PLUGIN_MARKER_V2 not in existing:
            return None  # user-owned: never touched, and nothing else written
    for name in PLUGIN_FILES_V2:
        if not _ensure_file(source_dir / name, target_dir / name):
            return None
    return target_dir


def plugin_installed(start_directory: Optional[str]) -> bool:
    """True when this project directory carries overcode's v2 telemetry files."""
    if not start_directory:
        return False
    target_dir = project_plugin_dir_v2(start_directory)
    for name in PLUGIN_FILES_V2:
        try:
            if PLUGIN_MARKER_V2 not in (target_dir / name).read_text(encoding="utf-8"):
                return False
        except OSError:
            return False
    return True
