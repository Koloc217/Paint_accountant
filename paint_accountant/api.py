"""REST API: /api/paints, /api/storages, /api/tags, /api/manufacturers."""

from __future__ import annotations

import io
import os
import sqlite3
from dataclasses import replace
from typing import Iterator, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from starlette.concurrency import run_in_threadpool

from . import repository as repo
from . import __version__, backup, export, lookup, recognition
from .db import connect
from .netinfo import lan_url
from .config import save_language, save_lookup
from .schemas import (
    BulkIn,
    BulkResult,
    LanguageIn,
    LookupSettingIn,
    RouteIn,
    Manufacturer,
    ManufacturerIn,
    MergeIn,
    MoveIn,
    PaintCreated,
    PotsIn,
    PotsResult,
    Paint,
    PaintIn,
    PaintPatch,
    Storage,
    StorageIn,
    StoragePatch,
    Tag,
    TagIn,
    TagPatch,
)

router = APIRouter(prefix="/api")


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    conn = connect(request.app.state.settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


Conn = Depends(get_conn)


def _translate(exc: Exception) -> HTTPException:
    if isinstance(exc, repo.DuplicateAtHand):
        return HTTPException(status.HTTP_409_CONFLICT, {
            "code": "duplicate_at_hand",
            "message": str(exc),
            "storage": exc.storage,
            "have": exc.have,
            "suggest": exc.suggest,
        })
    if isinstance(exc, repo.PaintExists):
        return HTTPException(status.HTTP_409_CONFLICT, {
            "code": "paint_exists",
            "message": str(exc),
            "paint_id": exc.paint_id,
        })
    if isinstance(exc, repo.NotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if isinstance(exc, repo.Conflict):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, repo.InvalidReference):
        return HTTPException(422, str(exc))
    raise exc


_DOMAIN_ERRORS = (repo.NotFound, repo.Conflict, repo.InvalidReference, repo.DuplicateAtHand)


@router.get("/health", tags=["system"])
def health(conn: sqlite3.Connection = Conn) -> dict:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    return {"status": "ok", "schema_version": version}


@router.get("/info", tags=["system"])
def info(request: Request) -> dict:
    """Version, the address other devices on the network can use (for NFC/QR links), interface language."""
    settings = request.app.state.settings
    how = lookup.route(settings)
    return {"version": __version__, "lan_url": lan_url(settings.port),
            "language": request.app.state.language,
            "lookup": {"upcitemdb": settings.lookup_enabled, "via": how.kind, "route": how.label,
                       "proxy": settings.lookup_proxy or "", "interface": settings.lookup_interface or "",
                       "from_env": how.from_env}}


@router.put("/settings/language", tags=["system"])
def set_language(body: LanguageIn, request: Request) -> dict:
    """Interface language for every device; saved into config.ini ([ui] language)."""
    settings = request.app.state.settings
    try:
        save_language(settings.config_file, body.language)
    except OSError as exc:
        raise HTTPException(500, f"can't write {settings.config_file}: {exc}")
    request.app.state.language = body.language
    return {"language": body.language, "config_file": str(settings.config_file),
            "overridden_by_env": bool(os.environ.get("PAINT_LANGUAGE"))}


@router.put("/settings/lookup", tags=["system"])
def set_lookup(body: LookupSettingIn, request: Request) -> dict:
    """Online barcode lookup for every device, saved into config.ini, section [lookup].

    Only what is given changes; an empty proxy or interface clears that one. A proxy is only
    accepted if it can be parsed — a typo in it would otherwise leave the lookup unreachable
    with nothing to say why."""
    settings = request.app.state.settings
    if body.proxy:
        try:
            lookup.parse_proxy(body.proxy)
        except lookup.LookupFailed as exc:
            raise HTTPException(422, {"code": exc.code, "message": str(exc)})
    try:
        save_lookup(settings.config_file, body.enabled, body.proxy, body.interface)
    except OSError as exc:
        raise HTTPException(500, f"can't write {settings.config_file}: {exc}")
    changed = {}
    if body.enabled is not None:
        changed["lookup_enabled"] = body.enabled
    if body.proxy is not None:
        changed["lookup_proxy"] = body.proxy.strip() or None
    if body.interface is not None:
        changed["lookup_interface"] = body.interface.strip() or None
    settings = replace(settings, **changed)
    request.app.state.settings = settings
    how = lookup.route(settings)
    overridden = sorted(name for name, var in (("upcitemdb", "PAINT_LOOKUP"), ("proxy", "PAINT_LOOKUP_PROXY"),
                                               ("interface", "PAINT_LOOKUP_INTERFACE"))
                        if os.environ.get(var))
    return {"upcitemdb": settings.lookup_enabled, "proxy": settings.lookup_proxy,
            "interface": settings.lookup_interface, "via": how.kind, "route": how.label,
            "config_file": str(settings.config_file), "overridden_by_env": overridden}


@router.post("/settings/lookup/check", tags=["system"])
async def check_lookup(body: RouteIn, request: Request) -> dict:
    """Try to reach UPCitemdb — by the route given, or by the saved one.

    Opens the connection and closes it without asking anything, so it costs none of the
    day's hundred requests. Answers {"ok": true} or 502 with what went wrong."""
    settings = request.app.state.settings
    if body.proxy or body.interface:
        how = lookup.Route(proxy=(body.proxy or "").strip() or None,
                           interface=(body.interface or "").strip() or None)
    else:
        how = lookup.route(settings)
    try:
        await run_in_threadpool(lookup.check, how)
    except lookup.LookupFailed as exc:
        raise HTTPException(_LOOKUP_STATUS.get(exc.code, 502), {"code": exc.code, "message": str(exc)})
    return {"ok": True, "via": how.kind, "route": how.label}


_LOOKUP_STATUS = {
    "lookup_disabled": 409, "lookup_bad_barcode": 422, "lookup_limit": 429, "lookup_too_fast": 429,
    "lookup_config": 500, "lookup_unreachable": 502, "lookup_error": 502,
}


@router.get("/lookup/upcitemdb", tags=["photo"])
async def lookup_upcitemdb(
    request: Request,
    barcode: str = Query(..., min_length=8, max_length=20, description="EAN-8, UPC-A, EAN-13 or GTIN-14, digits"),
) -> dict:
    """Ask UPCitemdb what this barcode is and turn the answer into suggestions for the paint form.

    The request leaves from the computer running the app (through the proxy or interface from
    config.ini, if set) and carries only the barcode. `found` is false when UPCitemdb doesn't
    know it. Errors come as {"code": "lookup_…", "message": …}: 409 turned off, 429 the free
    limit (100 a day, 6 a minute), 502 no connection."""
    settings = request.app.state.settings

    def work() -> dict:
        conn = connect(settings.db_path)
        try:
            return lookup.lookup(conn, settings, barcode)
        finally:
            conn.close()

    try:
        return await run_in_threadpool(work)
    except lookup.LookupFailed as exc:
        raise HTTPException(_LOOKUP_STATUS.get(exc.code, 502),
                            {"code": exc.code, "message": str(exc), "reset": exc.reset})


@router.get("/lookup/upcitemdb/raw", tags=["photo"])
def lookup_raw(barcode: str | None = Query(None, description="which one; the newest if left out")) -> dict:
    """What UPCitemdb answered for a barcode, exactly as it came.

    For looking into a pot whose fields came out wrong: no request is made, so it costs
    nothing. The last few answers are kept, until the app is restarted."""
    answer = lookup.last_answer(barcode)
    if answer is None:
        raise HTTPException(404, {"code": "lookup_no_answer",
                                  "message": "this barcode hasn't been looked up since the app started"})
    return answer


@router.get("/qr.svg", tags=["system"], response_class=Response,
            responses={200: {"content": {"image/svg+xml": {}}}})
def qr_code(data: str = Query(..., min_length=1, max_length=500, description="text or link to encode")) -> Response:
    """QR code as SVG — for printing storage labels."""
    try:
        import segno
    except ImportError:
        raise HTTPException(503, "QR codes need the segno package: pip install -r requirements.txt")
    buf = io.BytesIO()
    segno.make(data, error="m", micro=False).save(buf, kind="svg", border=2, omitsize=True, xmldecl=False, light="#fff")
    return Response(buf.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "max-age=86400"})


