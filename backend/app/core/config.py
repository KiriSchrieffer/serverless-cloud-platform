from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://serverless:serverless@localhost:5432/serverless"
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret_key: str = "change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
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
