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


def _parse_env_list(raw_value: str) -> list[str]:
    values: list[str] = []
    for item in raw_value.replace("\n", ",").split(","):
        value = item.strip()
        if value and value not in values:
            values.append(value)
    return values


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
    ORCHESTRATOR_STARTUP_DELAY_SECONDS: int = Field(default=5, ge=0)
    ORCHESTRATOR_EXIT_ON_FAILURE: bool = False
    PROBE_BATCH_SIZE: int = Field(default=200, ge=1)
    CHECK_FRESHNESS_MINUTES: int = Field(default=60, ge=1)
    MAX_SELECTION_AGE_MINUTES: int = Field(default=180, ge=1)
    EXPORT_BLACK_LIMIT: int = Field(default=5000, ge=1)
    EXPORT_WHITE_CIDR_LIMIT: int = Field(default=2000, ge=1)
    EXPORT_WHITE_SNI_LIMIT: int = Field(default=2000, ge=1)
    EXPORT_ALL_LIMIT: int = Field(default=10000, ge=1)
    EXPORT_MAX_PER_COUNTRY: int = Field(default=2, ge=1)
    EXPORT_MAX_PER_HOST: int = Field(default=1, ge=1)
    EXPORT_MAX_LATENCY_MS: int = Field(default=3000, ge=1)
    EXPORT_MIN_DOWNLOAD_MBPS: float = Field(default=2.0, ge=0)
    EXPORT_REQUIRE_SPEED_MEASUREMENT: bool = True
    EXPORT_MIN_FRESHNESS_SCORE: float = Field(default=0.75, ge=0, le=1)

    GEO_PROVIDER_PRIMARY: str = "ip-api"
    GEO_PROVIDER_FALLBACK: str = "ipwhois"
    GEO_REQUEST_TIMEOUT_SECONDS: int = Field(default=6, ge=1)
    GEO_IP_API_BASE_URL: str = "http://ip-api.com/json"
    GEO_IPWHOIS_BASE_URL: str = "https://ipwho.is"
    SPEED_TEST_URLS: str = (
        "http://cachefly.cachefly.net/1mb.test,"
        "https://speed.cloudflare.com/__down?bytes=1048576,"
        "https://proof.ovh.net/files/1Mb.dat"
    )
    SPEED_TEST_URL: str = "http://cachefly.cachefly.net/1mb.test"
    SPEED_TEST_ATTEMPTS: int = Field(default=3, ge=1, le=10)
    SPEED_TEST_CONNECT_TIMEOUT_SECONDS: int = Field(default=6, ge=1)
    SPEED_TEST_READ_TIMEOUT_SECONDS: int = Field(default=12, ge=1)
    SPEED_TEST_MAX_BYTES: int = Field(default=1_048_576, ge=1)
    SPEED_TEST_CHUNK_SIZE: int = Field(default=65_536, ge=1)
    CONNECT_TIMEOUT_SECONDS: int = Field(default=10, ge=1)
    DOWNLOAD_TIMEOUT_SECONDS: int = Field(default=20, ge=1)
    SINGBOX_BINARY: str = "sing-box"
    PROBER_LOCAL_BIND_HOST: str = "127.0.0.1"
    PROBER_BASE_LOCAL_PORT: int = Field(default=39000, ge=0, le=65535)
    PROBER_PROCESS_START_TIMEOUT_SECONDS: int = Field(default=8, ge=1)
    PROBER_EXIT_IP_URL: str = "https://api.ipify.org?format=json"
    PROBER_TEMP_DIR: str | None = None
    SCORER_RECENT_CHECKS_LIMIT: int = Field(default=20, ge=1)
    SCORER_MIN_ACTIVE_STABILITY: float = Field(default=0.75, ge=0, le=1)
    SCORER_MIN_DEGRADED_STABILITY: float = Field(default=0.25, ge=0, le=1)
    SCORER_LATENCY_GOOD_MS: int = Field(default=250, ge=1)
    SCORER_LATENCY_BAD_MS: int = Field(default=1800, ge=1)
    SCORER_SPEED_GOOD_MBPS: float = Field(default=30.0, ge=0)
    SCORER_SPEED_BAD_MBPS: float = Field(default=1.5, ge=0)
    SCORER_MIN_ACTIVE_FRESHNESS: float = Field(default=0.60, ge=0, le=1)
    SCORER_DEAD_FRESHNESS_MAX: float = Field(default=0.05, ge=0, le=1)
    SCORER_FRESHNESS_PENALTY_WEIGHT: float = Field(default=0.35, ge=0)
    SCORER_MISSING_SPEED_PENALTY: float = Field(default=0.08, ge=0)

    PUBLISH_ENABLED: bool = False
    PUBLISH_REMOTE: str = "origin"
    PUBLISH_BRANCH: str = "main"
    PUBLISH_GIT_AUTHOR_NAME: str = "proxy-mvp-bot"
    PUBLISH_GIT_AUTHOR_EMAIL: str = "proxy-mvp-bot@users.noreply.github.com"
    PUBLISH_COMMIT_MESSAGE_PREFIX: str = "chore(exports): refresh etalon outputs"
    PUBLISH_AUTH_MODE: str = "auto"
    PUBLISH_PUSH_TIMEOUT_SECONDS: int = Field(default=60, ge=1)

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

    @property
    def speed_test_urls(self) -> tuple[str, ...]:
        """Return deterministic speed endpoint list with legacy URL as fallback."""
        urls = _parse_env_list(self.SPEED_TEST_URLS)
        legacy_url = self.SPEED_TEST_URL.strip()
        if legacy_url and legacy_url not in urls:
            urls.append(legacy_url)
        return tuple(urls)

    @property
    def speed_test_timeout(self) -> tuple[int, int]:
        """Return requests timeout tuple for speed measurements."""
        return (
            self.SPEED_TEST_CONNECT_TIMEOUT_SECONDS,
            self.SPEED_TEST_READ_TIMEOUT_SECONDS,
        )

    def safe_summary(self) -> dict[str, str | int | float | bool]:
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
            "ORCHESTRATOR_STARTUP_DELAY_SECONDS": self.ORCHESTRATOR_STARTUP_DELAY_SECONDS,
            "ORCHESTRATOR_EXIT_ON_FAILURE": self.ORCHESTRATOR_EXIT_ON_FAILURE,
            "PROBE_BATCH_SIZE": self.PROBE_BATCH_SIZE,
            "CHECK_FRESHNESS_MINUTES": self.CHECK_FRESHNESS_MINUTES,
            "MAX_SELECTION_AGE_MINUTES": self.MAX_SELECTION_AGE_MINUTES,
            "EXPORT_BLACK_LIMIT": self.EXPORT_BLACK_LIMIT,
            "EXPORT_WHITE_CIDR_LIMIT": self.EXPORT_WHITE_CIDR_LIMIT,
            "EXPORT_WHITE_SNI_LIMIT": self.EXPORT_WHITE_SNI_LIMIT,
            "EXPORT_ALL_LIMIT": self.EXPORT_ALL_LIMIT,
            "EXPORT_MAX_PER_COUNTRY": self.EXPORT_MAX_PER_COUNTRY,
            "EXPORT_MAX_PER_HOST": self.EXPORT_MAX_PER_HOST,
            "EXPORT_MAX_LATENCY_MS": self.EXPORT_MAX_LATENCY_MS,
            "EXPORT_MIN_DOWNLOAD_MBPS": self.EXPORT_MIN_DOWNLOAD_MBPS,
            "EXPORT_REQUIRE_SPEED_MEASUREMENT": self.EXPORT_REQUIRE_SPEED_MEASUREMENT,
            "EXPORT_MIN_FRESHNESS_SCORE": self.EXPORT_MIN_FRESHNESS_SCORE,
            "GEO_PROVIDER_PRIMARY": self.GEO_PROVIDER_PRIMARY,
            "GEO_PROVIDER_FALLBACK": self.GEO_PROVIDER_FALLBACK,
            "GEO_REQUEST_TIMEOUT_SECONDS": self.GEO_REQUEST_TIMEOUT_SECONDS,
            "GEO_IP_API_BASE_URL": self.GEO_IP_API_BASE_URL,
            "GEO_IPWHOIS_BASE_URL": self.GEO_IPWHOIS_BASE_URL,
            "SPEED_TEST_URLS": ",".join(self.speed_test_urls),
            "SPEED_TEST_URL": self.SPEED_TEST_URL,
            "SPEED_TEST_ATTEMPTS": self.SPEED_TEST_ATTEMPTS,
            "SPEED_TEST_CONNECT_TIMEOUT_SECONDS": self.SPEED_TEST_CONNECT_TIMEOUT_SECONDS,
            "SPEED_TEST_READ_TIMEOUT_SECONDS": self.SPEED_TEST_READ_TIMEOUT_SECONDS,
            "SPEED_TEST_MAX_BYTES": self.SPEED_TEST_MAX_BYTES,
            "SPEED_TEST_CHUNK_SIZE": self.SPEED_TEST_CHUNK_SIZE,
            "CONNECT_TIMEOUT_SECONDS": self.CONNECT_TIMEOUT_SECONDS,
            "DOWNLOAD_TIMEOUT_SECONDS": self.DOWNLOAD_TIMEOUT_SECONDS,
            "SINGBOX_BINARY": self.SINGBOX_BINARY,
            "PROBER_LOCAL_BIND_HOST": self.PROBER_LOCAL_BIND_HOST,
            "PROBER_BASE_LOCAL_PORT": self.PROBER_BASE_LOCAL_PORT,
            "PROBER_PROCESS_START_TIMEOUT_SECONDS": self.PROBER_PROCESS_START_TIMEOUT_SECONDS,
            "PROBER_EXIT_IP_URL": self.PROBER_EXIT_IP_URL,
            "PROBER_TEMP_DIR": self.PROBER_TEMP_DIR or "",
            "SCORER_RECENT_CHECKS_LIMIT": self.SCORER_RECENT_CHECKS_LIMIT,
            "SCORER_MIN_ACTIVE_STABILITY": self.SCORER_MIN_ACTIVE_STABILITY,
            "SCORER_MIN_DEGRADED_STABILITY": self.SCORER_MIN_DEGRADED_STABILITY,
            "SCORER_LATENCY_GOOD_MS": self.SCORER_LATENCY_GOOD_MS,
            "SCORER_LATENCY_BAD_MS": self.SCORER_LATENCY_BAD_MS,
            "SCORER_SPEED_GOOD_MBPS": self.SCORER_SPEED_GOOD_MBPS,
            "SCORER_SPEED_BAD_MBPS": self.SCORER_SPEED_BAD_MBPS,
            "SCORER_MIN_ACTIVE_FRESHNESS": self.SCORER_MIN_ACTIVE_FRESHNESS,
            "SCORER_DEAD_FRESHNESS_MAX": self.SCORER_DEAD_FRESHNESS_MAX,
            "SCORER_FRESHNESS_PENALTY_WEIGHT": self.SCORER_FRESHNESS_PENALTY_WEIGHT,
            "SCORER_MISSING_SPEED_PENALTY": self.SCORER_MISSING_SPEED_PENALTY,
            "PUBLISH_ENABLED": self.PUBLISH_ENABLED,
            "PUBLISH_REMOTE": self.PUBLISH_REMOTE,
            "PUBLISH_BRANCH": self.PUBLISH_BRANCH,
            "PUBLISH_GIT_AUTHOR_NAME": self.PUBLISH_GIT_AUTHOR_NAME,
            "PUBLISH_GIT_AUTHOR_EMAIL": self.PUBLISH_GIT_AUTHOR_EMAIL,
            "PUBLISH_COMMIT_MESSAGE_PREFIX": self.PUBLISH_COMMIT_MESSAGE_PREFIX,
            "PUBLISH_AUTH_MODE": self.PUBLISH_AUTH_MODE,
            "PUBLISH_PUSH_TIMEOUT_SECONDS": self.PUBLISH_PUSH_TIMEOUT_SECONDS,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Build and cache settings instance."""
    return Settings()


settings = get_settings()
