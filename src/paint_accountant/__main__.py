"""Entry point: `python -m paint_accountant` or `paint-accountant`."""

from __future__ import annotations

import argparse

import uvicorn

from .app import create_app
from .config import DEFAULT_HOST, DEFAULT_PORT, load_settings


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="paint-accountant",
        description="Self-hosted catalog for miniature paints.",
    )
    parser.add_argument(
        "--host", help=f"address to listen on (default {DEFAULT_HOST}, env PAINT_HOST)"
    )
    parser.add_argument(
        "--port", type=int, help=f"port to listen on (default {DEFAULT_PORT}, env PAINT_PORT)"
    )
    parser.add_argument(
        "--data-dir",
        help="folder for the database and photos (default: OS user data folder, env PAINT_DATA_DIR)",
    )
    args = parser.parse_args(argv)

    settings = load_settings(host=args.host, port=args.port, data_dir=args.data_dir)
    app = create_app(settings)
    print(f"Paint Accountant: data in {settings.data_dir}")
    print(f"Open http://<this-machine>:{settings.port}/ (API docs at /docs)")
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
