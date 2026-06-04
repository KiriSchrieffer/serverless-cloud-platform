from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    database_url: str = "postgresql+asyncpg://serverless:serverless@localhost:5432/serverless"
    redis_url: str = "redis://localhost:6379/0"
    invocation_stream: str = "invocations"
    invocation_consumer_group: str = "workers"
    worker_heartbeat_seconds: int = 5
    stale_worker_seconds: int = 15
    default_max_concurrency: int = 2
    default_max_attempts: int = 3
    runtime_image: str = "serverless-python311-runtime:latest"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = WorkerSettings()
