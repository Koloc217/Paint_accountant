"""Command line: `python run.py` from the app folder (or `python -m paint_accountant`)."""

from __future__ import annotations

import argparse
import socket

import uvicorn

from .app import create_app
from .config import (
    CONFIG_FILE,
    DEFAULT_DATA_DIR,
    DEFAULT_HOST,
    DEFAULT_PORT,
    ensure_config_file,
    load_settings,
)


def _lan_ip() -> str | None:
    """This machine's address in the local network.

    Connecting a UDP socket sends no packets; it only makes the OS pick
    the outgoing interface, whose address we then read.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
        return None if ip.startswith("127.") else ip
    except OSError:
        return None


def _urls(host: str, port: int) -> list[str]:
    if host in ("0.0.0.0", "", "::"):
        urls = [f"http://localhost:{port}/  (this computer)"]
        ip = _lan_ip()
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
    args = parser.parse_args(argv)

    if args.config is None and ensure_config_file():
        print(f"Created {CONFIG_FILE} with default settings — edit it to change port or data folder.")

    settings = load_settings(
        host=args.host, port=args.port, data_dir=args.data_dir, config_file=args.config
    )
    app = create_app(settings)
    print(f"Paint Accountant: data in {settings.data_dir}")
    print("Open in a browser:")
    for url in _urls(settings.host, settings.port):
        print(f"  {url}")
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
