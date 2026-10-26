"""REST API: /api/paints, /api/storages, /api/tags, /api/manufacturers."""

from __future__ import annotations

import sqlite3
from typing import Iterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from . import repository as repo
from .db import connect
from .schemas import (
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
    if isinstance(exc, repo.NotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if isinstance(exc, repo.Conflict):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, repo.InvalidReference):
        return HTTPException(422, str(exc))
    raise exc


_DOMAIN_ERRORS = (repo.NotFound, repo.Conflict, repo.InvalidReference)


@router.get("/health", tags=["system"])
def health(conn: sqlite3.Connection = Conn) -> dict:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    return {"status": "ok", "schema_version": version}


# ---------------- paints ----------------

@router.get("/paints", response_model=list[Paint], tags=["paints"])
def list_paints(
    q: str | None = Query(None, description="search in name, manufacturer and code"),
    manufacturer: str | None = None,
    storage_id: int | None = None,
    no_storage: bool = Query(False, description="only paints without a storage"),
    tag_id: list[int] = Query([], description="repeat to require several tags"),
    at_hand: bool | None = Query(None, description="only paints in (or not in) 'at hand' storages"),
    in_stock: bool | None = Query(None, description="true: quantity > 0, false: quantity = 0"),
    conn: sqlite3.Connection = Conn,
):
    return repo.list_paints(
        conn,
        q=q,
        manufacturer=manufacturer,
        storage_id=storage_id,
        no_storage=no_storage,
        tag_ids=tag_id,
        at_hand=at_hand,
        in_stock=in_stock,
    )


@router.post("/paints", response_model=Paint, status_code=201, tags=["paints"])
def create_paint(body: PaintIn, conn: sqlite3.Connection = Conn):
    try:
        return repo.create_paint(conn, body.model_dump())
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


@router.get("/manufacturers", response_model=list[str], tags=["paints"])
def list_manufacturers(q: str | None = None, conn: sqlite3.Connection = Conn):
    return repo.list_manufacturers(conn, q)


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
