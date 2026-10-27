"""REST API: /api/paints, /api/storages, /api/tags, /api/manufacturers."""

from __future__ import annotations

import io
import sqlite3
from typing import Iterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from starlette.concurrency import run_in_threadpool

from . import repository as repo
from . import __version__, recognition
from .db import connect
from .netinfo import lan_url
from .schemas import (
    Manufacturer,
    ManufacturerIn,
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
    """Version and the address other devices on the network can use (for NFC/QR links)."""
    return {"version": __version__, "lan_url": lan_url(request.app.state.settings.port)}


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

@router.get("/paints", response_model=list[Paint], tags=["paints"])
def list_paints(
    q: str | None = Query(None, description="search in name, manufacturer and code"),
    manufacturer: str | None = Query(None, description="manufacturer name"),
    manufacturer_id: int | None = None,
    storage_id: int | None = None,
    no_storage: bool = Query(False, description="only paints without a storage"),
    tag_id: list[int] = Query([], description="repeat to require several tags"),
    at_hand: bool | None = Query(None, description="only paints in (or not in) 'at hand' storages"),
    in_stock: bool | None = Query(None, description="true: quantity > 0, false: quantity = 0"),
    barcode: str | None = Query(None, description="exact barcode"),
    uuid: list[str] = Query([], description="repeat to get several paints by their permanent uuid"),
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
        at_hand=at_hand,
        in_stock=in_stock,
        barcode=barcode,
        uuids=uuid,
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
    data = await request.body()
    if not data:
        raise HTTPException(422, "send a photo in the request body")
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "photo is too large (max 20 MB)")

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
