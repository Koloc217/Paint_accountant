"""FastAPI application factory."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from . import auth_api
from .api import router
from .config import Settings, load_settings
from .db import init_db

WEB_DIR = Path(__file__).resolve().parent / "web"


def _asset_version() -> str:
    """Changes whenever a web file changes, so phones don't keep stale JS/CSS."""
    newest = max(int(p.stat().st_mtime) for p in WEB_DIR.rglob("*") if p.is_file())
    return f"{__version__}-{newest}"


class _WebFiles(StaticFiles):
    """The page's files, always checked with the server before use ("no-cache").

    app.js loads the other scripts by plain names (import './js/core.js'), so the ?v= mark
    of the page can't reach them. Without this a phone could run a new app.js with an old
    module after an update. The check is cheap: an unchanged file is answered with 304.
    """

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    settings.ensure_dirs()
    init_db(settings.db_path)

    app = FastAPI(title="Paint Accountant", version=__version__)
    app.state.settings = settings
    app.state.language = settings.language  # changed from Settings at runtime
    app.include_router(router)
    app.include_router(auth_api.router)
    auth_api.install(app)

    @app.exception_handler(OverflowError)
    async def number_too_large(request: Request, exc: OverflowError) -> JSONResponse:
        # an id or count beyond SQLite's 64-bit integers (/api/paints/99999999999999999999)
        return JSONResponse({"detail": "number is too large"}, status_code=422)
    app.mount("/static", _WebFiles(directory=WEB_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> HTMLResponse:
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(
            html.replace("__V__", _asset_version()).replace("__LANG__", app.state.language),
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/storage/{storage_id}", include_in_schema=False)
    def storage_link(storage_id: int) -> RedirectResponse:
        # Short, stable address to write into an NFC tag or QR code.
        return RedirectResponse(f"/#/storage/{storage_id}")

    return app
