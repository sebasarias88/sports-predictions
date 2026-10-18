from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ENGINE_ROOT = Path(__file__).resolve().parents[2]  # .../prediction-engine
REPO_ROOT = ENGINE_ROOT.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", ENGINE_ROOT / ".env"),
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://predictions:predictions@localhost:5433/predictions"
    api_football_key: str = ""
    football_data_org_key: str = ""
    odds_api_key: str = ""

    data_dir: Path = ENGINE_ROOT / "data"
    sql_dir: Path = ENGINE_ROOT / "sql"


@lru_cache
def get_settings() -> Settings:
    return Settings()
