"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from . import __version__
from .api import router
from .config import Settings, load_settings
from .db import init_db


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    settings.ensure_dirs()
    init_db(settings.db_path)

    app = FastAPI(title="Paint Accountant", version=__version__)
    app.state.settings = settings
    app.include_router(router)

    @app.get("/", include_in_schema=False)
    def index():
        # The web UI comes next; until then the root opens the interactive API docs.
        return RedirectResponse("/docs")

    return app
