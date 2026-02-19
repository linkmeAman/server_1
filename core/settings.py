"""
Core settings and configuration management
"""
import json
import os
from typing import List
from pydantic import field_validator
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

    # Authentication / Token Settings
    PASETO_SECRET_KEY: str = os.getenv("PASETO_SECRET_KEY", "change-this-paseto-secret-key")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 15))
    REFRESH_TOKEN_EXPIRE_DAYS: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", 7))
    RESET_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("RESET_TOKEN_EXPIRE_MINUTES", 30))
    
    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = "logs/app.log"
    
    # Controller Security
    ALLOW_PRIVATE_METHODS: bool = False
    MAX_CONTROLLER_NAME_LENGTH: int = 50
    MAX_FUNCTION_NAME_LENGTH: int = 50
    
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


# Global settings instance
settings = Settings()


def get_settings() -> Settings:
    """Get application settings"""
    return settings
