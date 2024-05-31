from datetime import datetime

from eflips.model import Event, EventType

from eflips.depot.api import apply_even_smart_charging
from smart_charging.base import BaseTest


class TestSmartCharging(BaseTest):
    def test_fixtures(
        self, session, scenario, post_simulation_scenario_no_smart_charging
    ):
        assert scenario == post_simulation_scenario_no_smart_charging
        assert len(scenario.events) > 0

    def test_smart_charging(self, session, post_simulation_scenario_no_smart_charging):
        # Remember the start and end SoC for each ChargingEvent
        all_charging_events = (
            session.query(Event)
            .filter(Event.event_type == EventType.CHARGING_DEPOT)
            .all()
        )
        start_socs = for_event = {}
        end_socs_for_event = {}
        for event in all_charging_events:
            start_socs[event.id] = event.soc_start
            end_socs_for_event[event.id] = event.soc_end

        apply_even_smart_charging(post_simulation_scenario_no_smart_charging)

        # Check that the
        # - socs match before the optimization
        # - the timeseries time is monotonically increasing
        # - the socs are monotonically increasing
        for event in all_charging_events:
            assert start_socs[event.id] == event.soc_start
            assert end_socs_for_event[event.id] <= event.soc_end
            times = [datetime.fromisoformat(t) for t in event.timeseries["time"]]
            socs = event.timeseries["soc"]
            assert all(times[i] <= times[i + 1] for i in range(len(times) - 1))
            assert all(socs[i] <= socs[i + 1] for i in range(len(socs) - 1))

            assert event.soc_start <= event.soc_end
            assert event.soc_start <= socs[0]
            assert event.soc_end >= socs[-1]

            assert event.time_start <= times[0]
            assert event.time_end >= times[-1]

        session.flush()
