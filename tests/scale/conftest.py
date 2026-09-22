"""Scale-budget tests: opt-in, built on ``scripts/bench_scaling.py``.

Everything under ``tests/scale`` is skipped unless ``OVERCODE_SCALE_TESTS=1``
— the fixture is a 100 MB synthetic state tree and the daemon budget alone
runs the production tick body over 50 agents, which is far too slow for the
unit lane. Run with:

    OVERCODE_SCALE_TESTS=1 uv run pytest tests/scale -q -p no:cacheprovider
"""

import os
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

_ENABLED = os.environ.get("OVERCODE_SCALE_TESTS") == "1"
_SKIP = pytest.mark.skip(reason="scale tests are opt-in: set OVERCODE_SCALE_TESTS=1")


def pytest_collection_modifyitems(config, items):
    here = Path(__file__).resolve().parent
    for item in items:
        if here in Path(str(item.fspath)).resolve().parents:
            item.add_marker(pytest.mark.scale)
            if not _ENABLED:
                item.add_marker(_SKIP)


@pytest.fixture(scope="session")
def scale_fixture(tmp_path_factory):
    """The ``--quick`` fixture, built once per session, with the code patched onto it.

    Yields the ``FixturePaths``; every production module reads from and
    writes to this tree for the whole session (see
    ``bench_scaling.patched_environment``).
    """
    import bench_scaling

    spec = bench_scaling.FixtureSpec.quick()
    root = tmp_path_factory.mktemp("overcode-scale")
    paths = bench_scaling.build_fixture(spec, root, quiet=True)
    with bench_scaling.patched_environment(paths):
        yield paths
