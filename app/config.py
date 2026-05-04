from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    APP_NAME: str = "InboxAI Brain"
    ENVIRONMENT: str = "development"
    PORT: int = 8000

    ANTHROPIC_API_KEY: str = ""
    OPENAI_API_KEY: str = ""

    SUPABASE_URL: str = ""
    SUPABASE_SERVICE_ROLE_KEY: str = ""
    SUPABASE_DB_PASSWORD: str = ""

    DATABASE_URL_LOCAL: str = "postgresql+asyncpg://brain:brain@localhost:5432/brain"
    DATABASE_URL_SUPABASE: str = ""

    REDIS_URL: str = "redis://localhost:6379/0"

    ALLOWED_ORIGINS: str = "http://localhost:3000"

    @property
    def allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",") if origin.strip()]

    @property
    def database_url(self) -> str:
        if self.ENVIRONMENT == "production":
            return self.DATABASE_URL_SUPABASE
        return self.DATABASE_URL_LOCAL


settings = Settings()
