"""FastAPI application factory."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from . import auth_api
from .api import router
from .config import Settings, load_settings
from .db import init_db

WEB_DIR = Path(__file__).resolve().parent / "web"


def _asset_version() -> str:
    """Changes whenever a web file changes, so phones don't keep stale JS/CSS."""
    newest = max(int(p.stat().st_mtime) for p in WEB_DIR.iterdir() if p.is_file())
    return f"{__version__}-{newest}"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    settings.ensure_dirs()
    init_db(settings.db_path)

    app = FastAPI(title="Paint Accountant", version=__version__)
    app.state.settings = settings
    app.include_router(router)
    app.include_router(auth_api.router)
    auth_api.install(app)
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> HTMLResponse:
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(
            html.replace("__V__", _asset_version()),
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/storage/{storage_id}", include_in_schema=False)
    def storage_link(storage_id: int) -> RedirectResponse:
        # Short, stable address to write into an NFC tag or QR code.
        return RedirectResponse(f"/#/storage/{storage_id}")

    return app
