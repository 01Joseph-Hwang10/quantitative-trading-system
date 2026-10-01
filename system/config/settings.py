"""Application settings loaded from environment variables (`.env`)."""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv(".env")

# Notebook strategy window start (strategy_1.ipynb WINDOW["start"]).
DEFAULT_FEED_START_DATE = "2022-10-01"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Broker settings
    broker: str = "mock"  # "mock" | "toss"
    toss_client_id: str | None = None
    toss_client_secret: str | None = None

    # Monitor settings
    authorized_users: str = ""  # comma-separated email addresses
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_redirect_uri: str = "http://localhost:8501/oauth2callback"

    # Runtime settings
    data_dir: Path = Path("./data")
    trader_enabled: bool = True  # initial value used to seed app_state
    decision_schedule_cron: str = "0 15 * * 1-5"  # 15:00 KST, weekdays
    feed_update_schedule_cron: str = "0 9 * * 1-5"  # 09:00 KST catch-up
    timezone: str = "Asia/Seoul"
    log_level: str = "INFO"

    @property
    def authorized_user_list(self) -> list[str]:
        return [email.strip() for email in self.authorized_users.split(",") if email.strip()]

    @property
    def metadata_db_path(self) -> Path:
        return self.data_dir / "metadata.db"

    @property
    def feed_db_path(self) -> Path:
        return self.data_dir / "feed.db"

    def ensure_data_dir(self) -> Path:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self.data_dir
