"""The engine's freshness contract (docs/design/engine-0.6.md), as constants.

A "fix" that buys CPU by slowing a loop or widening a cache window has to
change one of these numbers, and then this test, in the same diff: the
#476/#517 lesson. Change the design doc's table with it.
"""

import pytest

pytestmark = pytest.mark.unit


def test_hook_driven_status_reaches_views_within_half_a_second_attended():
    from overcode.monitor_daemon import WAKE_SCAN_ATTENDED_SECONDS

    assert WAKE_SCAN_ATTENDED_SECONDS <= 0.5


def test_hook_driven_status_is_recorded_within_two_seconds_unattended():
    from overcode.monitor_daemon import WAKE_SCAN_UNATTENDED_SECONDS

    assert WAKE_SCAN_UNATTENDED_SECONDS <= 2.0


def test_blips_merge_at_g_twenty_seconds():
    from overcode.episodes import EPISODE_MERGE_SECONDS

    assert EPISODE_MERGE_SECONDS == 20.0  # #507: chosen over 10 s and 60 s


def test_views_learn_the_engine_is_alive_every_five_seconds():
    from overcode.engine_socket import PING_SECONDS

    assert PING_SECONDS <= 5.0


def test_burn_window_reuse_stays_inside_its_five_to_thirty_second_contract():
    from overcode.backends.opencode_stats import WINDOW_INDEX_MAX_AGE_SECONDS

    assert 5 <= WINDOW_INDEX_MAX_AGE_SECONDS <= 30


def test_quick_ticks_save_the_state_file_at_least_once_a_second():
    from overcode.monitor_daemon import QUICK_TICK_STATE_SAVE_SECONDS

    assert QUICK_TICK_STATE_SAVE_SECONDS <= 1.0
