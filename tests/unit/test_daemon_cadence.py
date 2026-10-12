from datetime import datetime, timedelta

from overcode.daemon_cadence import Cadence

T0 = datetime(2026, 10, 12, 12, 0, 0)


def test_due_at_once_then_after_its_interval():
    c = Cadence(10)
    assert c.due(T0)
    c.mark(T0)
    assert not c.due(T0 + timedelta(seconds=9.9))
    assert c.due(T0 + timedelta(seconds=10))


def test_an_interval_can_be_given_per_call():
    c = Cadence(5)
    c.mark(T0)
    assert c.due(T0 + timedelta(seconds=5))
    assert not c.due(T0 + timedelta(seconds=5), 60)
