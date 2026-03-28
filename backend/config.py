"""Application configuration loaded from environment variables."""

import os


class Settings:
    API_SECRET_KEY: str | None = os.getenv("API_SECRET_KEY")
    AUTH_DISABLED: bool = os.getenv("AUTH_DISABLED", "").lower() == "true"
    MAX_PROJECTS_PER_USER: int = int(os.getenv("MAX_PROJECTS_PER_USER", "20"))
    MAX_CONCURRENT_PIPELINES_PER_USER: int = int(os.getenv("MAX_CONCURRENT_PIPELINES_PER_USER", "2"))
    MAX_UPLOAD_SIZE_MB: int = int(os.getenv("MAX_UPLOAD_SIZE_MB", "50"))
    PROJECT_TTL_HOURS: int = int(os.getenv("PROJECT_TTL_HOURS", "72"))
    CLEANUP_INTERVAL_MINUTES: int = int(os.getenv("CLEANUP_INTERVAL_MINUTES", "60"))
    CORS_ORIGINS: list[str] = os.getenv("CORS_ORIGINS", "*").split(",")
    DATA_DIR: str = os.getenv("DATA_DIR", "./data")


settings = Settings()
