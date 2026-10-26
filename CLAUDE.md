# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

SimPy bus-depot simulation for eFLIPS. Reads/writes the DB schema from the separate `eflips-model` package. Poetry, Python 3.11–3.13.

## Commands

Tests wipe the database in `DATABASE_URL` (Postgres+PostGIS or SQLite+SpatiaLite; CI runs both):

```bash
export PYTHONPATH=tests:. DATABASE_URL=sqlite:////tmp/eflips.db   # SQLite also needs SPATIALITE_LIBRARY_PATH
poetry run pytest tests/api/test_api.py::TestClass::test_name     # single test
```

## Rules

- Code must pass `black` (pinned 23.12.1). Enforced by hooks in `.claude/settings.json`.
- Every PR bumps the version per SemVer: patch = no API change, minor = backwards-compatible API change, major = breaking. Update `pyproject.toml` and `release` in `docs/conf.py` together (CI checks equality); keep `poetry.lock` consistent.
- Never edit or delete tests to make them pass.

## Architecture

- `eflips/depot/*.py`: legacy SimPy core (depot, processes, smart charging, evaluation). Very large files; read targeted ranges. `layout_opt/` is an old optimizer.
- `eflips/depot/api/__init__.py`: public API. Its module docstring defines the call order (consumption sim → depot layout → `simulate_scenario` or `init_simulation`/`run_simulation`/`add_evaluation_to_database` → re-run consumption sim → `shrink_to_peak_usage`). Most changes happen here and in `api/private/`.
- Flow: DB scenario → `init_simulation` builds a template dict + `SimulationHost` → SimPy run → `DepotEvaluation` → events written back to DB.

## Gotchas

- `generate_optimal_depot_layout` simulates many candidates: pass `rotation_ids` to `simulate_scenario`/`init_simulation` instead of deleting rows, and call `session.expire_all()` between candidates.
- `simple_consumption_simulation` has an N+1 query pattern; it is off the production path (driving events are pre-populated externally).
- `pperf-improvement/`, `bin/`, `gurobi_detailed.log` are local scratch, not the package.
