"""Command line: `python run.py` from the app folder (or `python -m paint_accountant`)."""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from . import auth, backup, recognition
from .db import connect
from .app import create_app
from .netinfo import lan_ip
from .config import (
    CONFIG_FILE,
    DEFAULT_DATA_DIR,
    DEFAULT_HOST,
    DEFAULT_PORT,
    ensure_config_file,
    load_settings,
)


def _urls(host: str, port: int) -> list[str]:
    if host in ("0.0.0.0", "", "::"):
        urls = [f"http://localhost:{port}/  (this computer)"]
        ip = lan_ip()
        if ip:
            urls.append(f"http://{ip}:{port}/  (phone or other devices on your network)")
        return urls
    return [f"http://{host}:{port}/"]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="Self-hosted catalog for miniature paints. "
        f"Settings are read from {CONFIG_FILE.name} next to run.py; arguments override it.",
    )
    parser.add_argument("--host", help=f"address to listen on (default {DEFAULT_HOST}, env PAINT_HOST)")
    parser.add_argument("--port", type=int, help=f"port to listen on (default {DEFAULT_PORT}, env PAINT_PORT)")
    parser.add_argument(
        "--data-dir",
        help=f"folder for the database and photos (default ./{DEFAULT_DATA_DIR} in the app folder, env PAINT_DATA_DIR)",
    )
    parser.add_argument("--config", help=f"settings file to use instead of {CONFIG_FILE.name}")
    parser.add_argument("--reset-auth", action="store_true",
                        help="forgot the password? turn sign-in off (set a new one in Settings) and exit")
    parser.add_argument("--dump", nargs="?", const="", metavar="FILE",
                        help="save the whole catalog to a .zip (default: paint-accountant-<date>.zip here) and exit")
    parser.add_argument("--restore", metavar="FILE",
                        help="replace the catalog with a dump made by --dump or in Settings, and exit "
                             "(the current database is copied aside first)")
    args = parser.parse_args(argv)

    if args.config is None and ensure_config_file():
        print(f"Created {CONFIG_FILE} with default settings — edit it to change port or data folder.")

    settings = load_settings(
        host=args.host, port=args.port, data_dir=args.data_dir, config_file=args.config
    )
    app = create_app(settings)
    if args.reset_auth:
        conn = connect(settings.db_path)
        try:
            auth.disable(conn)
        finally:
            conn.close()
        print("Sign-in is off: the app opens without a password. Set a new one in Settings.")
        return
    if args.dump is not None:
        target = Path(args.dump or backup.dump_filename())
        if target.is_dir():
            target = target / backup.dump_filename()
        target.write_bytes(backup.make_dump(settings))
        print(f"Saved the catalog to {target.resolve()}")
        return
    if args.restore:
        try:
            result = backup.restore_dump(settings, Path(args.restore).read_bytes())
        except (OSError, backup.BadDump) as exc:
            raise SystemExit(f"Can't restore: {exc}")
        c = result["counts"]
        print(f"Restored: {c['paint']} paints, {c['storage']} storages, {c['tag']} tags.")
        print(f"The previous database is kept as {settings.data_dir / result['previous']}")
        return
    print(f"Paint Accountant: data in {settings.data_dir}")
    print("Open in a browser:")
    for url in _urls(settings.host, settings.port):
        print(f"  {url}")
    photo = recognition.availability()
    if photo["available"]:
        recognition.warm_up()
        print("Photo recognition: on")
    else:
        print(f"Photo recognition: off (to enable: {photo['install']})")
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