# ---------------- paints ----------------

@router.get("/stats", tags=["stats"])
def stats(conn: sqlite3.Connection = Conn) -> dict:
    """Totals, busiest storages, paints with several pots, probable duplicate records, breakdown by manufacturer and tag."""
    return repo.stats(conn)


@router.get("/paints", response_model=list[Paint], tags=["paints"])
def list_paints(
    q: str | None = Query(None, description="search in name, manufacturer and code"),
    manufacturer: str | None = Query(None, description="manufacturer name"),
    manufacturer_id: int | None = None,
    storage_id: int | None = None,
    no_storage: bool = Query(False, description="only paints without a storage"),
    tag_id: list[int] = Query([], description="repeat to require several tags"),
    no_tags: bool = Query(False, description="only paints with no tag at all; ignores tag_id"),
    at_hand: bool | None = Query(None, description="only paints in (or not in) 'at hand' storages"),
    in_stock: bool | None = Query(None, description="true: quantity > 0, false: quantity = 0"),
    barcode: str | None = Query(None, description="exact barcode"),
    uuid: list[str] = Query([], description="repeat to get several paints by their permanent uuid"),
    sort: Literal["maker", "name", "qty_desc", "qty_asc", "new"] = Query(
        "maker", description="maker: manufacturer then name; name; qty_desc/qty_asc: by pots; new: recently added first"),
    conn: sqlite3.Connection = Conn,
):
    return repo.list_paints(
        conn,
        q=q,
        manufacturer=manufacturer,
        manufacturer_id=manufacturer_id,
        storage_id=storage_id,
        no_storage=no_storage,
        tag_ids=tag_id,
        no_tags=no_tags,
        at_hand=at_hand,
        in_stock=in_stock,
        barcode=barcode,
        uuids=uuid,
        sort=sort,
    )


