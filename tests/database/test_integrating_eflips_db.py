import pytest
import os

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from eflips.model import Base
from eflips.model.general import (
    Scenario,
    VehicleType,
    BatteryType,
    VehicleClass,
    Vehicle,
)

from eflips.depot.api.eflips_db.input import VehicleType as EflipsVehicleType


class TestGeneral:
    @pytest.fixture()
    def scenario(self, session):
        """
        Creates a scenario
        :param session: An SQLAlchemy Session with the eflips-db schema
        :return: A :class:`Scenario` object
        """
        scenario = Scenario(name="Test Scenario")
        session.add(scenario)
        session.commit()
        return scenario

    @pytest.fixture(autouse=True)
    def sample_content(self, session):
        """
        Creates a scenario that comes filled with sample content for each type
        :param session: An SQLAlchemy Session with the eflips-db schema
        :return: A :class:`Scenario` object
        """

        # Add a scenario
        scenario = Scenario(name="Test Scenario")
        session.add(scenario)

        # Add a vehicle type with a battery type
        vehicle_type = VehicleType(
            scenario=scenario,
            name="Test Vehicle Type",
            battery_capacity=100,
            charging_curve=[[0, 1], [150, 150]],
            opportunity_charge_capable=True,
        )
        session.add(vehicle_type)
        battery_type = BatteryType(
            scenario=scenario, specific_mass=100, chemistry={"test": "test"}
        )
        session.add(battery_type)
        vehicle_type.battery_type = battery_type

        # Add a vehicle type without a battery type
        vehicle_type = VehicleType(
            scenario=scenario,
            name="Test Vehicle Type 2",
            battery_capacity=100,
            charging_curve=[[0, 1], [150, 150]],
            opportunity_charge_capable=True,
        )
        session.add(vehicle_type)

        # Add a VehicleClass
        vehicle_class = VehicleClass(
            scenario=scenario,
            name="Test Vehicle Class",
            vehicle_types=[vehicle_type],
        )
        session.add(vehicle_class)

        # Add a vehicle
        vehicle = Vehicle(
            scenario=scenario,
            vehicle_type=vehicle_type,
            name="Test Vehicle",
            name_short="TV",
        )
        session.add(vehicle)

        session.commit()
        return scenario

    @pytest.fixture()
    def session(self):
        """
        Creates a session with the eflips-db schema
        NOTE: THIS DELETE ALL DATA IN THE DATABASE
        :return: an SQLAlchemy Session with the eflips-db schema
        """
        url = os.environ["DATABASE_URL"]
        engine = create_engine(
            url, echo=False
        )  # Change echo to True to see SQL queries
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        session = Session(bind=engine)
        yield session
        session.close()


class TestQueryEntities(TestGeneral):
    @pytest.fixture()
    def query_vehicle_type(self, session):
        list_of_vehicle_types = []
        stmt = select(VehicleType)
        for vehicle_type in session.execute(stmt).scalars():
            eflips_vehicle_type = EflipsVehicleType.from_eflips_db(vehicle_type)
            list_of_vehicle_types.append(eflips_vehicle_type)

        return list_of_vehicle_types

    def test_vehicle_type_validity(self, query_vehicle_type):
        for vehicle_type in query_vehicle_type:
            assert isinstance(vehicle_type, EflipsVehicleType)
            assert isinstance(vehicle_type.charging_curve(0.5), float)
            print(vehicle_type.charging_curve(0.5))
