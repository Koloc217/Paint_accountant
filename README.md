# Paint Accountant

A self-hosted catalog for miniature paints: what you own, how many pots, and which box they're in.

Runs on any machine in your home network — Linux (x86_64 or ARM64, including Raspberry Pi) or Windows — with a web UI for your phone and a Telegram bot for adding paints by photo from anywhere.

> **Status:** early development. The database and REST API work; the web UI is next. See [plans.md](plans.md) (in Russian) for the spec and roadmap.

## Quick start

Requires Python 3.10+. The same steps work on Linux, Raspberry Pi and Windows.
Nothing is installed system-wide: the app, its settings and its data all live in one folder.

```bash
git clone https://github.com/Koloc217/Paint_accountant.git
cd Paint_accountant
python -m venv .venv
# Linux / macOS:  source .venv/bin/activate
# Windows:        .venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

On start it prints the addresses to open — `http://localhost:8642/` on this computer and `http://<your-LAN-IP>:8642/` from a phone on the same network. For now they lead to the interactive API docs.

On Windows, allow Python through the firewall for **private networks** when asked, otherwise the phone won't reach the server. If `python` opens the Microsoft Store, use `py` instead, or turn off the `python.exe` alias in Settings → Apps → Advanced app settings → App execution aliases.

### Folder layout

```
Paint_accountant/
├── run.py               start here
├── config.ini           your settings (created from config.example.ini on first start)
├── paint_accountant/    the code
└── data/                created on first start
    ├── paints.sqlite3   the database
    └── photos/          uploaded pictures
```

Back up or move your catalog by copying the `data/` folder. To move the whole app, copy the folder (and recreate `.venv` on the new machine).

### Settings

On first start `config.ini` is created from `config.example.ini`; edit it to change settings. It is personal and not tracked by git. Command-line arguments and environment variables override the file.

| `config.ini` key | Argument | Environment variable | Default |
|---|---|---|---|
| `host` | `--host` | `PAINT_HOST` | `0.0.0.0` (reachable from other devices on your network) |
| `port` | `--port` | `PAINT_PORT` | `8642` |
| `data_dir` | `--data-dir` | `PAINT_DATA_DIR` | `data` inside the app folder; relative paths are counted from the app folder, absolute ones (`/mnt/usb/paints`, `D:\Paints`) work too |

### Running the tests

```bash
pip install -r requirements-dev.txt
pytest
```

## API

| Method | Path | What it does |
|---|---|---|
| GET | `/api/paints` | list paints; filters: `q`, `manufacturer`, `storage_id`, `no_storage`, `tag_id` (repeatable), `at_hand`, `in_stock` |
| POST | `/api/paints` | add a paint |
| GET / PATCH / DELETE | `/api/paints/{id}` | read, change, delete a paint |
| GET | `/api/manufacturers` | manufacturer names for autocomplete, `q` to filter |
| GET / POST | `/api/storages` | list (with paint and pot counts) / add storages |
| GET / PATCH / DELETE | `/api/storages/{id}` | deleting a storage keeps its paints, without a storage |
| GET / POST | `/api/tags` | list / add tags |
| PATCH / DELETE | `/api/tags/{id}` | |
| GET | `/api/health` | status and schema version |

Full details at `/docs` on a running server.

## Idea

- **Catalog your paints** — name, manufacturer, product code, hex color, number of pots, tags.
- **Storage boxes with NFC tags** — tap your phone on a box to open the list of paints inside it. Tags are optional: a box can just be a named shelf.
- **"At hand" storage** — mark a drawer or organizer on your desk as within reach, and filter by what you can grab right now.
- **Add paints by photo, not by typing** — barcode scan first, then local OCR of the label with fuzzy matching against paints you already have. Everything runs locally, no cloud AI APIs.
- **Brand-agnostic** — no built-in brand list is required. The catalog grows from what you add yourself; external paint datasets can be imported as optional hints.
- **Tags** — primer, base, layer, contrast, shade/wash, technical, metallic, ink, varnish, airbrush; editable and extendable.

## Planned stack

| Part | Choice |
|---|---|
| Server | Python, FastAPI, SQLite |
| Barcode | zxing-cpp |
| OCR | RapidOCR (ONNX, runs locally) |
| Fuzzy search | rapidfuzz |
| Web UI | plain HTTP on the local network, no certificates needed |
| Bot | Telegram, optionally through a SOCKS5/MTProto proxy |
| Platforms | Linux (x86_64, ARM64) and Windows |

Development and testing happen on a Raspberry Pi 5 (8 GB), but the Pi is not a requirement.

## Portability goals

- Only dependencies with prebuilt wheels for Windows, Linux x86_64 and Linux ARM64
- No hardcoded paths: data, database and photos go to a configurable folder, defaulting to the OS's standard per-user data directory
- One command to run on any OS; autostart recipes (systemd, Windows Task Scheduler) are optional extras
- Photo recognition is an optional module — the catalog works without it

## Roadmap

1. Skeleton and manual entry — data model, list/card views, storages, tags, search and filters
2. Storages and NFC/QR tags — a page per storage, links to write into tags
3. Photo recognition in the web UI — barcode → OCR → confirm
4. Telegram bot — send photos or albums, confirm with inline buttons, pick a storage
5. Import of external paint datasets (name, manufacturer, code, hex)

Later ideas (return-to-box, cross-brand substitutes by color distance, shopping list, and more) are listed at the end of [plans.md](plans.md).

## Acknowledgements

Third-party code or data used in this project will be credited here, with its original license kept in the repository.

## License

Paint Accountant is free software, licensed under the [GNU General Public License v3.0](LICENSE).