def _target(body) -> object:
    """storage_id left out -> automatic placement; given (even null) -> exactly there."""
    return body.storage_id if "storage_id" in body.model_fields_set else repo.AUTO


@router.post("/paints", response_model=PaintCreated, status_code=201, tags=["paints"])
def create_paint(body: PaintIn, conn: sqlite3.Connection = Conn):
    try:
        return repo.create_paint(
            conn, body.model_dump(exclude={"storage_id", "allow_duplicate"}), _target(body), body.allow_duplicate
        )
    except _DOMAIN_ERRORS as exc:
        raise _translate(exc)


@router.post("/paints/{paint_id}/add", response_model=PotsResult, tags=["pots"])
def add_pots(paint_id: int, body: PotsIn = PotsIn(), conn: sqlite3.Connection = Conn):
    """Add pots. Without storage_id they go to the default storage.
    A second pot of a paint in an "at hand" storage needs allow_duplicate=true; otherwise the answer
    is 409 with detail.code "duplicate_at_hand", the storage, how many are there and a suggested storage."""
    try:
        return repo.add_pots(conn, paint_id, body.count, _target(body), body.allow_duplicate)
    except _DOMAIN_ERRORS as exc:
        raise _translate(exc)


@router.post("/paints/{paint_id}/remove", response_model=PotsResult, tags=["pots"])
def remove_pots(paint_id: int, body: PotsIn = PotsIn(), conn: sqlite3.Connection = Conn):
    """A pot is used up. Without storage_id it's taken from "at hand" first."""
    try:
        return repo.remove_pots(conn, paint_id, body.count, _target(body))
    except _DOMAIN_ERRORS as exc:
        raise _translate(exc)


