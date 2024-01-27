#! /usr/bin/env python3
from multiprocessing import Pool
from typing import Dict, Tuple

from eflips.model import *
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from tqdm.auto import tqdm

DATABASE_URL = "postgresql://ludger:@/bvg_schedule_all?host=/var/run/postgresql"
engine = create_engine(DATABASE_URL, echo=False)


def find_first_last_stop_for_rotation_id(rotation_id: int) -> Tuple[int, int]:
    """
    Finds the first and last stop for a given rotation id
    """
    rotation_id = rotation_id[0]
    with Session(engine) as session:
        rotation = session.query(Rotation).filter(Rotation.id == rotation_id).one()
        first_stop = rotation.trips[0].route.departure_station_id
        last_stop = rotation.trips[-1].route.arrival_station_id
        return first_stop, last_stop


def group_rotations_by_start_end_stop() -> Dict[Tuple[int, int], int]:
    """
    Groups rotations by start, end and stop
    """
    with Session(engine) as session:
        rotation_ids = (
            session.query(Rotation.id).filter(Rotation.scenario_id == 1).all()
        )
        grouped_rotations: Dict[Tuple[int, int], int] = {}
        with Pool() as pool:
            for result in tqdm(
                pool.imap_unordered(find_first_last_stop_for_rotation_id, rotation_ids),
                total=len(rotation_ids),
            ):
                key = (result[0], result[1])
                if key not in grouped_rotations:
                    grouped_rotations[key] = 0
                grouped_rotations[key] += 1
        return grouped_rotations


def print_rotations_by_station() -> None:
    grouped_rotations = group_rotations_by_start_end_stop()

    # order by length of list
    sorted_dict = {
        k: v
        for k, v in sorted(
            grouped_rotations.items(), key=lambda item: item[1], reverse=True
        )
    }
    with Session(engine) as session:
        for key, rotations in sorted_dict.items():
            first_station_id, last_station_id = key
            first_station = (
                session.query(Station).filter(Station.id == first_station_id).one()
            )
            last_station = (
                session.query(Station).filter(Station.id == last_station_id).one()
            )
            print(
                f"{first_station.name} ({first_station_id}) -> {last_station.name} ({last_station_id}): {rotations}"
            )


def prune_scenario(station: Station, scenario_id: int, session: Session) -> None:
    """
    Creates a scenario with all the rotations starting and ending at the given station
    """
    # Find all the rotations *not* starting and ending at this station
    rotations = (
        session.query(Rotation).filter(Rotation.scenario_id == scenario_id).all()
    )
    for rotation in tqdm(rotations):
        for trip in rotation.trips:
            for stop_time in trip.stop_times:
                session.delete(stop_time)
            session.delete(trip)
        session.delete(rotation)


if __name__ == "__main__":
    # print_rotations_by_station()

    # Load the most interesting stations and take the first six
    grouped_rotations = group_rotations_by_start_end_stop()
    # order by length of list
    sorted_dict = {
        k: v
        for k, v in sorted(
            grouped_rotations.items(), key=lambda item: item[1], reverse=True
        )
    }

    # Take the first six
    stationss = list(sorted_dict.keys())[:6]
    for stations in stationss:
        assert len(stations) == 2
        assert stations[0] == stations[1]
        # Copy a scenario
        with Session(engine) as session:
            try:
                station_name = (
                    session.query(Station).filter(Station.id == stations[0]).one().name
                )
                station_short_name = (
                    session.query(Station)
                    .filter(Station.id == stations[0])
                    .one()
                    .name_short
                )

                scenario = session.query(Scenario).filter(Scenario.id == 1).one()
                new_scenario = scenario.clone(session)
                new_scenario.name = (
                    f"All Rotations starting and ending at {station_name}"
                )

                # Find this station in the new scenario
                station = (
                    session.query(Station)
                    .filter(Station.scenario_id == new_scenario.id)
                    .filter(Station.name == station_name)
                    .filter(Station.name_short == station_short_name)
                    .one()
                )

                prune_scenario(station, new_scenario.id, session)
            except Exception as e:
                session.rollback()
                raise e
            finally:
                session.commit()
