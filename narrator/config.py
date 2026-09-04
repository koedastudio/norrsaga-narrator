"""Settings from the environment or a `.env` file. Missing required values fail at startup."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    port: int = 13379
    abs_url: str
    abs_token: str
    kokoro_url: str
    kokoro_voice: str = "af_heart"
    cache_dir: Path = Path("./cache")
    log_level: str = "info"
