"""Settings.

The app is portable: everything lives inside the folder it was unpacked to.

    Paint_accountant/
    ├── run.py              start here
    ├── config.ini          your settings (created from config.example.ini on first start)
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
CONFIG_EXAMPLE = APP_DIR / "config.example.ini"

DEFAULT_HOST = "0.0.0.0"  # reachable from a phone on the same network
DEFAULT_PORT = 8642
DEFAULT_DATA_DIR = "data"
LANGUAGES = ("auto", "ru", "en")  # auto = the browser's language
DEFAULT_LANGUAGE = "auto"


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    data_dir: Path
    language: str = DEFAULT_LANGUAGE
    config_file: Path = CONFIG_FILE  # where the language chosen in the app is saved

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
    """Keys of the [server] and [ui] sections (their names don't overlap)."""
    if not path.is_file():
        return {}
    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")
    return {k: v.strip() for section in ("server", "ui") if parser.has_section(section)
            for k, v in parser[section].items() if v.strip()}


def _language(value: str | None) -> str:
    value = (value or "").strip().lower()
    return value if value in LANGUAGES else DEFAULT_LANGUAGE


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
    path = Path(config_file) if config_file else CONFIG_FILE
    file = _read_config_file(path)
    return Settings(
        host=host or env.get("PAINT_HOST") or file.get("host") or DEFAULT_HOST,
        port=int(port or env.get("PAINT_PORT") or file.get("port") or DEFAULT_PORT),
        data_dir=_resolve(data_dir or env.get("PAINT_DATA_DIR") or file.get("data_dir") or DEFAULT_DATA_DIR),
        language=_language(env.get("PAINT_LANGUAGE") or file.get("language")),
        config_file=path,
    )


def save_language(path: Path, language: str) -> None:
    """Write `language = …` into the [ui] section, keeping the rest of the file (and its comments) as is."""
    language = _language(language)
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    lines = text.splitlines()
    section = None
    ui_at = None
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s[1:-1].strip().lower()
            if section == "ui":
                ui_at = i
        elif section == "ui" and s.split("=", 1)[0].strip().lower() == "language" and "=" in s:
            lines[i] = f"language = {language}"
            break
    else:
        if ui_at is None:
            if lines and lines[-1].strip():
                lines.append("")
            lines += ["[ui]", "; Interface language: auto (the browser's), ru or en. Also set in the app's Settings.",
                      f"language = {language}"]
        else:
            lines.insert(ui_at + 1, f"language = {language}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def ensure_config_file(path: Path = CONFIG_FILE, example: Path = CONFIG_EXAMPLE) -> bool:
    """Create config.ini from the example on first start. Returns True if created.

    config.ini is personal and stays out of git; the example is what's in the repo.
    An existing config.ini is never touched.
    """
    if path.exists() or not example.is_file():
        return False
    path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    return True
