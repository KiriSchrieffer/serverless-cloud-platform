from fastapi import FastAPI

from backend.app.api.routes import auth, functions, invocations, metrics, workers


def create_app() -> FastAPI:
    app = FastAPI(title="Serverless Cloud Platform API")

    app.include_router(auth.router, prefix="/auth", tags=["auth"])
    app.include_router(functions.router, prefix="/functions", tags=["functions"])
    app.include_router(invocations.router, prefix="/invocations", tags=["invocations"])
    app.include_router(metrics.router, prefix="/metrics", tags=["metrics"])
    app.include_router(workers.router, prefix="/workers", tags=["workers"])

    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