@router.post("/paints/{paint_id}/move", response_model=PotsResult, tags=["pots"])
def move_pots(paint_id: int, body: MoveIn, conn: sqlite3.Connection = Conn):
    """Move pots from one storage to another (null = without a storage)."""
    try:
        return repo.move_pots(conn, paint_id, body.from_storage_id, body.to_storage_id, body.count)
    except _DOMAIN_ERRORS as exc:
        raise _translate(exc)


@router.post("/paints/{paint_id}/merge", response_model=Paint, tags=["paints"])
def merge_paints(paint_id: int, body: MergeIn, conn: sqlite3.Connection = Conn):
    """Fold a duplicate paint into this one: pots and tags move here, the duplicate is deleted
    (its uuid goes with it). `barcode` picks which barcode stays."""
    try:
        return repo.merge_paints(conn, paint_id, body.from_id, body.barcode)
    except _DOMAIN_ERRORS as exc:
        raise _translate(exc)


@router.post("/paints/bulk", response_model=BulkResult, tags=["paints"])
def bulk_paints(body: BulkIn, conn: sqlite3.Connection = Conn):
    """Change several paints at once: move all their pots to a storage, or add / remove tags.
    Everything is done in one transaction; `changed` tells how many paints actually changed."""
    kw: dict = {"to_storage_id": body.to_storage_id, "tag_ids": body.tag_ids}
    if "from_storage_id" in body.model_fields_set:
        kw["from_storage_id"] = body.from_storage_id
    try:
        return repo.bulk_paints(conn, body.paint_ids, body.action, **kw)
    except _DOMAIN_ERRORS as exc:
        raise _translate(exc)


@router.get("/paints/by-uuid/{paint_uuid}", response_model=Paint, tags=["paints"])
def get_paint_by_uuid(paint_uuid: str, conn: sqlite3.Connection = Conn):
    """The paint by its permanent uuid: use this from other apps instead of the numeric id."""
    try:
        return repo.get_paint_by_uuid(conn, paint_uuid)
    except _DOMAIN_ERRORS as exc:
        raise _translate(exc)


@router.get("/paints/{paint_id}", response_model=Paint, tags=["paints"])
def get_paint(paint_id: int, conn: sqlite3.Connection = Conn):
    try:
        return repo.get_paint(conn, paint_id)
    except _DOMAIN_ERRORS as exc:
        raise _translate(exc)


@router.patch("/paints/{paint_id}", response_model=Paint, tags=["paints"])
def update_paint(paint_id: int, body: PaintPatch, conn: sqlite3.Connection = Conn):
    try:
        return repo.update_paint(conn, paint_id, body.model_dump(exclude_unset=True))
    except _DOMAIN_ERRORS as exc:
        raise _translate(exc)


@router.delete("/paints/{paint_id}", status_code=204, tags=["paints"])
def delete_paint(paint_id: int, conn: sqlite3.Connection = Conn):
    try:
        repo.delete_paint(conn, paint_id)
    except _DOMAIN_ERRORS as exc:
        raise _translate(exc)
    return Response(status_code=204)


# ---------------- manufacturers ----------------

@router.get("/manufacturers", response_model=list[Manufacturer], tags=["manufacturers"])
def list_manufacturers(q: str | None = None, conn: sqlite3.Connection = Conn):
    return repo.list_manufacturers(conn, q)


@router.post("/manufacturers", response_model=Manufacturer, status_code=201, tags=["manufacturers"])
def create_manufacturer(body: ManufacturerIn, conn: sqlite3.Connection = Conn):
    try:
        return repo.create_manufacturer(conn, body.model_dump())
    except _DOMAIN_ERRORS as exc:
        raise _translate(exc)


@router.patch("/manufacturers/{manufacturer_id}", response_model=Manufacturer, tags=["manufacturers"])
def update_manufacturer(manufacturer_id: int, body: ManufacturerIn, conn: sqlite3.Connection = Conn):
    try:
        return repo.update_manufacturer(conn, manufacturer_id, body.model_dump())
    except _DOMAIN_ERRORS as exc:
        raise _translate(exc)


