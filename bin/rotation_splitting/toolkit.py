import json
import logging
import os
import subprocess

import numpy as np
from eflips.model import *
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from tqdm import tqdm

logger = logging.getLogger("custom")

def get_all_negative_rotations_at_station(self, station_id):
    """
    :return: list of all negative rotations at the given station with their lowest SoC: [[rotation_id, min_soc], ...]
    """
    engine = create_engine(self.database_url)
    with Session(engine) as session:
        stmt = select(StopTime).where(StopTime.scenario_id == self.scenario_id,
                                      StopTime.station_id == station_id).join(Event, Event.trip_id == StopTime.trip_id)
        stoptimes = session.execute(stmt).scalars().all()
        rotations_at_station = []
        for stop in stoptimes:
            rotations_at_station.append({stop.trip.rotation.id: stop.trip.rotation.trips})
        rotations = {}
        for rotation in rotations_at_station:
            for trips in rotation.values():
                min_soc = 1
                for trip in trips:
                    for event in trip.events:
                        if event.soc_end < min_soc:
                            min_soc = event.soc_end
            if trip.rotation_id not in rotations or rotations[trip.rotation_id] < min_soc:
                rotations[trip.rotation_id] = min_soc
        negative_rotations = []
        for rotation in rotations:
            if rotations[rotation] <= 0:
                negative_rotations.append([rotation, rotations[rotation]])
    return negative_rotations


def get_negative_rotations_at_station(self, station_id):
    """
    :return: list of all negative rotations at the given station with their lowest SoC: [[rotation_id, min_soc], ...]
    """
    negative_rotations = []
    engine = create_engine(self.database_url)
    with Session(engine) as session:
        stops = session.query(StopTime).filter(StopTime.station_id == station_id).join(Event,
                                                                                       Event.trip_id == StopTime.trip_id).all()
        for stop in stops:
            for event in stop.trip.events:
                if event.soc_end < 0:
                    if not stop.trip.rotation.id in [i[0] for i in negative_rotations]:
                        negative_rotations.append([stop.trip.rotation.id, event.soc_end])
    return sorted(negative_rotations, key=lambda x: x[1])

def get_station_standby(self) -> list:  # (alternative)
    logger.info("\nGenerating list of electrifiable stations...")
    engine = create_engine(self.database_url)
    with Session(engine) as session:
        terminal_stations = []
        all_routes = session.execute(select(Route).where(Route.scenario_id == self.scenario_id)).scalars().all()
        depots = session.query(Depot).filter(Depot.scenario_id == self.scenario_id).all()
        depot_stations = [d.station_id for d in depots]
        for route in all_routes:
            if route.arrival_station_id not in terminal_stations:
                terminal_stations.append(route.arrival_station_id)
        all_stations = []
        stations = session.execute(select(Station).where(Station.scenario_id == self.scenario_id)).scalars().all()
        for station in tqdm(stations):
            if station.id in terminal_stations and station.id not in depot_stations:
                neg_rotations = len(get_all_negative_rotations_at_station(self, station.id))
                all_stations.append({'station_id': station.id, 'is_electrified': station.is_electrified,
                                     'standby_time': neg_rotations,
                                     # TODO standby_time currently number of negative rotations
                                     'station_name': station.name})
        all_stations.sort(key=lambda x: x['standby_time'])
        logger.info("List generated.")
        session.close()
    return all_stations

def get_station_standby_og(self):
    """
    :return: List of all stations and their total standby time:
     [{station_id, is_electrified, standby_time, station_name}, ...]
    """
    engine = create_engine(self.database_url)
    with Session(engine) as session:
        stmt = select(Station).where(Station.scenario_id == self.scenario_id)
        stations = session.execute(stmt).scalars().all()
        station_standby = []
        tmp_standby = {}
        for station in stations:
            station_standby.append({'station_id': station.id, 'is_electrified': station.is_electrified,
                                    'standby_time': 0, 'station_name': station.name})
            tmp_standby[station.id] = 0
        stmt = select(Vehicle).where(Vehicle.scenario_id == self.scenario_id)
        vehicles = session.execute(stmt).scalars().all()
        for vehicle in vehicles:
            stmt = select(Event).where(Event.vehicle_id == vehicle.id).order_by(Event.time_start)
            vehicle_events = session.execute(stmt).scalars().all()
            for i in range(len(vehicle_events) - 1):
                if vehicle_events[i].event_type == EventType.DRIVING and \
                        vehicle_events[i + 1].event_type == EventType.DRIVING:
                    tmp_standby[vehicle_events[i].trip.route.arrival_station_id] += \
                        int((vehicle_events[i + 1].time_start - vehicle_events[i].time_end).total_seconds() / 60)
        for i in station_standby:
            i['standby_time'] = tmp_standby[i['station_id']]
        station_standby.sort(key=lambda x: x['standby_time'])
        return station_standby

