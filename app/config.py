import os

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

    @property
    def effective_database_url(self) -> str:
        """Priority: env DATABASE_URL > production Supabase > local Docker."""
        env_override = os.environ.get("DATABASE_URL")
        if env_override:
            return env_override
        if self.ENVIRONMENT == "production":
            if not self.DATABASE_URL_SUPABASE:
                raise RuntimeError(
                    "ENVIRONMENT=production but DATABASE_URL_SUPABASE is empty. "
                    "Set DATABASE_URL in Railway env vars."
                )
            return self.DATABASE_URL_SUPABASE
        return self.DATABASE_URL_LOCAL

    @property
    def effective_redis_url(self) -> str:
        """Priority: env REDIS_URL > self.REDIS_URL."""
        return os.environ.get("REDIS_URL") or self.REDIS_URL


settings = Settings()
