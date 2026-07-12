from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://serverless:serverless@localhost:5432/serverless"
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret_key: str = Field(
        default="local-development-only-change-before-deploy",
        min_length=32,
    )
    jwt_algorithm: Literal["HS256"] = "HS256"
    jwt_issuer: str = "serverless-cloud-platform"
    jwt_audience: str = "serverless-cloud-platform-api"
    access_token_expire_minutes: int = Field(default=60, ge=1)
    password_bcrypt_rounds: int = Field(default=12, ge=4, le=31)
    invocation_rate_limit_capacity: int = Field(default=100, ge=1)
    invocation_rate_limit_period_seconds: int = Field(default=60, ge=1)
    invocation_stream: str = "invocations"
    invocation_consumer_group: str = "workers"
    dispatch_poll_seconds: float = 0.25
    dispatch_batch_size: int = 100
    package_storage_dir: str = "storage/packages"
    result_storage_dir: str = "storage/results"
    log_storage_dir: str = "storage/logs"
    stale_worker_seconds: int = 15

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
