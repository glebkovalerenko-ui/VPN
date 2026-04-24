"""Shared application settings loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    """Centralized configuration for all services."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    PROJECT_NAME: str = "proxy-mvp"
    TZ: str = "Europe/Moscow"

    POSTGRES_DB: str = "proxy_mvp"
    POSTGRES_USER: str = "proxy_user"
    POSTGRES_PASSWORD: SecretStr = SecretStr("change_me")
    POSTGRES_HOST: str = "127.0.0.1"
    POSTGRES_PORT: int = Field(default=5432, ge=1, le=65535)
    DATABASE_URL: str | None = None

    FETCH_INTERVAL_MINUTES: int = Field(default=15, ge=1)
    PROBE_BATCH_SIZE: int = Field(default=200, ge=1)
    CHECK_FRESHNESS_MINUTES: int = Field(default=60, ge=1)
    MAX_SELECTION_AGE_MINUTES: int = Field(default=180, ge=1)
    EXPORT_BLACK_LIMIT: int = Field(default=5000, ge=1)
    EXPORT_WHITE_CIDR_LIMIT: int = Field(default=2000, ge=1)
    EXPORT_WHITE_SNI_LIMIT: int = Field(default=2000, ge=1)
    EXPORT_ALL_LIMIT: int = Field(default=10000, ge=1)
    MAX_PER_COUNTRY: int = Field(default=500, ge=1)
    MAX_PER_HOST: int = Field(default=100, ge=1)

    GEO_PROVIDER_PRIMARY: str = "ip-api"
    GEO_PROVIDER_FALLBACK: str = "ipwhois"
    SPEED_TEST_URL: str = "https://speed.hetzner.de/10MB.bin"
    CONNECT_TIMEOUT_SECONDS: int = Field(default=10, ge=1)
    DOWNLOAD_TIMEOUT_SECONDS: int = Field(default=20, ge=1)

    @property
    def database_url(self) -> str:
        """Return effective SQLAlchemy DSN."""
        if self.DATABASE_URL:
            return self.DATABASE_URL

        password = self.POSTGRES_PASSWORD.get_secret_value()
        return (
            f"postgresql+psycopg://{quote_plus(self.POSTGRES_USER)}:{quote_plus(password)}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def masked_database_url(self) -> str:
        """Return DSN with hidden password for safe logs/CLI output."""
        url = make_url(self.database_url)
        if url.password is None:
            return str(url)
        return str(url.set(password="***"))

    def safe_summary(self) -> dict[str, str | int | bool]:
        """Return non-sensitive settings summary."""
        return {
            "PROJECT_NAME": self.PROJECT_NAME,
            "TZ": self.TZ,
            "POSTGRES_DB": self.POSTGRES_DB,
            "POSTGRES_USER": self.POSTGRES_USER,
            "POSTGRES_PASSWORD": "***",
            "POSTGRES_HOST": self.POSTGRES_HOST,
            "POSTGRES_PORT": self.POSTGRES_PORT,
            "DATABASE_URL": self.masked_database_url,
            "DATABASE_URL_EXPLICIT": bool(self.DATABASE_URL),
            "FETCH_INTERVAL_MINUTES": self.FETCH_INTERVAL_MINUTES,
            "PROBE_BATCH_SIZE": self.PROBE_BATCH_SIZE,
            "CHECK_FRESHNESS_MINUTES": self.CHECK_FRESHNESS_MINUTES,
            "MAX_SELECTION_AGE_MINUTES": self.MAX_SELECTION_AGE_MINUTES,
            "EXPORT_BLACK_LIMIT": self.EXPORT_BLACK_LIMIT,
            "EXPORT_WHITE_CIDR_LIMIT": self.EXPORT_WHITE_CIDR_LIMIT,
            "EXPORT_WHITE_SNI_LIMIT": self.EXPORT_WHITE_SNI_LIMIT,
            "EXPORT_ALL_LIMIT": self.EXPORT_ALL_LIMIT,
            "MAX_PER_COUNTRY": self.MAX_PER_COUNTRY,
            "MAX_PER_HOST": self.MAX_PER_HOST,
            "GEO_PROVIDER_PRIMARY": self.GEO_PROVIDER_PRIMARY,
            "GEO_PROVIDER_FALLBACK": self.GEO_PROVIDER_FALLBACK,
            "SPEED_TEST_URL": self.SPEED_TEST_URL,
            "CONNECT_TIMEOUT_SECONDS": self.CONNECT_TIMEOUT_SECONDS,
            "DOWNLOAD_TIMEOUT_SECONDS": self.DOWNLOAD_TIMEOUT_SECONDS,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Build and cache settings instance."""
    return Settings()


settings = get_settings()
