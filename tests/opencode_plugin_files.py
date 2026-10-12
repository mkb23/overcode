"""The bundled opencode (v1) telemetry plugin, laid out for node.

The plugin imports the writer it shares with opencode2 from
``overcode-telemetry-v2/overcode-telemetry-core.mjs`` beside it, the way
the installer lays it out in a project's ``.opencode/plugins/``.
"""

from pathlib import Path

from overcode.backends.opencode import bundled_plugin_path
from overcode.backends.opencode2_plugin_install import (
    PLUGIN_DIR_NAME_V2,
    bundled_plugin_dir_v2,
)

CORE_FILENAME = "overcode-telemetry-core.mjs"


def copy_v1_plugin(directory: Path) -> Path:
    """Copy the plugin into ``directory`` as ``.mjs`` (the repo has no
    package.json, so node would parse a bare ``.js`` as CommonJS), with the
    shared core at the relative path it imports. Returns the plugin path."""
    target = directory / "overcode-telemetry.mjs"
    target.write_text(bundled_plugin_path().read_text(encoding="utf-8"))
    core = directory / PLUGIN_DIR_NAME_V2 / CORE_FILENAME
    core.parent.mkdir(parents=True, exist_ok=True)
    core.write_text((bundled_plugin_dir_v2() / CORE_FILENAME).read_text(encoding="utf-8"))
    return target
