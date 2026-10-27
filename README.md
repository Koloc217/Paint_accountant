# Paint Accountant

A self-hosted catalog for miniature paints: what you own, how many pots, and which box they're in.

Runs on any machine in your home network — Linux (x86_64 or ARM64, including Raspberry Pi) or Windows — with a web UI for your phone and a Telegram bot for adding paints by photo from anywhere.

> **Status:** early development. Catalog, web UI and adding paints by photo work. The Telegram bot comes next. See [plans.md](plans.md) (in Russian) for the spec and roadmap.

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

On start it prints the addresses to open — `http://localhost:8642/` on this computer and `http://<your-LAN-IP>:8642/` from a phone on the same network. The web UI is in Russian and works on phones and desktops, in light and dark mode. The REST API docs are at `/docs`.

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

### Adding paints by photo (optional)

```bash
pip install -r requirements-photo.txt
```

Restart the app — the console says `Photo recognition: on`, and the new-paint form gets **📷 Снять этикетку** (opens the phone camera directly) and **🖼 Галерея** buttons. Take a photo of the label: the app reads the text and the barcode, fills in name, manufacturer, code, barcode and tags, and shows paints already in your catalog that look the same, with a **+1 pot** button. Pieces of the label text can be tapped: for the name they work as toggles and join in tap order (tap again to take a piece out); for code and manufacturer a tap replaces the field.

Everything runs locally on your machine: [RapidOCR](https://github.com/RapidAI/RapidOCR) (ONNX models ship inside the package, nothing is downloaded at runtime) and [zxing-cpp](https://github.com/zxing-cpp/zxing-cpp) for barcodes. Prebuilt packages exist for Windows, Linux x86_64 and ARM64 (Raspberry Pi), Python 3.10–3.14. The text model reads Latin letters and digits; Cyrillic isn't recognized.

Tips: shoot the label close up, straight on, in good light, without glare. Once a paint is saved with its barcode, the next pot of it is found instantly.

**Camera modes on a phone** (chosen per device, remembered by the browser):

- **Фото** — the button opens the phone's camera app; works over plain `http`.
- **Поток + фото** — live video right in the page: point at a barcode and the paint is found in a moment (with **+1 pot** right there, scanning continues for the next pot); for a new barcode press **Снимок этикетки** to read the label. Browsers allow live video only over HTTPS — on Android Chrome you can allow it for the app's address with the flag `chrome://flags/#unsafely-treat-insecure-origin-as-secure` (the app shows the steps). Barcodes are read by Chrome's built-in detector when available, otherwise frames go to the server.

On a computer there is just an upload button.

### Running the tests

```bash
pip install -r requirements-dev.txt   # includes the photo packages
pytest
```

## Web UI

- **Краски** — list with color swatches, search, filters by storage, manufacturer, tags, "at hand" and stock; quick +/− for the number of pots; add, edit, delete; manufacturer picked from a list, a new one can be added right in the form
- **Хранилища** — boxes and drawers with paint and pot counts; each storage has a short link `http://<address>:8642/storage/<id>` to write into an NFC tag or a QR code
- **Where pots lie** — a paint can be in several storages at once ("at hand 1, Запас 2"), and any storage can hold several pots of a paint. When adding you pick a storage (the **default** one is preselected; on a new database it's «Под рукой»). Adding a second pot of a paint to an **"at hand"** storage asks first: put it there anyway, or into another storage (each storage can name one to suggest). Adding a paint that already lies elsewhere warns about it; when "at hand" runs out, the app offers to take a pot from where it's left. The paint list shows the total over all storages
- **Справочники** — manufacturers (rename, add, delete unused ones) and tags (rename, recolor, add, delete). Starter manufacturers: AK, Citadel, Green Stuff World, Kaleido, Kimera, Tamiya, Vallejo

No build step and no external libraries — plain HTML, CSS and JavaScript in `paint_accountant/web/`, so it works on a home network without internet.

## API

| Method | Path | What it does |
|---|---|---|
| GET | `/api/paints` | list paints; filters: `q`, `barcode`, `manufacturer_id` or `manufacturer` (name), `storage_id`, `no_storage`, `tag_id` (repeatable), `at_hand`, `in_stock` |
| POST | `/api/paints` | add a paint; give `manufacturer_id`, or `manufacturer` by name (a new name is added to the list) |
| GET / PATCH / DELETE | `/api/paints/{id}` | read, change (name, manufacturer, code, barcode, color, tags), delete a paint |
| POST | `/api/paints/{id}/add` | add pots: `{"count": 1}` to the default storage, `{"storage_id": N}` there; a second pot in an "at hand" storage needs `"allow_duplicate": true`, else 409 `duplicate_at_hand` with a suggested storage |
| POST | `/api/paints/{id}/remove` | a pot is used up: from "at hand" first, or from `storage_id` |
| POST | `/api/paints/{id}/move` | `{"from_storage_id", "to_storage_id", "count"}` |
| GET / POST | `/api/manufacturers` | list (with paint counts, `q` to filter) / add manufacturers |
| PATCH / DELETE | `/api/manufacturers/{id}` | rename; delete only works when no paints use it |
| GET / POST | `/api/storages` | list (with paint and pot counts) / add storages |
| GET / PATCH / DELETE | `/api/storages/{id}` | settings include `is_default` and `copies_storage_id` (storage suggested for duplicates); deleting keeps the pots, without a storage (the default one can't be deleted) |
| GET / POST | `/api/tags` | list / add tags |
| PATCH / DELETE | `/api/tags/{id}` | |
| GET | `/api/recognize` | whether photo recognition is installed |
| POST | `/api/recognize` | send a photo as the body (`Content-Type: image/jpeg`); returns label text, barcodes, form suggestions and matching paints; `?barcode_only=true` skips text (for live camera frames) |
| GET | `/api/health` | status and schema version |

Full details at `/docs` on a running server.

## Idea

- **Catalog your paints** — name, manufacturer, product code, hex color, number of pots, tags.
- **Storage boxes with NFC tags** — tap your phone on a box to open the list of paints inside it. Tags are optional: a box can just be a named shelf.
- **"At hand" storage** — mark a drawer or organizer on your desk as within reach, and filter by what you can grab right now.
- **Add paints by photo, not by typing** — barcode scan first, then local OCR of the label with fuzzy matching against paints you already have. Everything runs locally, no cloud AI APIs.
- **Brand-agnostic** — no built-in brand list is required. The catalog grows from what you add yourself; external paint datasets can be imported as optional hints.
- **Tags** — primer, base, layer, contrast, shade/wash, effects, metallic, ink, varnish, airbrush, technical (thinners, mediums, retarders); editable and extendable.

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