@router.delete("/manufacturers/{manufacturer_id}", status_code=204, tags=["manufacturers"])
def delete_manufacturer(manufacturer_id: int, conn: sqlite3.Connection = Conn):
    """Only a manufacturer without paints can be deleted (409 otherwise)."""
    try:
        repo.delete_manufacturer(conn, manufacturer_id)
    except _DOMAIN_ERRORS as exc:
        raise _translate(exc)
    return Response(status_code=204)


# ---------------- storages ----------------

@router.get("/storages", response_model=list[Storage], tags=["storages"])
def list_storages(at_hand: bool | None = None, conn: sqlite3.Connection = Conn):
    return repo.list_storages(conn, at_hand)


@router.post("/storages", response_model=Storage, status_code=201, tags=["storages"])
def create_storage(body: StorageIn, conn: sqlite3.Connection = Conn):
    try:
        return repo.create_storage(conn, body.model_dump())
    except _DOMAIN_ERRORS as exc:
        raise _translate(exc)


@router.get("/storages/{storage_id}", response_model=Storage, tags=["storages"])
def get_storage(storage_id: int, conn: sqlite3.Connection = Conn):
    try:
        return repo.get_storage(conn, storage_id)
    except _DOMAIN_ERRORS as exc:
        raise _translate(exc)


@router.patch("/storages/{storage_id}", response_model=Storage, tags=["storages"])
def update_storage(storage_id: int, body: StoragePatch, conn: sqlite3.Connection = Conn):
    try:
        return repo.update_storage(conn, storage_id, body.model_dump(exclude_unset=True))
    except _DOMAIN_ERRORS as exc:
        raise _translate(exc)


@router.delete("/storages/{storage_id}", status_code=204, tags=["storages"])
def delete_storage(storage_id: int, conn: sqlite3.Connection = Conn):
    """Paints inside are kept and become 'without storage'."""
    try:
        repo.delete_storage(conn, storage_id)
    except _DOMAIN_ERRORS as exc:
        raise _translate(exc)
    return Response(status_code=204)


# ---------------- tags ----------------

@router.get("/tags", response_model=list[Tag], tags=["tags"])
def list_tags(conn: sqlite3.Connection = Conn):
    return repo.list_tags(conn)


@router.post("/tags", response_model=Tag, status_code=201, tags=["tags"])
def create_tag(body: TagIn, conn: sqlite3.Connection = Conn):
    try:
        return repo.create_tag(conn, body.model_dump())
    except _DOMAIN_ERRORS as exc:
        raise _translate(exc)


@router.patch("/tags/{tag_id}", response_model=Tag, tags=["tags"])
def update_tag(tag_id: int, body: TagPatch, conn: sqlite3.Connection = Conn):
    try:
        return repo.update_tag(conn, tag_id, body.model_dump(exclude_unset=True))
    except _DOMAIN_ERRORS as exc:
        raise _translate(exc)


@router.delete("/tags/{tag_id}", status_code=204, tags=["tags"])
def delete_tag(tag_id: int, conn: sqlite3.Connection = Conn):
    try:
        repo.delete_tag(conn, tag_id)
    except _DOMAIN_ERRORS as exc:
        raise _translate(exc)
    return Response(status_code=204)


# ---------------- photo recognition ----------------

MAX_UPLOAD = 20 * 1024 * 1024


