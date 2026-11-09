"""The one Settings class that reads .env, per docs/engineering-standards.md section 2."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Machine-specific values and secrets. Field names map to upper-case .env keys."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ee_project: str
    data_raw_dir: Path = Path("data/raw")
    data_processed_dir: Path = Path("data/processed")
