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

from eflips.depot.api.input import ApiVehicleType


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
        vehicle_type_1 = VehicleType(
            scenario=scenario,
            name="Test Vehicle Type",
            battery_capacity=100,
            charging_curve=[[0, 1], [150, 150]],
            opportunity_charging_capable=True,
        )
        # Create a 12 meter bus
        vehicle_type_12m = VehicleType(
            scenario=scenario,
            name="12",
            battery_capacity=300,
            charging_curve=[[0, 1], [150, 150]],
            opportunity_charging_capable=True,
        )
        session.add(vehicle_type_12m)
        battery_type = BatteryType(
            scenario=scenario, specific_mass_kg_per_kwh=15, chemistry={"test": "test"}
        )
        session.add(battery_type)
        vehicle_type_12m.battery_type = battery_type

        # Add a vehicle type without a battery type
        vehicle_type_18m = VehicleType(
            scenario=scenario,
            name="18",
            battery_capacity=120,
            charging_curve=[[0, 1], [150, 150]],
            opportunity_charging_capable=True,
        )

        session.add(vehicle_type_18m)

        vehicle_type_12m_terminus_charge = VehicleType(
            scenario=scenario,
            name="12_terminus_charge",
            battery_capacity=120,
            charging_curve=[[0, 1], [150, 150]],
            opportunity_charging_capable=True,
        )
        # Add a VehicleClass
        vehicle_class = VehicleClass(
            scenario=scenario,
            name="Test Vehicle Class",
            vehicle_types=[
                vehicle_type_12m,
                vehicle_type_18m,
                vehicle_type_12m_terminus_charge,
            ],
        )
        session.add(vehicle_class)

        # Add a vehicle
        vehicle = Vehicle(
            scenario=scenario,
            vehicle_type=vehicle_type_12m,
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


class TestQueryVehicleType(TestGeneral):
    @pytest.fixture()
    def db_vehicle_types(self, session):
        list_of_vehicle_types = []
        stmt = select(VehicleType)
        for db_vt in session.execute(stmt).scalars():
            eflips_vt = ApiVehicleType(db_vt)
            list_of_vehicle_types.append(eflips_vt)

        return list_of_vehicle_types

    def test_vehicle_type_validity(self, db_vehicle_types):
        for vehicle_type in db_vehicle_types:
            assert isinstance(vehicle_type, ApiVehicleType)
            assert isinstance(vehicle_type.current_charging_power(0.5), float)
            assert isinstance(vehicle_type.charging_curve(0.5), float)
