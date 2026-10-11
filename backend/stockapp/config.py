from functools import lru_cache
from pathlib import Path
import os

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(os.environ.get("STOCK_PROJECT_ROOT", Path(__file__).resolve().parents[2])).resolve()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="STOCK_", env_file=ROOT / ".env", extra="ignore")
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "stock_alei"
    db_user: str = "stock_alei_app"
    db_password: SecretStr
    api_port: int = 8000
    request_budget: int = 45000
    provider_timeout: int = 45


@lru_cache
def settings() -> Settings:
    return Settings()

