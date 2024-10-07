import argparse

from ds_wrapper import DjangoSimbaWrapper
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--database_url",
        type=str,
        required=True,
    )
    parser.add_argument(
        "--scenario_id",
        type=int,
        required=True,
    )
    args = parser.parse_args()

    engine = create_engine(args.database_url)
    with Session(engine) as session:
        session.commit()
        ds_wrapper = DjangoSimbaWrapper(args.database_url)
        ds_wrapper.single_step_electrification(args.scenario_id)
        session.commit()
        session.expire_all()

