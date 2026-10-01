"""Create PostGIS extension and application tables.

Run after the database role exists and before serving the API:
    python -m app.manage initdb
"""
from __future__ import annotations

import sys

from sqlalchemy import text

from .database import engine, init_db


def init_postgis_and_tables() -> None:
    init_db()
    if engine.dialect.name != "postgresql":
        print(f"Skipping PostGIS extension for dialect {engine.dialect.name}")
        return
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_points_geom_gist ON points USING GIST (geom_geometry)"))
    print("PostGIS extension and spatial index are ready")


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "initdb"
    if command == "initdb":
        init_postgis_and_tables()
    else:
        raise SystemExit(f"unknown command: {command}")
