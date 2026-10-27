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
    is_default: bool = False
    copies_storage_id: int | None = None

    _strip = field_validator("nfc_tag")(_strip_or_none)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be empty")
        return v


class StoragePatch(BaseModel):
    """Only sent fields change. is_default=true makes this the default storage
    (the previous default stops being one). copies_storage_id=null turns the copies rule off."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    nfc_tag: str | None = None
    at_hand: bool | None = None
    is_default: bool | None = None
    copies_storage_id: int | None = None

    _strip = field_validator("nfc_tag")(_strip_or_none)


class StorageName(BaseModel):
    id: int
    name: str


class Storage(BaseModel):
    id: int
    name: str
    nfc_tag: str | None
    at_hand: bool
    is_default: bool = False
    copies_storage: StorageName | None = None
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
    paint_count: int = 0


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
    barcode: str | None = Field(default=None, max_length=100)
    hex: str | None = None
    quantity: int = Field(default=1, ge=0)
    storage_id: int | None = Field(
        default=None,
        description="leave out to use the default storage; give an id to put the pots there; null = without a storage",
    )
    allow_duplicate: bool = Field(False, description="allow several pots of this paint in an 'at hand' storage")
    tag_ids: list[int] = []

    _strip_code = field_validator("code", "barcode")(_strip_or_none)
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
    barcode: str | None = Field(default=None, max_length=100)
    hex: str | None = None
    tag_ids: list[int] | None = None

    _strip_code = field_validator("code", "barcode")(_strip_or_none)
    _hex = field_validator("hex")(normalize_hex)


class StorageRef(BaseModel):
    id: int
    name: str
    at_hand: bool
    is_default: bool = False


class Location(BaseModel):
    storage: StorageRef | None  # null = without a storage
    quantity: int


class TagRef(BaseModel):
    id: int
    name: str
    color: str


class Paint(BaseModel):
    id: int
    uuid: str = Field(description="permanent id for links from other apps; never changes")
    name: str
    manufacturer: str
    manufacturer_id: int
    code: str | None
    barcode: str | None
    hex: str | None
    quantity: int = Field(description="pots in all storages")
    locations: list[Location]
    tags: list[TagRef]
    created_at: str
    updated_at: str


class PaintCreated(Paint):
    notice: dict | None = Field(None, description="where the pots went, and storages already holding this paint")


# ---------- pots ----------

class PotsIn(BaseModel):
    count: int = Field(default=1, ge=1, le=100)
    storage_id: int | None = Field(
        default=None,
        description="leave out: adding goes to the default storage, removing takes from 'at hand' first; "
        "give an id (or null = without storage) to use that one",
    )
    allow_duplicate: bool = Field(
        False, description="adding: allow a second pot of this paint in an 'at hand' storage (else 409 duplicate_at_hand)"
    )


class MoveIn(BaseModel):
    from_storage_id: int | None
    to_storage_id: int | None
    count: int = Field(default=1, ge=1, le=100)


class PotsResult(BaseModel):
    paint: Paint
    notice: dict


class PasswordIn(BaseModel):
    password: str = Field(..., min_length=1, max_length=200)


class NewPasswordIn(BaseModel):
    password: str = Field(..., min_length=4, max_length=200, description="at least 4 characters")
