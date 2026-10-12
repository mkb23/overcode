"""Timing budgets, scaled for slower machines.

The millisecond budgets in tests/scale were calibrated on an M-series Mac.
A hosted CI runner is about 3x slower, so CI sets OVERCODE_SCALE_TIME_FACTOR
(see .github/workflows/full-suite.yml). Count budgets (writes, reads, tmux
commands per tick) are machine-independent and never scaled.
"""

import os

TIME_FACTOR = float(os.environ.get("OVERCODE_SCALE_TIME_FACTOR") or 1.0)


def budget(limit):
    """``limit`` (a time) as allowed on this machine."""
    return limit * TIME_FACTOR
