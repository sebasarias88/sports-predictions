from functools import lru_cache

from sqlalchemy import Engine, create_engine, text

from prediction_engine.config import get_settings
from prediction_engine.leagues import LEAGUES


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True)


def init_db() -> None:
    """Create tables (idempotent) and sync the league catalog."""
    schema_sql = (get_settings().sql_dir / "schema.sql").read_text()
    with get_engine().begin() as conn:
        conn.exec_driver_sql(schema_sql)
        conn.execute(
            text(
                """
                INSERT INTO leagues (code, name, country, tier, source)
                VALUES (:code, :name, :country, :tier, :source)
                ON CONFLICT (code) DO UPDATE SET
                    name = EXCLUDED.name,
                    country = EXCLUDED.country,
                    tier = EXCLUDED.tier,
                    source = EXCLUDED.source
                """
            ),
            [
                {
                    "code": lg.code,
                    "name": lg.name,
                    "country": lg.country,
                    "tier": lg.tier,
                    "source": lg.source,
                }
                for lg in LEAGUES
            ],
        )


def check_connection() -> str:
    with get_engine().connect() as conn:
        return conn.execute(text("SELECT version()")).scalar_one()
