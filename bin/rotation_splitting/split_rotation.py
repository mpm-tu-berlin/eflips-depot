import argparse
from datetime import timedelta, datetime

from eflips.model import *
from sqlalchemy import create_engine, select, func, delete, update
from sqlalchemy.orm import Session


def calculate_distance():
    # TODO calculate distance between departure and arrival station using the geom WKB Elements
    return 1234


def calculate_trip_duration(departure_station, arrival_station):
    # TODO calculate trip duration
    return timedelta(minutes=5)


def generate_soc_timeseries(start_time, duration, start_soc):
    """
    with Session(engine) as session_gts:
        stmt = select(Event).where(Event.event_type == EventType.DRIVING)
        events = session_gts.execute(stmt).scalars().all()
        rotation_events = []
        for event in events:
            if event.vehicle_id == old_rotation.vehicle_id:
                rotation_events.append(event.timeseries)
        charge = 0
        minutes = 0
        for i in rotation_events:
            charge += i['soc'][0] - i['soc'][-1]
            minutes += (datetime.fromisoformat(i['time'][-1]) - datetime.fromisoformat(
                i['time'][0])).total_seconds() / 60

        charge_per_minute = charge / minutes
    """
    charge_per_minute = 0.0003655   # value from random rotation

    new_timeseries = {'soc': [start_soc], 'time': [start_time.isoformat()]}

    for i in range(int(duration.total_seconds() / 60)):
        new_timeseries['soc'].append(new_timeseries['soc'][i] - charge_per_minute)
        x = datetime.fromisoformat(new_timeseries['time'][i]) + timedelta(minutes=1)
        new_timeseries['time'].append(x.isoformat())

    return new_timeseries


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario_id",
        type=int,
    )
    parser.add_argument(
        "--database_url",
        type=str,
    )
    parser.add_argument(
        "--rotation_id",
        type=int,
    )
    args = parser.parse_args()

    engine = create_engine(args.database_url)
    SCENARIO_ID = args.scenario_id
    ROTATION = args.rotation_id

    ### split the rotation into rotation_a and rotation_b and save the last trip of rotation_a and the first trip of rotation_b
    with Session(engine) as session:
        stmt_tr = select(Trip).where(Trip.scenario_id == SCENARIO_ID, Trip.rotation_id == ROTATION). \
            join(Route, Trip.route_id == Route.id).order_by(Trip.departure_time)
        tr = session.execute(stmt_tr).scalars().all()
        trips = []
        for trip in tr:
            trips.append(
                {
                    'trip_id': trip.id,
                    'trip_type': trip.trip_type,
                    'rotation_id': trip.rotation_id,
                    'route_id': trip.route_id,
                    'route_name': trip.route.name,
                    'departure_station': trip.route.departure_station,
                    'arrival_station': trip.route.arrival_station,
                    'line_id': trip.route.line_id,
                    'scenario_id': trip.scenario_id,
                    'departure_time': trip.departure_time,
                    'arrival_time': trip.arrival_time,
                }
            )
        session.close()

    einsetzfahrt = None
    passenger_trips = []
    aussetzfahrt = None

    for trip in trips:
        if trip['trip_type'] == TripType.EMPTY:
            if einsetzfahrt is None:
                einsetzfahrt = trip
            elif aussetzfahrt is None:
                aussetzfahrt = trip
        else:
            passenger_trips.append(trip)

    rotation_a = [einsetzfahrt]
    for i in range(int(len(passenger_trips) / 2)):
        rotation_a.append(passenger_trips[i])
    last_trip_a = rotation_a[-1]

    rotation_b = []
    for i in passenger_trips[int(len(passenger_trips) / 2):]:
        rotation_b.append(i)
    rotation_b.append(aussetzfahrt)

    first_trip_b = rotation_b[0]

    ### query the rotation and generate IDs for new database entries

    with Session(engine) as session:

        old_rotation = session.query(Rotation).where(Rotation.id == ROTATION).scalar()

        new_route_id = session.query(func.max(Route.id)).scalar() + 1
        new_trip_id = session.query(func.max(Trip.id)).scalar() + 1
        new_rotation_id = session.query(func.max(Rotation.id)).scalar() + 1
        new_event_id = session.query(func.max(Event.id)).scalar() + 1
        new_stoptime_id = session.query(func.max(StopTime.id)).scalar() + 1
        new_vehicle_id = session.query(func.max(Vehicle.id)).scalar() + 1

    ### generate vehicle ###
    with Session(engine) as session_gv:
        new_vehicle = Vehicle(id=new_vehicle_id, name='new_vehicle', vehicle_type_id=old_rotation.vehicle_type_id,
                              scenario_id=old_rotation.scenario_id)
        session_gv.add(new_vehicle)
        session_gv.commit()
    print('\tVehicle created: ', new_vehicle_id)

    ### generate rotations ###
    # with Session(engine) as session:
    #     line_name = session.query(Line.name).where(Line.id == last_trip_a['line_id']).scalar()
    rotation_name = f"{old_rotation.name} new"

    rot_a = Rotation(id=new_rotation_id, name=rotation_name, scenario_id=last_trip_a['scenario_id'],
                     allow_opportunity_charging=True, vehicle_id=old_rotation.vehicle_id,
                     vehicle_type_id=old_rotation.vehicle_type_id)
    rot_b = Rotation(id=new_rotation_id + 1, name=rotation_name, scenario_id=first_trip_b['scenario_id'],
                     allow_opportunity_charging=True, vehicle_id=new_vehicle_id,
                     vehicle_type_id=old_rotation.vehicle_type_id)

    session.add(rot_a)
    session.add(rot_b)

    session.commit()
    print(f'\tRotations created: {new_rotation_id}, {new_rotation_id+1}')

    ### generate routes ###
    # if aussetzfahrt is None:
    #     aszfahrt = aussetzfahrt
    with Session(engine) as session_route:
        line_name = session_route.query(Line.name).where(Line.id == last_trip_a['line_id']).scalar()

    # aussetzfahrt
    arrival_station = aussetzfahrt['arrival_station']
    new_name = f"{line_name} Aussetzfahrt {last_trip_a['arrival_station'].name} → {arrival_station.name}"
    new_name_short = f"{line_name} {last_trip_a['arrival_station'].name_short} → {arrival_station.name_short}"

    aszfahrt = Route(id=new_route_id, scenario_id=last_trip_a['scenario_id'], line_id=last_trip_a['line_id'],
                     name=new_name,
                     name_short=new_name_short, arrival_station_id=arrival_station.id,
                     departure_station_id=last_trip_a['arrival_station'].id, distance=calculate_distance())

    # einsetzfahrt
    departure_station = einsetzfahrt['departure_station']
    new_name = f"{line_name} Einsetzfahrt {departure_station.name} → {first_trip_b['departure_station'].name}"
    new_name_short = f"{line_name} {departure_station.name_short} → {first_trip_b['departure_station'].name_short}"

    eszfahrt = Route(id=(new_route_id + 1), scenario_id=first_trip_b['scenario_id'],
                     line_id=first_trip_b['line_id'],
                     name=new_name, name_short=new_name_short,
                     arrival_station_id=first_trip_b['departure_station'].id,
                     departure_station_id=departure_station.id, distance=calculate_distance())

    session_route.add(aszfahrt)
    session_route.add(eszfahrt)
    session_route.commit()

    print(f'\tRoutes created: {new_route_id}, {new_route_id+1}')

    ### generate trips ###
    with Session(engine) as session_trip:
        ### aussetzfahrt
        departure_time = last_trip_a['arrival_time']
        arrival_time = departure_time + calculate_trip_duration(1, 2)

        aszfahrt = Trip(id=new_trip_id, departure_time=departure_time, arrival_time=arrival_time,
                        rotation_id=new_rotation_id, scenario_id=last_trip_a['scenario_id'], route_id=new_route_id,
                        trip_type='EMPTY')

        ### einsetzfahrt
        departure_time = first_trip_b['departure_time'] - calculate_trip_duration(1, 2)
        arrival_time = first_trip_b['departure_time']

        eszfahrt = Trip(id=new_trip_id + 1, departure_time=departure_time, arrival_time=arrival_time,
                        rotation_id=new_rotation_id + 1, scenario_id=last_trip_a['scenario_id'],
                        route_id=new_route_id + 1,
                        trip_type='EMPTY')

        session_trip.add(aszfahrt)
        session_trip.add(eszfahrt)
        session_trip.commit()
        session_trip.close()
        print(f'\tTrips created: {new_trip_id}, {new_trip_id + 1}')

    ### generate stoptimes ###
    # TODO modify dwell duration (to interval)
    with Session(engine) as session_stoptimes:
        # aussetzfahrt
        aussetzfahrt_trip = session_stoptimes.query(Trip).where(Trip.id == new_trip_id).scalar()
        aussetzfahrt_route = session_stoptimes.query(Route).where(Route.id == new_route_id).scalar()
        departure_aussetzfahrt = StopTime(id=new_stoptime_id, arrival_time=aussetzfahrt_trip.departure_time,
                                          dwell_duration=timedelta(minutes=0), scenario_id=old_rotation.scenario_id,
                                          station_id=aussetzfahrt_route.departure_station_id, trip_id=new_trip_id)
        arrival_aussetzfahrt = StopTime(id=new_stoptime_id + 1, arrival_time=aussetzfahrt_trip.arrival_time,
                                        dwell_duration=timedelta(minutes=0), scenario_id=old_rotation.scenario_id,
                                        station_id=aussetzfahrt_route.arrival_station_id, trip_id=new_trip_id)
        session_stoptimes.add(departure_aussetzfahrt)
        session_stoptimes.add(arrival_aussetzfahrt)

        # einsetzfahrt
        einsetzfahrt_trip = session_stoptimes.query(Trip).where(Trip.id == new_trip_id + 1).scalar()
        einsetzfahrt_route = session_stoptimes.query(Route).where(Route.id == new_route_id + 1).scalar()

        departure_einsetzfahrt = StopTime(id=new_stoptime_id + 2, arrival_time=einsetzfahrt_trip.departure_time,
                                          dwell_duration=timedelta(minutes=0), scenario_id=old_rotation.scenario_id,
                                          station_id=aussetzfahrt_route.departure_station_id, trip_id=new_trip_id + 1)
        arrival_einsetzfahrt = StopTime(id=new_stoptime_id + 3, arrival_time=einsetzfahrt_trip.arrival_time,
                                        dwell_duration=timedelta(minutes=0), scenario_id=old_rotation.scenario_id,
                                        station_id=einsetzfahrt_route.arrival_station_id, trip_id=new_trip_id + 1)
        session_stoptimes.add(departure_einsetzfahrt)
        session_stoptimes.add(arrival_einsetzfahrt)
        session_stoptimes.commit()
        session_stoptimes.close()

        print(f'\tStopTimes created: {new_stoptime_id}, {new_stoptime_id + 1}, {new_stoptime_id + 2},'
              f' {new_stoptime_id + 3}')

    ### update old trips and delete old rotation ###
    with Session(engine) as session:
        for trip in rotation_a:
            session.execute(update(Trip).where(trip['trip_id'] == Trip.id).values(rotation_id=new_rotation_id))
        for trip in rotation_b:
            session.execute(update(Trip).where(Trip.id == trip['trip_id']).values(rotation_id=new_rotation_id+1))
        session.execute(delete(Rotation).where(Rotation.id == old_rotation.id))
        print(f'\tRotation removed: {old_rotation.id}')
        session.commit()
        session.close()
