import os

from dash import Dash, html, dcc, callback, Input, Output
import plotly.express as px
from dash.exceptions import PreventUpdate
from eflips.model import Event, EventType, Scenario, Vehicle

import sqlalchemy
from sqlalchemy.orm import Session


# TODO possibly existing a better way to "share" sessions between callbacks


@callback(
    Output("gantt-chart", "figure"),
    Input("color-scheme-dropdown", "value"),
    Input("scenario-id-dropdown", "value"),
)
def get_ganttchart_scenario(color_scheme: str, scenario_id: int):
    """This function takes a value from dropdown as scenario id and returns a :class:`plotly.express.timeline` object
    representing the gantt chart of the scenario to be used in a html layout.
    :param scenario_id: The output from dropdown as scenario id
    :return: A :class:`plotly.express.timeline` object
    """

    # Pass dropdown value to the scenario_id
    # scenario_id = value

    # Create a connection to the database

    engine = sqlalchemy.create_engine(os.environ.get("DATABASE_URL"))
    with Session(engine) as session:
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
        EventType.DRIVING: "skyblue",
        EventType.SERVICE: "salmon",
        EventType.STANDBY_DEPARTURE: "orange",
    }

    if color_scheme == "Event type":
        color = "event_type"
        color_discrete_map = {
            EventType.CHARGING_DEPOT: "forestgreen",
            EventType.DRIVING: "skyblue",
            EventType.SERVICE: "salmon",
            EventType.STANDBY_DEPARTURE: "orange",
        }
        color_continuous_scale = None
    else:
        color = "soc_end"
        color_discrete_map = None
        color_continuous_scale = px.colors.sequential.Viridis

    fig = px.timeline(
        event_dict,
        x_start="time_start",
        x_end="time_end",
        y="vehicle_id",
        color=color,
        color_discrete_map=color_discrete_map,
        color_continuous_scale=color_continuous_scale,
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
def get_scenario_name(scenario_id: int):
    engine = sqlalchemy.create_engine(os.environ.get("DATABASE_URL"))
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
    if clickData is None:
        raise PreventUpdate
    vehicle_id = clickData["points"][0]["y"]
    return vehicle_id


@callback(
    Output("vehicle-soc-plot", "figure"),
    Input("click-data", "children"),
)
def get_vehicle_soc_plot(vehicle_id: int):
    if vehicle_id is None:
        raise PreventUpdate

    engine = sqlalchemy.create_engine(os.environ.get("DATABASE_URL"))

    with Session(engine) as session:
        all_events = (
            session.query(Event)
            .filter(Event.vehicle_id == vehicle_id)
            .order_by(Event.time_start)
            .all()
        )
        # Go through all events and connect the soc_start and soc_end and time_start and time_end
        all_times = []
        all_soc = []
        for event in all_events:
            all_times.append(event.time_start)
            all_times.append(event.time_end)
            all_soc.append(event.soc_start)
            all_soc.append(event.soc_end)

            fig = px.line(
                x=all_times,
                y=all_soc,
                width=2500,
                height=500,
                labels={"x": "Time", "y": "SOC"},
            )

    return fig


if __name__ == "__main__":
    app = Dash(__name__)
    app.layout = html.Div(
        children=[
            html.H1(children="eflips-depot says hi"),
            html.Div("Select a scenario by id:"),
            dcc.Dropdown(
                ["8", "7", "6"], "8", id="scenario-id-dropdown", style={"width": "30%"}
            ),
            html.Div("Select a color-scheme:"),
            dcc.Dropdown(
                ["Event type", "SOC"],
                "Event type",
                id="color-scheme-dropdown",
                style={"width": "30%"},
            ),
            html.Div(id="scenario-name"),
            html.Div(id="num-vehicles"),
            dcc.Graph(id="gantt-chart"),
            html.Div(
                children=[
                    html.Div(children="Selected vehicle id"),
                    html.Pre(id="click-data"),
                    dcc.Graph(id="vehicle-soc-plot"),
                ]
            ),
        ]
    )

    app.run_server(debug=True)