async def read_body(request: Request, limit: int, too_large: str) -> bytes:
    """The request body, refused as soon as it is known to be over the limit — by its
    Content-Length, or while it arrives — instead of after all of it is in memory."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise HTTPException(413, too_large)
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HTTPException(413, too_large)
        chunks.append(chunk)
    return b"".join(chunks)


@router.get("/recognize", tags=["photo"])
def recognize_status() -> dict:
    """Whether photo recognition is installed (it is an optional part)."""
    return recognition.availability()


@router.post(
    "/recognize",
    tags=["photo"],
    openapi_extra={"requestBody": {"required": True, "content": {"image/*": {"schema": {"type": "string", "format": "binary"}}}}},
)
async def recognize(
    request: Request,
    barcode_only: bool = Query(False, description="skip text reading — for frames of a live camera"),
) -> dict:
    """Send a photo as the request body (Content-Type: image/jpeg or similar).

    Returns the text and barcodes found, suggestions for the paint form
    and paints already in the catalog that look like a match.
    """
    info = recognition.availability()
    if not info["available"]:
        raise HTTPException(503, f"photo recognition is not installed: {info['install']}")
    data = await read_body(request, MAX_UPLOAD, "photo is too large (max 20 MB)")
    if not data:
        raise HTTPException(422, "send a photo in the request body")

    db_path = request.app.state.settings.db_path

    def work() -> dict:
        reading = recognition.read_image(data, ocr=not barcode_only)
        conn = connect(db_path)
        try:
            return recognition.suggest(conn, reading)
        finally:
            conn.close()

    try:
        return await run_in_threadpool(work)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


# ---------------- backup ----------------

def _download(data: bytes, filename: str, media_type: str) -> Response:
    return Response(data, media_type=media_type, headers={
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Cache-Control": "no-store",
    })


@router.get("/backup/paints.csv", tags=["backup"], response_class=Response,
            responses={200: {"content": {"text/csv": {}}}})
def backup_csv(lang: str = Query("ru", pattern="^(ru|en)$", description="column names and separator"),
               conn: sqlite3.Connection = Conn) -> Response:
    """Every paint by manufacturer and name, with its color when set. For reading, not for import."""
    return _download(backup.paints_csv(conn, lang), f"paints-{backup._today()}.csv", "text/csv; charset=utf-8")


@router.get("/export/paints.md", tags=["backup"], response_class=Response,
            responses={200: {"content": {"text/markdown": {}}}})
def export_markdown(
    level: Literal["short", "full"] = Query(
        "short", description="short: in-stock paints for a chatbot; full: storages, pots, barcodes, out of stock"),
    lang: str = Query("ru", pattern="^(ru|en)$"),
    download: bool = Query(False, description="true: as a file; false: plain text to show or copy"),
    q: str | None = None,
    manufacturer_id: int | None = None,
    storage_id: int | None = None,
    no_storage: bool = False,
    tag_id: list[int] = Query([]),
    at_hand: bool | None = None,
    in_stock: bool | None = None,
    conn: sqlite3.Connection = Conn,
) -> Response:
    """The paint list as Markdown, with the same filters as /paints."""
    text = export.paints_markdown(
        conn, level, lang, q=(q or "").strip() or None, manufacturer_id=manufacturer_id, storage_id=storage_id,
        no_storage=no_storage, tag_ids=tag_id, at_hand=at_hand, in_stock=in_stock)
    data = text.encode("utf-8")
    media = "text/markdown; charset=utf-8"
    if download:
        return _download(data, f"paints-{level}-{backup._today()}.md", media)
    return Response(data, media_type=media, headers={"Cache-Control": "no-store"})


@router.get("/backup/dump", tags=["backup"], response_class=Response,
            responses={200: {"content": {"application/zip": {}}}})
async def backup_dump(request: Request) -> Response:
    """The whole catalog (database + photos) as a .zip — to restore here or on another machine."""
    data = await run_in_threadpool(backup.make_dump, request.app.state.settings)
    return _download(data, backup.dump_filename(), "application/zip")


@router.post("/backup/restore", tags=["backup"],
             openapi_extra={"requestBody": {"content": {"application/zip": {}}, "required": True}})
async def backup_restore(request: Request) -> dict:
    """Replace the whole catalog with a dump sent as the request body. The current
    database is copied aside first (paints.before-restore-….sqlite3 in the data folder)."""
    data = await read_body(request, backup.MAX_DUMP_BYTES, "the file is too large")
    if not data:
        raise HTTPException(422, "send the dump file in the request body")
    try:
        return await run_in_threadpool(backup.restore_dump, request.app.state.settings, data)
    except backup.BadDump as exc:
        raise HTTPException(422, str(exc))
