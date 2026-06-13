from typing import cast

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # App Settings
    app_name: str = Field("Croar", validation_alias="APP_NAME")
    debug: bool = False
    app_env: str = "development"
    default_logo_url: str = Field("https://croar-app.com/logo.png", validation_alias="DEFAULT_LOGO_URL")

    # Database Settings
    db_name: str = Field(..., validation_alias="DB_NAME")
    enterprise_db_name: str = Field("db_enterprise_shared", validation_alias="ENTERPRISE_DB_NAME")
    db_user: str = Field(..., validation_alias="DB_USER")
    db_password: str = Field(..., validation_alias="DB_PASSWORD")
    db_host: str = Field("localhost", validation_alias="DB_HOST")
    db_port: int = Field(5432, validation_alias="DB_PORT")

    # Security Settings
    secret_key: str = Field("your-super-secret-key-for-development", validation_alias="SECRET_KEY")
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440  # 24 hours

    # Redis Settings (Optional but recommended for Cache/Rate Limiting)
    redis_host: str = Field("localhost", validation_alias="REDIS_HOST")
    redis_port: int = Field(6379, validation_alias="REDIS_PORT")
    redis_password: str | None = Field(None, validation_alias="REDIS_PASSWORD")

    @property
    def celery_broker_url(self) -> str:
        if self.redis_password:
            return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/0"
        return f"redis://{self.redis_host}:{self.redis_port}/0"

    # External APIs
    openai_api_key: str | None = Field(None, validation_alias="OPENAI_API_KEY")
    openai_model: str = Field("gpt-4o-mini", validation_alias="OPENAI_MODEL")

    # Google SSO
    google_client_id: str | None = Field(None, validation_alias="GOOGLE_CLIENT_ID")
    google_client_secret: str | None = Field(None, validation_alias="GOOGLE_CLIENT_SECRET")

    # Google Indexing API (for Google Jobs)
    google_service_account_json: str | None = Field(None, validation_alias="GOOGLE_SERVICE_ACCOUNT_JSON")

    # Microsoft SSO
    ms_client_id: str | None = Field(None, validation_alias="MS_CLIENT_ID")
    ms_client_secret: str | None = Field(None, validation_alias="MS_CLIENT_SECRET")
    ms_tenant_id: str = Field("common", validation_alias="MS_TENANT_ID")

    # Product Hunt API
    producthunt_developer_token: str | None = Field(None, validation_alias="PRODUCTHUNT_DEVELOPER_TOKEN")

    # TwitterAPI.io (third-party Twitter/X data API)
    twitterapi_io_key: str | None = Field(None, validation_alias="TWITTERAPI_IO_KEY")

    # Mail Configuration
    mailer_sender_email: str | None = Field(None, validation_alias="MAILER_SENDER_EMAIL")
    smtp_address: str = Field("smtp.gmail.com", validation_alias="SMTP_ADDRESS")
    smtp_port: int = Field(587, validation_alias="SMTP_PORT")
    smtp_username: str | None = Field(None, validation_alias="SMTP_USERNAME")
    smtp_password: str | None = Field(None, validation_alias="SMTP_PASSWORD")

    # IMAP Configuration
    imap_address: str = Field("imap.gmail.com", validation_alias="IMAP_ADDRESS")
    imap_port: int = Field(993, validation_alias="IMAP_PORT")
    imap_username: str | None = Field(None, validation_alias="IMAP_USERNAME")
    imap_password: str | None = Field(None, validation_alias="IMAP_PASSWORD")

    # Frontend URL
    frontend_url: str = Field("http://localhost:3000", validation_alias="FRONTEND_URL")

    # CORS Settings
    cors_origins: str = Field(
        "http://localhost:3000,http://100.31.6.242,http://100.31.6.242:3000,http://3.94.202.48,http://3.94.202.48:3000,https://app.croar.co,https://api.croar.co",
        validation_alias="CORS_ORIGINS",
    )

    @property
    def parsed_cors_origins(self) -> list[str]:
        v = self.cors_origins
        if v == "*":
            return ["*"]
        if v.startswith("["):
            import json

            try:
                return cast("list[str]", json.loads(v))
            except Exception:
                pass
        return [i.strip() for i in v.split(",") if i.strip()]

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


_INSECURE_DEFAULT_SECRET = "your-super-secret-key-for-development"  # nosec B105  # gated: rejected in non-debug

settings = Settings()

# Refuse to run in production with the public default JWT signing key (anyone who
# knows it can forge tokens for any user). In dev we only warn.
if settings.secret_key == _INSECURE_DEFAULT_SECRET:
    if settings.app_env.lower() in ("production", "prod") or not settings.debug:
        raise RuntimeError(
            "SECRET_KEY is unset/insecure. Set a strong random SECRET_KEY env var before "
            "running outside local development."
        )
    print("WARNING: using the insecure default SECRET_KEY — set SECRET_KEY for any non-local use.")


def get_settings() -> Settings:
    return settings
