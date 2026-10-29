import warnings
from types import SimpleNamespace
from unittest import mock

import pytest

from eflips.depot.filters import VehicleFilter


def _filter(trip):
    vf = VehicleFilter.__new__(VehicleFilter)
    vf.trip = trip
    return vf


def _vehicle(soc, energy_real=350.0):
    battery = SimpleNamespace(
        soc=soc, energy_real=energy_real, energy_remaining=soc * energy_real
    )
    return SimpleNamespace(ID="1", battery=battery)


def _trip(charge_on_track, start_soc=1.0, end_soc=0.1, minimal_soc=0.06):
    return SimpleNamespace(
        charge_on_track=charge_on_track,
        start_soc=start_soc,
        end_soc=end_soc,
        minimal_soc=minimal_soc,
    )


@pytest.fixture(autouse=True)
def soc_given():
    with mock.patch.dict(
        "eflips.depot.filters.globalConstants",
        {"depot": {"consumption_calc_mode": "soc_given"}},
    ):
        yield


class TestSufficientEnergySocGiven:
    def test_opportunity_trip_refuses_vehicle_below_drawdown(self):
        assert not _filter(_trip(True)).filter_sufficient_energy(_vehicle(0.57))

    def test_opportunity_trip_accepts_vehicle_above_drawdown(self):
        assert _filter(_trip(True)).filter_sufficient_energy(_vehicle(0.95))

    def test_opportunity_trip_exceeding_full_battery_dispatches_with_warning(self):
        trip = _trip(True, minimal_soc=-0.37)
        with pytest.warns(UserWarning, match="Dispatching anyway"):
            assert _filter(trip).filter_sufficient_energy(_vehicle(1.0))

    def test_opportunity_trip_exceeding_full_battery_refuses_partial_vehicle(self):
        trip = _trip(True, minimal_soc=-0.37)
        assert not _filter(trip).filter_sufficient_energy(_vehicle(0.9))

    def test_non_opportunity_branch_unchanged(self):
        trip = _trip(False, start_soc=1.0, end_soc=0.1)
        assert not _filter(trip).filter_sufficient_energy(_vehicle(0.5))
        assert _filter(trip).filter_sufficient_energy(_vehicle(0.95))
