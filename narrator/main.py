"""App assembly and uvicorn entry point (`python -m narrator.main`)."""

import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import FastAPI

from .abs import AbsClient
from .cache import Cache
from .config import Settings
from .kokoro import KokoroClient
from .routes import router
from .session import SessionManager
from .version import __version__


def create_app(
    settings: Settings | None = None,
    http_transport: httpx.AsyncBaseTransport | None = None,  # tests inject a MockTransport
) -> FastAPI:
    settings = settings or Settings()  # type: ignore[call-arg]
    logging.basicConfig(level=settings.log_level.upper())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with httpx.AsyncClient(transport=http_transport) as http:
            app.state.abs = AbsClient(settings.abs_url, settings.abs_token, http)
            kokoro = KokoroClient(settings.kokoro_url, settings.kokoro_voice, http)
            app.state.sessions = SessionManager(
                settings, app.state.abs, kokoro, Cache(settings.cache_dir)
            )
            warmup = asyncio.create_task(kokoro.warm_up())
            yield
            warmup.cancel()

    app = FastAPI(title="norrsaga-narrator", version=__version__, lifespan=lifespan)
    app.include_router(router)
    return app


if __name__ == "__main__":
    _settings = Settings()  # type: ignore[call-arg]
    uvicorn.run(create_app(_settings), host="0.0.0.0", port=_settings.port)
