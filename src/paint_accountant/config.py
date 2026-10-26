"""Settings.

Priority, highest first: command-line arguments, environment variables,
built-in defaults. Nothing here is tied to a particular machine: the default
data folder is the OS's standard per-user data directory
(e.g. ~/.local/share/paint-accountant on Linux,
%LOCALAPPDATA%\\paint-accountant on Windows).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_dir

APP_NAME = "paint-accountant"

DEFAULT_HOST = "0.0.0.0"  # reachable from a phone on the same network
DEFAULT_PORT = 8642


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    data_dir: Path

    @property
    def db_path(self) -> Path:
        return self.data_dir / "paints.sqlite3"


def load_settings(
    host: str | None = None,
    port: int | None = None,
    data_dir: str | os.PathLike[str] | None = None,
) -> Settings:
    env = os.environ
    resolved_dir = Path(
        data_dir
        or env.get("PAINT_DATA_DIR")
        or user_data_dir(APP_NAME, appauthor=False)
    ).expanduser()
    return Settings(
        host=host or env.get("PAINT_HOST") or DEFAULT_HOST,
        port=int(port or env.get("PAINT_PORT") or DEFAULT_PORT),
        data_dir=resolved_dir,
    )
