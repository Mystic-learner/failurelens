from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx

from app.core.config import Settings
from app.main import create_app


def make_settings(**overrides: object) -> Settings:
    """Settings that ignore any local .env file, so tests behave the same everywhere."""
    values: dict[str, object] = {
        "embedding_provider": "fake",
        "worker_count": 0,
        "embedding_worker_enabled": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


@asynccontextmanager
async def running_client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    """Run the app's lifespan (engine, queue) and yield a client bound to it."""
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            client.app = app  # type: ignore[attr-defined]
            yield client
