"""Request/response models for the REST API."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator, model_validator

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def normalize_hex(value: str | None) -> str | None:
    """'abc', '#ABC', 'aabbcc' -> '#aabbcc'. Empty string -> None."""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    match = _HEX_RE.match(value)
    if not match:
        raise ValueError("hex must look like #rrggbb or #rgb")
    digits = match.group(1).lower()
    if len(digits) == 3:
        digits = "".join(ch * 2 for ch in digits)
    return f"#{digits}"


def _strip_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


# ---------- storages ----------

class StorageIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    nfc_tag: str | None = None
    at_hand: bool = False

    _strip = field_validator("nfc_tag")(_strip_or_none)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be empty")
        return v


class StoragePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    nfc_tag: str | None = None
    at_hand: bool | None = None

    _strip = field_validator("nfc_tag")(_strip_or_none)


class Storage(BaseModel):
    id: int
    name: str
    nfc_tag: str | None
    at_hand: bool
    paint_count: int = 0
    pot_count: int = 0


# ---------- tags ----------

class TagIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    color: str = "#888888"

    @field_validator("color")
    @classmethod
    def _color(cls, v: str) -> str:
        return normalize_hex(v) or "#888888"


class TagPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=50)
    color: str | None = None

    @field_validator("color")
    @classmethod
    def _color(cls, v: str | None) -> str | None:
        return normalize_hex(v)


class Tag(BaseModel):
    id: int
    name: str
    color: str
    keywords: list[str] = []


# ---------- manufacturers ----------

class ManufacturerIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be empty")
        return v


class Manufacturer(BaseModel):
    id: int
    name: str
    paint_count: int = 0


class PaintIn(BaseModel):
    """Give the manufacturer either by id (from /api/manufacturers) or by name;
    an unknown name is added to the manufacturer list."""

    name: str = Field(min_length=1, max_length=200)
    manufacturer_id: int | None = None
    manufacturer: str | None = Field(default=None, max_length=100)
    code: str | None = Field(default=None, max_length=50)
    hex: str | None = None
    quantity: int = Field(default=1, ge=0)
    storage_id: int | None = None
    tag_ids: list[int] = []

    _strip_code = field_validator("code")(_strip_or_none)
    _hex = field_validator("hex")(normalize_hex)

    @field_validator("name")
    @classmethod
    def _required_text(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be empty")
        return v

    @model_validator(mode="after")
    def _need_manufacturer(self) -> "PaintIn":
        if self.manufacturer_id is None and not (self.manufacturer or "").strip():
            raise ValueError("give manufacturer_id or manufacturer")
        return self


class PaintPatch(BaseModel):
    """Only the fields that are sent get changed. Send null to clear an optional field."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    manufacturer_id: int | None = None
    manufacturer: str | None = Field(default=None, min_length=1, max_length=100)
    code: str | None = Field(default=None, max_length=50)
    hex: str | None = None
    quantity: int | None = Field(default=None, ge=0)
    storage_id: int | None = None
    tag_ids: list[int] | None = None

    _strip_code = field_validator("code")(_strip_or_none)
    _hex = field_validator("hex")(normalize_hex)


class StorageRef(BaseModel):
    id: int
    name: str
    at_hand: bool


class TagRef(BaseModel):
    id: int
    name: str
    color: str


class Paint(BaseModel):
    id: int
    name: str
    manufacturer: str
    manufacturer_id: int
    code: str | None
    hex: str | None
    quantity: int
    storage: StorageRef | None
    tags: list[TagRef]
    created_at: str
    updated_at: str
