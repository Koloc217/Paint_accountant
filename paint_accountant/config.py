"""Settings.

The app is portable: everything lives inside the folder it was unpacked to.

    Paint_accountant/
    ├── run.py              start here
    ├── config.ini          your settings (copy from config.example.ini)
    ├── paint_accountant/   the code
    └── data/               created on first start
        ├── paints.sqlite3  the database
        └── photos/         uploaded pictures

Priority, highest first: command-line arguments, environment variables,
config.ini, built-in defaults. Relative paths are resolved against the app
folder, not the current directory, so the app behaves the same however it is
started (by hand, systemd, Windows Task Scheduler).
"""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = APP_DIR / "config.ini"

DEFAULT_HOST = "0.0.0.0"  # reachable from a phone on the same network
DEFAULT_PORT = 8642
DEFAULT_DATA_DIR = "data"


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    data_dir: Path

    @property
    def db_path(self) -> Path:
        return self.data_dir / "paints.sqlite3"

    @property
    def photos_dir(self) -> Path:
        return self.data_dir / "photos"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.photos_dir.mkdir(parents=True, exist_ok=True)


def _read_config_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")
    return {k: v.strip() for k, v in parser["server"].items() if v.strip()} if parser.has_section("server") else {}


def _resolve(path: str | os.PathLike[str]) -> Path:
    p = Path(path).expanduser()
    return p if p.is_absolute() else (APP_DIR / p).resolve()


def load_settings(
    host: str | None = None,
    port: int | None = None,
    data_dir: str | os.PathLike[str] | None = None,
    config_file: str | os.PathLike[str] | None = None,
) -> Settings:
    env = os.environ
    file = _read_config_file(Path(config_file) if config_file else CONFIG_FILE)
    return Settings(
        host=host or env.get("PAINT_HOST") or file.get("host") or DEFAULT_HOST,
        port=int(port or env.get("PAINT_PORT") or file.get("port") or DEFAULT_PORT),
        data_dir=_resolve(data_dir or env.get("PAINT_DATA_DIR") or file.get("data_dir") or DEFAULT_DATA_DIR),
    )
