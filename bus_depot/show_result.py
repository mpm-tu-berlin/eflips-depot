import os

from typing import Dict
from dash import Dash, html, dcc, callback, Input, Output
import plotly.express as px
from eflips.model import Base, Event, EventType, Scenario, Vehicle

import sqlalchemy
from sqlalchemy.orm import Session


import plotly.graph_objects as go


@callback(
    Output("gantt-chart", "figure"),
    Input("scenario-id-dropdown", "value"),
)
def get_ganttchart_scenario(value: int):
    """This function takes a value from dropdown as scenario id and returns a :class:`plotly.express.timeline` object
    representing the gantt chart of the scenario to be used in a html layout.
    :param value: The output from dropdown as scenario id
    :return: A :class:`plotly.express.timeline` object
    """

    # Pass dropdown value to the scenario_id
    scenario_id = value

    # Create a connection to the database
    engine = sqlalchemy.create_engine(os.environ["DATABASE_URL"])
    with Session(engine) as session:
        scenario_name = (
            session.query(Scenario.name).filter(Scenario.id == scenario_id).first()
        )
        event_list = (
            session.query(Event.__table__)
            .filter(Event.scenario_id == scenario_id)
            .order_by(Event.vehicle_id)
            .all()
        )
        event_dict = []
        for row in event_list:
            d = dict(row._mapping)
            # Convert the datetime objects to strings in order to avoid empty rows in gantt chart.TODO find a better
            #  solution
            d["vehicle_id"] = str(d["vehicle_id"])
            event_dict.append(d)

    num_vehicles = len(set([event["vehicle_id"] for event in event_dict]))

    color_map = {
        EventType.CHARGING_DEPOT: "forestgreen",
        EventType.DRIVING: "steelblue",
        EventType.SERVICE: "salmon",
        EventType.STANDBY_DEPARTURE: "tan",
    }
    fig = px.timeline(
        event_dict,
        x_start="time_start",
        x_end="time_end",
        y="vehicle_id",
        color="event_type",
        color_discrete_map=color_map,
        hover_data=[
            "time_start",
            "time_end",
            "soc_start",
            "soc_end",
            "vehicle_id",
            "area_id",
        ],
        width=2500,
        height=num_vehicles * 20,
    )
    fig.update_traces(width=0.5)
    return fig


@callback(
    Output("scenario-name", "children"),
    Output("num-vehicles", "children"),
    Input("scenario-id-dropdown", "value"),
)
def get_scenario_name(value: int):
    scenario_id = value
    engine = sqlalchemy.create_engine(os.environ["DATABASE_URL"])
    with Session(engine) as session:
        scenario_name = (
            session.query(Scenario.name).filter(Scenario.id == scenario_id).first()
        )
        vehicle_count = (
            session.query(Vehicle).filter(Vehicle.scenario_id == scenario_id).count()
        )

    return str(scenario_name), f"Total number of vehicles:{vehicle_count}"


@callback(
    Output("click-data", "children"),
    Input("gantt-chart", "clickData"),
)
def get_vehicle_by_click(clickData):
    return clickData


if __name__ == "__main__":
    app = Dash(__name__)
    app.layout = html.Div(
        children=[
            html.H1(children="eflips-depot says hi"),
            html.Div("Select a scenario by id:"),
            dcc.Dropdown(
                ["8", "7", "6"], "8", id="scenario-id-dropdown", style={"width": "30%"}
            ),
            html.Div(id="scenario-name"),
            html.Div(id="num-vehicles"),
            dcc.Graph(id="gantt-chart"),
            html.Div(
                id="click-data",
                children="Click on the gantt chart to see the data of the clicked event",
            ),
        ]
    )
    app.run_server(debug=True)
