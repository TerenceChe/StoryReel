"""Application configuration loaded from environment variables."""

import os


class Settings:
    AUTH0_DOMAIN: str | None = os.getenv("AUTH0_DOMAIN")
    AUTH0_AUDIENCE: str | None = os.getenv("AUTH0_AUDIENCE")
    DISABLE_AUTH: bool = os.getenv("DISABLE_AUTH", "").lower() in ("1", "true", "yes")
    LOCAL_OWNER_ID: str = os.getenv("LOCAL_OWNER_ID", "local-user")
    MAX_PROJECTS_PER_USER: int = int(os.getenv("MAX_PROJECTS_PER_USER", "20"))
    MAX_CONCURRENT_PIPELINES_PER_USER: int = int(os.getenv("MAX_CONCURRENT_PIPELINES_PER_USER", "2"))
    MAX_UPLOAD_SIZE_MB: int = int(os.getenv("MAX_UPLOAD_SIZE_MB", "50"))
    CORS_ORIGINS: list[str] = os.getenv("CORS_ORIGINS", "*").split(",")
    DATA_DIR: str = os.getenv("DATA_DIR", "./data")

    @property
    def JWT_ISSUER(self) -> str | None:
        if self.AUTH0_DOMAIN:
            return f"https://{self.AUTH0_DOMAIN}/"
        return None

    @property
    def JWT_JWKS_URI(self) -> str | None:
        if self.AUTH0_DOMAIN:
            return f"https://{self.AUTH0_DOMAIN}/.well-known/jwks.json"
        return None

    @property
    def JWT_AUDIENCE(self) -> str | None:
        return self.AUTH0_AUDIENCE


settings = Settings()
