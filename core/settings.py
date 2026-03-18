"""
Core settings and configuration management
"""
import json
import os
from typing import Any, Dict, List
from pydantic import Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, EnvSettingsSource, PydanticBaseSettingsSource
from dotenv import load_dotenv
from pathlib import Path
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


class ListFriendlyEnvSource(EnvSettingsSource):
    """Allow simple string values for list settings (e.g. `*`, `a,b,c`)."""

    LIST_FIELDS = {"ALLOWED_HOSTS", "CORS_ORIGINS", "API_KEYS"}

    def prepare_field_value(self, field_name, field, value, value_is_complex):
        if field_name in self.LIST_FIELDS and isinstance(value, str):
            raw = value.strip()
            if not raw:
                return []
            if raw.startswith("["):
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, list):
                        return parsed
                except Exception:
                    pass
            if "," in raw:
                return [item.strip() for item in raw.split(",") if item.strip()]
            return [raw]

        return super().prepare_field_value(field_name, field, value, value_is_complex)


class Settings(BaseSettings):
    """Application settings with environment variable support"""
    
    # API Configuration
    APP_NAME: str = "Dynamic Multi-Project API"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = True
    
    # Server Configuration
    HOST: str = "127.0.0.1"
    PORT: int = 8010
    RELOAD: bool = False
    
    # Security
    SECRET_KEY: str = "your-super-secret-key-here"
    ALLOWED_HOSTS: List[str] = ["*"]
    CORS_ORIGINS: List[str] = ["*"]
    CORS_MANAGED_BY_PROXY: bool = False
    
    # API Key Authentication (optional)
    API_KEY_ENABLED: bool = True
    API_KEYS: List[str] = [
        "tr_live_key_2025_a1b2c3d4e5f6",  # Production key
        "tr_test_key_2025_x9y8z7w6v5u4",  # Testing key
        "tr_admin_key_2025_p0o9i8u7y6t5"  # Admin key
    ]
    
    # Rate Limiting
    RATE_LIMIT_ENABLED: bool = False
    RATE_LIMIT_REQUESTS: int = 100
    RATE_LIMIT_WINDOW: int = 60
    
    # Database Configuration
    DATABASE_URL: str = ""
    DB_HOST: str = os.getenv("DB_HOST", "localhost")
    DB_USER: str = os.getenv("DB_USER", "")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "")
    DB_NAME: str = os.getenv("DB_NAME", "")
    DB_PORT: int = int(os.getenv("DB_PORT", 3306))
    DB_CENTRAL: str = os.getenv("DB_CENTRAL", "")
    CENTRAL_DATABASE_URL: str = os.getenv("CENTRAL_DATABASE_URL", "")
    DB_HOST: str = os.getenv("DB_HOST", os.getenv("DB_HOST", "localhost"))
    DB_USER: str = os.getenv("DB_USER", os.getenv("DB_USER", ""))
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", os.getenv("DB_PASSWORD", ""))
    CENTRAL_DB_NAME: str = os.getenv("CENTRAL_DB_NAME", os.getenv("DB_CENTRAL", ""))
    DB_PORT: int = int(os.getenv("DB_PORT", os.getenv("DB_PORT", 3306)))
    DATABASE_MAIN_URL: str = os.getenv("DATABASE_MAIN_URL", os.getenv("DATABASE_URL", ""))
    DATABASE_CENTRAL_URL: str = os.getenv(
        "DATABASE_CENTRAL_URL",
        os.getenv("CENTRAL_DATABASE_URL", ""),
    )

    # Authentication / Token Settings
    PASETO_SECRET_KEY: str = os.getenv("PASETO_SECRET_KEY", "change-this-paseto-secret-key")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 15))
    REFRESH_TOKEN_EXPIRE_DAYS: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", 7))
    RESET_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("RESET_TOKEN_EXPIRE_MINUTES", 30))

    # Google Calendar V1
    GOOGLE_CALENDAR_API_BASE_URL: str = os.getenv(
        "GOOGLE_CALENDAR_API_BASE_URL",
        "https://www.googleapis.com/calendar/v3",
    )
    GOOGLE_CALENDAR_TIMEOUT_SECONDS: int = int(
        os.getenv("GOOGLE_CALENDAR_TIMEOUT_SECONDS", 20)
    )
    GOOGLE_CALENDAR_COMPARE_TIMEZONE: str = os.getenv(
        "GOOGLE_CALENDAR_COMPARE_TIMEZONE",
        "Asia/Kolkata",
    )
    GOOGLE_CALENDAR_ID: str = os.getenv("GOOGLE_CALENDAR_ID", "")
    GOOGLE_DRIVE_TOKEN_ID: int = int(os.getenv("GOOGLE_DRIVE_TOKEN_ID", 2))
    GOOGLE_OAUTH_TOKEN_URL: str = os.getenv(
        "GOOGLE_OAUTH_TOKEN_URL",
        "https://oauth2.googleapis.com/token",
    )
    GOOGLE_TOKEN_REFRESH_SKEW_SECONDS: int = int(
        os.getenv("GOOGLE_TOKEN_REFRESH_SKEW_SECONDS", 120)
    )

    # Employee Events V1
    EMP_EVENT_APPROVED_STATUS: int = int(os.getenv("EMP_EVENT_APPROVED_STATUS", 1))
    EMP_EVENT_PARKED_VALUE: int = int(os.getenv("EMP_EVENT_PARKED_VALUE", 1))
    EMP_EVENT_TIMEZONE: str = os.getenv("EMP_EVENT_TIMEZONE", "Asia/Kolkata")
    EMP_EVENT_ENABLE_GOOGLE_SYNC: bool = os.getenv("EMP_EVENT_ENABLE_GOOGLE_SYNC", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    # Authentication v2
    AUTH_V2_ENABLED: bool = os.getenv("AUTH_V2_ENABLED", "False").lower() == "true"
    AUTH_V2_ISSUER: str = os.getenv("AUTH_V2_ISSUER", "dynamic-api-auth-v2")
    AUTH_V2_AUDIENCE: str = os.getenv("AUTH_V2_AUDIENCE", "dynamic-api-clients")
    AUTH_V2_ACCESS_TOKEN_MINUTES: int = int(os.getenv("AUTH_V2_ACCESS_TOKEN_MINUTES", 15))
    AUTH_V2_REFRESH_TOKEN_DAYS: int = int(os.getenv("AUTH_V2_REFRESH_TOKEN_DAYS", 7))
    AUTH_V2_TOKEN_VERSION: int = int(os.getenv("AUTH_V2_TOKEN_VERSION", 2))
    AUTH_V2_CURRENT_KID: str = os.getenv("AUTH_V2_CURRENT_KID", "v2-kid-1")
    AUTH_V2_SIGNING_KEYS_JSON: str = os.getenv("AUTH_V2_SIGNING_KEYS_JSON", "[]")
    AUTH_V2_REFRESH_HASH_PEPPER: str = os.getenv("AUTH_V2_REFRESH_HASH_PEPPER", "")
    AUTH_V2_TIMING_FLOOR_MS: int = int(os.getenv("AUTH_V2_TIMING_FLOOR_MS", 400))
    AUTH_V2_TIMING_JITTER_MIN_MS: int = int(os.getenv("AUTH_V2_TIMING_JITTER_MIN_MS", 25))
    AUTH_V2_TIMING_JITTER_MAX_MS: int = int(os.getenv("AUTH_V2_TIMING_JITTER_MAX_MS", 80))
    AUTH_V2_RATE_LIMIT_IP_10M: int = int(os.getenv("AUTH_V2_RATE_LIMIT_IP_10M", 120))
    AUTH_V2_RATE_LIMIT_IP_MOBILE_10M: int = int(os.getenv("AUTH_V2_RATE_LIMIT_IP_MOBILE_10M", 30))
    AUTH_V2_RATE_LIMIT_MOBILE_GLOBAL_10M: int = int(
        os.getenv("AUTH_V2_RATE_LIMIT_MOBILE_GLOBAL_10M", 40)
    )
    AUTH_V2_LOGIN_FAIL_THRESHOLD: int = int(os.getenv("AUTH_V2_LOGIN_FAIL_THRESHOLD", 5))
    AUTH_V2_LOGIN_FAIL_WINDOW_MINUTES: int = int(
        os.getenv("AUTH_V2_LOGIN_FAIL_WINDOW_MINUTES", 15)
    )
    AUTH_V2_LOGIN_COOLDOWN_MINUTES: int = int(os.getenv("AUTH_V2_LOGIN_COOLDOWN_MINUTES", 15))
    AUTH_V2_BOOTSTRAP_ONLY: bool = os.getenv("AUTH_V2_BOOTSTRAP_ONLY", "True").lower() == "true"
    AUTH_SUPREME_CREATE_ENABLED: bool = os.getenv("AUTH_SUPREME_CREATE_ENABLED", "False").lower() == "true"
    
    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = "logs/app.log"
    
    # Controller Security
    ALLOW_PRIVATE_METHODS: bool = False
    MAX_CONTROLLER_NAME_LENGTH: int = 50
    MAX_FUNCTION_NAME_LENGTH: int = 50

    # SQL Gateway
    SQL_GATEWAY_ALLOWLIST: Dict[str, Any] = Field(
        default_factory=dict,
        alias="SQL_GATEWAY_ALLOWLIST_JSON",
    )
    SQL_GATEWAY_ALLOWLIST_SOURCE: str = "auto"
    SQL_GATEWAY_ALLOWLIST_PATH: str = ""
    SQL_GATEWAY_DB_ENGINE_MAP: Dict[str, str] = Field(
        default_factory=lambda: {"STORE": "default", "CENTRAL": "central", "DEFAULT": "default"},
        alias="SQL_GATEWAY_DB_ENGINE_MAP_JSON",
    )
    SQL_GATEWAY_ENABLE_TOTAL_COUNT: bool = True
    SQL_GATEWAY_DEFAULT_LIMIT: int = 100
    SQL_GATEWAY_MAX_LIMIT: int = 1000
    SQL_GATEWAY_MAX_FILTERS: int = 25
    SQL_GATEWAY_MAX_IN_LIST: int = 200
    SQL_GATEWAY_MAX_COLUMNS: int = 50
    SQL_GATEWAY_MAX_ORDER_BY: int = 5
    SQL_GATEWAY_MAX_GROUP_BY: int = 10
    SQL_GATEWAY_MAX_BULK_INSERT_ROWS: int = 500
    SQL_GATEWAY_MAX_BODY_BYTES: int = 1048576
    SQL_GATEWAY_MAX_WRITE_ROWS_DEFAULT: int = 100
    SQL_GATEWAY_RATE_LIMIT_PER_MINUTE: int = 120
    SQL_GATEWAY_STATEMENT_TIMEOUT_MS: int = 15000
    SQL_GATEWAY_POLICY_CACHE_TTL_SECONDS: int = 60
    SQL_GATEWAY_SCHEMA_CACHE_TTL_SECONDS: int = 600
    SQLGW_ADMIN_REQUIRE_RBAC: bool = True
    
    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"  # Ignore extra fields from environment

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ):
        # `load_dotenv()` already loads `.env` into environment variables.
        # Use a custom env source to safely parse list-like env fields.
        return (
            init_settings,
            ListFriendlyEnvSource(settings_cls),
            file_secret_settings,
        )

    @field_validator("ALLOWED_HOSTS", "CORS_ORIGINS", "API_KEYS", mode="before")
    @classmethod
    def parse_list_like_env(cls, value):
        """
        Accept both JSON arrays and simple comma-separated strings in env vars.

        Examples:
        - "*"
        - "https://a.com,https://b.com"
        - ["*"] (already parsed)
        - '["*"]' (JSON string)
        """
        if value is None:
            return value

        if isinstance(value, list):
            return value

        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return []

            if raw.startswith("["):
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, list):
                        return parsed
                except Exception:
                    # Fall back to comma parsing below
                    pass

            if "," in raw:
                return [item.strip() for item in raw.split(",") if item.strip()]

            return [raw]

        return value

    @field_validator("SQL_GATEWAY_ALLOWLIST", "SQL_GATEWAY_DB_ENGINE_MAP", mode="before")
    @classmethod
    def parse_gateway_json_env(cls, value, info: ValidationInfo):
        """Parse JSON object env values safely for SQL gateway settings."""
        if isinstance(value, dict):
            return value

        default_db_map = {"STORE": "default", "CENTRAL": "central", "DEFAULT": "default"}

        if value is None:
            if info.field_name == "SQL_GATEWAY_DB_ENGINE_MAP":
                return default_db_map
            return {}

        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                if info.field_name == "SQL_GATEWAY_DB_ENGINE_MAP":
                    return default_db_map
                return {}
            try:
                parsed = json.loads(raw)
            except Exception:
                return {"__invalid__": "__invalid__"}
            if isinstance(parsed, dict):
                return parsed
            return {"__invalid__": "__invalid__"}

        return {"__invalid__": "__invalid__"}

    @field_validator("SQL_GATEWAY_ALLOWLIST_SOURCE", mode="before")
    @classmethod
    def parse_allowlist_source(cls, value):
        if value is None:
            return "auto"
        source = str(value).strip().lower()
        if source not in {"auto", "env", "file", "db"}:
            return "auto"
        return source


# Global settings instance
settings = Settings()


def get_settings() -> Settings:
    """Get application settings"""
    return settings
