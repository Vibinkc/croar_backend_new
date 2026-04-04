
from pydantic import Field, field_validator
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
    cors_origins: list[str] = Field(["http://localhost:3000", "http://3.94.202.48", "http://3.94.202.48:3000"], validation_alias="CORS_ORIGINS")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def assemble_cors_origins(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str) and not v.startswith("["):
            return [i.strip() for i in v.split(",")]
        elif isinstance(v, str) and v.startswith("["):
            import json
            return json.loads(v)
        return v

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()


def get_settings():
    return settings
