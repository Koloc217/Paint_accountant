"""Settings.

The app is portable: everything lives inside the folder it was unpacked to.

    Paint_accountant/
    ├── run.py              start here
    ├── config.ini          your settings (created from config.example.ini on first start)
    ├── paint_accountant/   the code
    └── data/               created on first start
        └── paints.sqlite3  the database

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
DEFAULT_LOOKUP = True  # online barcode lookup (UPCitemdb); nothing is sent until the button is pressed


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    data_dir: Path
    language: str = DEFAULT_LANGUAGE
    lookup_enabled: bool = DEFAULT_LOOKUP
    lookup_proxy: str | None = None  # socks5://host:port, socks5h://…, http://…
    lookup_interface: str | None = None  # network interface ("awg0") or local address to send from
    config_file: Path = CONFIG_FILE  # where the language chosen in the app is saved

    @property
    def db_path(self) -> Path:
        return self.data_dir / "paints.sqlite3"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)


def _read_config_file(path: Path) -> dict[str, str]:
    """Keys of the [server], [ui] and [lookup] sections (their names don't overlap)."""
    if not path.is_file():
        return {}
    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")
    return {k: v.strip() for section in ("server", "ui", "lookup") if parser.has_section(section)
            for k, v in parser[section].items() if v.strip()}


class BadSetting(ValueError):
    """A setting that can't be used; the message says which one and where it came from."""


def _port(arg: int | None, env: str | None, file: str | None, path: Path) -> int:
    for value, source in ((arg, "the --port argument"), (env, "the PAINT_PORT environment variable"),
                          (file, f"port in {path}")):
        if value is None or str(value).strip() == "":
            continue
        try:
            port = int(str(value).strip())
        except ValueError:
            port = 0
        if not 1 <= port <= 65535:
            raise BadSetting(f"{source} is {str(value).strip()!r} — the port must be a number from 1 to 65535")
        return port
    return DEFAULT_PORT


def _language(value: str | None) -> str:
    value = (value or "").strip().lower()
    return value if value in LANGUAGES else DEFAULT_LANGUAGE


def _flag(value: str | None, default: bool) -> bool:
    value = (value or "").strip().lower()
    if value in ("on", "yes", "true", "1"):
        return True
    if value in ("off", "no", "false", "0"):
        return False
    return default


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
        port=_port(port, env.get("PAINT_PORT"), file.get("port"), path),
        data_dir=_resolve(data_dir or env.get("PAINT_DATA_DIR") or file.get("data_dir") or DEFAULT_DATA_DIR),
        language=_language(env.get("PAINT_LANGUAGE") or file.get("language")),
        lookup_enabled=_flag(env.get("PAINT_LOOKUP") or file.get("upcitemdb"), DEFAULT_LOOKUP),
        lookup_proxy=(env.get("PAINT_LOOKUP_PROXY") or file.get("proxy") or "").strip() or None,
        lookup_interface=(env.get("PAINT_LOOKUP_INTERFACE") or file.get("interface") or "").strip() or None,
        config_file=path,
    )


def save_option(path: Path, section: str, key: str, value: str, comment: str) -> None:
    """Write `key = value` into [section], keeping the rest of the file (and its comments) as is."""
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    lines = text.splitlines()
    current = None
    section_at = None
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            current = s[1:-1].strip().lower()
            if current == section:
                section_at = i
        elif current == section and s.split("=", 1)[0].strip().lower() == key and "=" in s:
            lines[i] = f"{key} = {value}"
            break
    else:
        if section_at is None:
            if lines and lines[-1].strip():
                lines.append("")
            lines += [f"[{section}]", f"; {comment}", f"{key} = {value}"]
        else:
            lines.insert(section_at + 1, f"{key} = {value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def save_language(path: Path, language: str) -> None:
    save_option(path, "ui", "language", _language(language),
                "Interface language: auto (the browser's), ru or en. Also set in the app's Settings.")


def save_lookup(path: Path, enabled: bool) -> None:
    save_option(path, "lookup", "upcitemdb", "on" if enabled else "off",
                "Online barcode lookup at UPCitemdb: on or off. Also set in the app's Settings.")


def ensure_config_file(path: Path = CONFIG_FILE, example: Path = CONFIG_EXAMPLE) -> bool:
    """Create config.ini from the example on first start. Returns True if created.

    config.ini is personal and stays out of git; the example is what's in the repo.
    An existing config.ini is never touched.
    """
    if path.exists() or not example.is_file():
        return False
    path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    return True