def get_eligible_stations(self) -> list:
    """
    :return: List of all stations and their total standby time above the given percentile:
    [{station_id, is_electrified, standby_time, station_name}, ...]
    """
    station_standby_filtered = [station for station in self.station_standby if station.get('standby_time') > 0]
    standby_times = [station['standby_time'] for station in station_standby_filtered]
    cut_off = np.percentile(standby_times, self.percentile)
    eligible_stations = [station for station in station_standby_filtered if station['standby_time'] >= cut_off]
    return eligible_stations


class Toolkit:

    def __init__(self, database_url, scenario_id, percentile, tmp=False):
        self.database_url = database_url
        self.scenario_id = scenario_id
        self.percentile = percentile
        if not tmp:
            #self.station_standby = get_station_standby(self)
            self.station_standby = get_station_standby_og(self)
            self.eligible_stations = get_eligible_stations(self)


    def run_single_step(self):
        engine = create_engine(self.database_url)
        with Session(engine) as session:
            oc_stations = session.query(Station).filter(Station.is_electrified,
                                                        Station.scenario_id == self.scenario_id).all()
            script_path = os.path.join(os.path.dirname(__file__), 'single_step.py')
            logger.info("\nRunning single-step-electrification...")
            try:
                subprocess.run(
                    ['python', script_path, f'--database_url={self.database_url}',
                     f'--scenario_id={self.scenario_id}'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
                )
            except subprocess.CalledProcessError as e:
                logger.info(f"\nAn error occurred while running {script_path}: {e}")
            oc_stations_after_new = session.query(Station).filter(Station.is_electrified,
                                                                  Station.scenario_id == self.scenario_id).all()
            new_station = [station.id for station in oc_stations_after_new if station not in oc_stations]
            logger.info(f"Finished single-step-electrification.")
            return new_station

    def run_depot_sim(self, visualize=False):
        script_path = os.path.join(os.path.dirname(__file__), 'depot_sim.py')
        try:
            logger.info("\nRunning depot simulation...")
            if visualize:
                subprocess.run(
                    ['python', script_path, f'--database_url={self.database_url}',
                     f'--scenario_id={self.scenario_id}', '--visualize'], stderr=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, check=True
                )
            elif not visualize:
                subprocess.run(
                    ['python', script_path, f'--database_url={self.database_url}',
                     f'--scenario_id={self.scenario_id}', ], stderr=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, check=True
                )
        except subprocess.CalledProcessError as e:
            logger.info(f"\nAn error occurred while running {script_path}: {e}")
        logger.info(f'Finished depot simulation')

    def run_consumption_sim(self):
        script_path = os.path.join(os.path.dirname(__file__), 'consumption_sim.py')
        try:
            logger.info(f"\nRunning consumption simulation...")
            subprocess.run(['python', script_path, f'--scenario_id={self.scenario_id}',
                            f'--database_url={self.database_url}'], check=True)
            logger.info("Finished consumption simulation.")
        except subprocess.CalledProcessError as e:
            logger.info(f"An error occurred while running {script_path}: {e}")

    def undo_electrification(self, station_id):
        engine = create_engine(self.database_url)
        with Session(engine) as session:
            station = session.query(Station).where(Station.id == station_id).first()
            station.is_electrified = False
            session.commit()

    def get_rotation_info(self, rotations):
        engine = create_engine(self.database_url)
        single_rotation = None
        multiple_rotations = []
        with Session(engine) as session:
            if type(rotations) is int:
                r = session.execute(select(Rotation).where(Rotation.scenario_id == self.scenario_id,
                                                           Rotation.id == rotations)).scalars().first()
                single_rotation = {'rotation_id': r.id,
                                   'name': r.name,
                                   'vehicle_id': r.vehicle_id}
                return single_rotation
            if type(rotations) is list:
                for rotation in rotations:
                    r = session.execute(select(Rotation).where(Rotation.scenario_id == self.scenario_id,
                                                               Rotation.id == rotation)).scalars().first()
                    multiple_rotations.append({'rotation_id': r.id,
                                               'name': r.name,
                                               'vehicle_id': r.vehicle_id})
                return multiple_rotations

    def run_rotation_split(self, rotation_id):
        engine = create_engine(self.database_url)
        script_path = os.path.join(os.path.dirname(__file__), 'split_rotation.py')
        try:
            with Session(engine) as session:
                rotation = session.query(Rotation).where(Rotation.id == rotation_id).first()
                logger.info(f"\nRunning rotation split for rotation {rotation.name} ({rotation.id})")
                session.close()
            subprocess.run(['python', script_path, f'--scenario_id={self.scenario_id}',
                            f'--database_url={self.database_url}', f'--rotation={rotation_id}'], check=True)
            logger.info("Rotation split completed.")
        except subprocess.CalledProcessError as e:
            logger.info(f"An error occurred while running {script_path}: {e}")

    def get_stations(self, electrified=False):
        engine = create_engine(self.database_url)
        with Session(engine) as session:
            if electrified:
                stations = session.execute(
                    select(Station).filter(Station.is_electrified,
                                           Station.scenario_id == self.scenario_id)).scalars().all()
            if not electrified:
                stations = session.execute(
                    select(Station).filter(Station.scenario_id == self.scenario_id)).scalars().all()

            station_dict = {}
            for station in stations:
                station_dict[station.id] = station.name
            session.close()
        return station_dict

    def get_vehicle_count(self, name_short="ANY"):
        engine = create_engine(self.database_url)
        with Session(engine) as session:
            if name_short == "ANY":
                count = session.query(Vehicle).where(Vehicle.scenario_id == self.scenario_id).count()
            else:
                count = session.query(Vehicle).filter(Vehicle.scenario_id == self.scenario_id,
                                                      Vehicle.name_short == name_short).count()
            session.close()
        return count

    def get_rotation_count(self):
        engine = create_engine(self.database_url)
        with Session(engine) as session:
            count = session.query(Rotation).where(Rotation.scenario_id == self.scenario_id).count()
            session.close()
        return count

    def json_dump(self, result: dict):
        engine = create_engine(self.database_url)
        with Session(engine) as session:
            depot_name = session.query(Scenario.name).where(Scenario.id == self.scenario_id).scalar()
        subdir = os.path.join(os.getcwd(), f"results/{depot_name}")
        if not os.path.exists(subdir):
            os.makedirs(subdir)
        filepath = os.path.join(subdir, "results.json")
        if not os.path.exists(filepath):
            with open(filepath, "w", encoding="utf8") as f:
                json.dump(result, f, indent=4, ensure_ascii=False)
        else:
            with open(filepath, "r") as f:
                data = json.load(f)
            if isinstance(data, list):
                data.append(result)
            else:
                data = [data, result]
            with open(filepath, "w", encoding="utf8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)

    def remove_obsolete_stations(self):
        logger.info("\nRemoving obsolete stations...")
        engine = create_engine(self.database_url)
        with (Session(engine) as session):
            def consumption_sim():
                from eflips.depot.api import simple_consumption_simulation
                import warnings
                scenario = session.query(Scenario).filter(Scenario.id == self.scenario_id).one()
                rotation_q = session.query(Rotation).filter(Rotation.scenario_id == scenario.id)
                rotation_q.update({"vehicle_id": None})
                session.query(Event).filter(Event.scenario_id == scenario.id).delete()
                session.query(Vehicle).filter(Vehicle.scenario_id == scenario.id).delete()
                warnings.simplefilter("ignore", category=ConsistencyWarning)
                simple_consumption_simulation(scenario=scenario, initialize_vehicles=True)

            stations_q = session.query(Station).where(Station.scenario_id == self.scenario_id,
                                                      Station.is_electrified).all()

            stations = []
            for station in stations_q:
                stations.append([station, [st['standby_time']
                                           for st in self.station_standby if st['station_id'] == station.id]])
            stations.sort(key=lambda x: x[1])
            for station in stations:
                station[0].is_electrified = False
                consumption_sim()
                if session.query(Event).filter(Event.scenario_id == self.scenario_id,
                                               Event.soc_end < 0).count() == 0:
                    session.commit()
                    logger.info(f"Scenario can be electrified without opportunity charging at {station[0].name}.")
                else:
                    session.rollback()
        logger.info("\nObsolete stations removed.")
