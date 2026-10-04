"""pytest -m race -- see tests/race_check.py: simultaneous bookings, ship / invoice / payment double-clicks,
stale saves and a busy day, over real HTTP against a throwaway copy of the data."""
import pytest

pytestmark = pytest.mark.race


def test_races_are_handled():
    from tests.race_check import main
    assert main(), "a race slipped through -- see the FAIL lines above"
