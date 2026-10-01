from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LEVELING_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./leveling.db"
    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str | None = None
    celery_result_backend: str | None = None

    # Components above this size are handled as sparse; tiny components may use
    # a dense QR solely to produce an explicit diagnostic nullspace.
    dense_qr_limit: int = 5_000
    condition_warning: float = 1e10
    condition_failure: float = 1e14
    svd_rank_eps: float = 1e-10
    qr_rank_tol: float = 1e-10

    @property
    def broker_url(self) -> str:
        return self.celery_broker_url or self.redis_url

    @property
    def result_backend(self) -> str:
        return self.celery_result_backend or self.redis_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
