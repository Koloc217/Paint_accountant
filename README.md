# Paint Accountant

A self-hosted catalog for miniature paints: what you own, how many pots, and which box they're in.

Runs on any machine in your home network — Linux (x86_64 or ARM64, including Raspberry Pi) or Windows — with a web UI for your phone and a Telegram bot for adding paints by photo from anywhere.

> **Status:** in use, still growing. Catalog, web UI, QR labels for storages, adding paints by photo, export and backup work. Not done yet: the Telegram bot, import of paint datasets, HTTPS. See [plans.md](plans.md) (in Russian) for the spec and roadmap.

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

On start it prints the addresses to open — `http://localhost:8642/` on this computer and `http://<your-LAN-IP>:8642/` from a phone on the same network. The web UI is in Russian and English (the browser's language by default; set it for every device in ⚙ Settings or in `config.ini`) and works on phones and desktops, in light and dark mode. The REST API docs are at `/docs`.

On Windows, allow Python through the firewall for **private networks** when asked, otherwise the phone won't reach the server. If `python` opens the Microsoft Store, use `py` instead, or turn off the `python.exe` alias in Settings → Apps → Advanced app settings → App execution aliases.

### Folder layout

```
Paint_accountant/
├── run.py               start here
├── config.ini           your settings (created from config.example.ini on first start)
├── paint_accountant/    the code
└── data/                created on first start
    └── paints.sqlite3   the database
```

### Exporting the list (for a chatbot or for reading)

**Export** above the paint list copies or downloads the listed paints as Markdown, following the current filters (all metallics, one box…):

- **Short — for a chatbot**: paints in stock by manufacturer, one line each — name (code) — hex — tags — just the list, no header. Paste it into a chatbot and ask whether something you have can replace a paint, or you need to buy it.
- **Full**: adds a summary of storages, where each paint lies (storage ×pots, ✋ = at hand), barcodes, and the paints that ran out.

API: `GET /api/export/paints.md?level=short|full&lang=ru|en&download=true`, with the same filters as `/api/paints`.

### Backup and moving to another machine

**⚙ Settings → Backup** has three buttons:

- **⬇ Paint list (CSV)** — every paint sorted by manufacturer and name, with the color (hex) when set, pots, where they are, tags and barcode. UTF-8 with BOM; `;` as separator in Russian, `,` in English, so Excel opens it correctly in either locale. For reading only — it can't be imported back.
- **⬇ Full backup (.zip)** — the whole database (paints, storages, tags, manufacturers, settings including the sign-in password), consistent even while the app is running.
- **Restore from a backup…** — replaces the catalog with a `.zip` made here or on another machine. The current database is kept as `data/paints.before-restore-<date>.sqlite3`; a dump from an older app version is upgraded on the way in, one from a newer version is refused.

The same from the console: `python run.py --dump [file.zip]` and `python run.py --restore file.zip`. Copying the `data/` folder while the app is stopped works too.

Autostart (systemd on Linux, Task Scheduler on Windows) and moving to another computer, step by step: [docs/autostart.md](docs/autostart.md). To move the whole app, copy the folder (and recreate `.venv` on the new machine).

To change a storage's number in its link (`/#/storage/2` → `/#/storage/1`, e.g. to match a label you already printed), stop the app and run `python tools/storage_id.py` to list storages, then `python tools/storage_id.py 2 1`. If the number is taken, the two storages swap; pots and settings follow. A copy of the database is saved first.

### Settings

On first start `config.ini` is created from `config.example.ini`; edit it to change settings. It is personal and not tracked by git. Command-line arguments and environment variables override the file.

| `config.ini` key | Argument | Environment variable | Default |
|---|---|---|---|
| `host` | `--host` | `PAINT_HOST` | `0.0.0.0` (reachable from other devices on your network) |
| `port` | `--port` | `PAINT_PORT` | `8642`; a value that isn't a port number stops the start with a message saying where it came from |
| `language` (section `[ui]`) | — | `PAINT_LANGUAGE` | `auto` — the browser's language; `ru` or `en` for every device. ⚙ Settings writes it here |
| `data_dir` | `--data-dir` | `PAINT_DATA_DIR` | `data` inside the app folder; relative paths are counted from the app folder, absolute ones (`/mnt/usb/paints`, `D:\Paints`) work too |
| `upcitemdb` (section `[lookup]`) | — | `PAINT_LOOKUP` | `on` — the 🔎 button next to a barcode; `off` hides it. ⚙ Settings writes it here |
| `proxy` (section `[lookup]`) | — | `PAINT_LOOKUP_PROXY` | empty — the system's `HTTPS_PROXY` / `ALL_PROXY` if set, otherwise direct; `socks5://host:port`, `socks5h://…` (the proxy resolves the name too) or `http://host:port`, with `user:password@` if needed |
| `interface` (section `[lookup]`) | — | `PAINT_LOOKUP_INTERFACE` | empty; a network interface to send lookups through, e.g. a VPN's `awg0` (Linux), or that interface's IP address (any OS). Ignored when `proxy` is set |

### Looking a barcode up online (optional)

The **🔎** button next to the barcode field (and **🔎 Find at UPCitemdb** after a new barcode is scanned) asks [UPCitemdb](https://www.upcitemdb.com/) what the product is and fills in the empty fields: the name (cleaned from the shop title — brand, volume, shop articles and words like "Acrylic Paint … Modelling Hobby" are dropped), manufacturer, code and tags. One barcode usually comes back under several titles, the shops' own among them, and the one that actually names a colour is used; a listing for a whole range ("Citadel Paint Pot - Base, Layer, Contrast & Shade") fills in nothing but the manufacturer, and says so. A brand that isn't in your list is offered as a new manufacturer. Always check the result: shop titles can be inexact, and many hobby paints aren't in the database at all.

Only the barcode is sent, only when you press the button, and from the computer running the app — not from the phone. The free API needs no key: 100 lookups a day and 6 a minute per IP address; a repeated lookup of the same barcode within a day is answered from memory. Turn it off in ⚙ Settings or with `upcitemdb = off`. If the site isn't reachable from your network, set a proxy or a VPN interface in `[lookup]` (see the table above); nothing has to be installed for that.

### Adding paints by photo (optional)

```bash
pip install -r requirements-photo.txt
```

Restart the app — the console says `Photo recognition: on`, and the new-paint form gets **📷 Shoot** (opens the phone camera directly) and **🖼 Label photo** buttons. Take a photo of the label: the app reads the text and the barcode, fills in name, manufacturer, code, barcode and tags, and shows paints already in your catalog that look the same, with a **+1 pot** button. Pieces of the label text can be tapped: for the name they work as toggles and join in tap order (tap again to take a piece out); for code and manufacturer a tap replaces the field. Text found far from the label (keyboard keys, a cutting mat, another pot) is folded under **N more — looks like background**. In the live camera mode, tapping the picture switches the frame to a tall label frame (it switches by itself after a new barcode), and the label snapshot is then cut to that frame, so the background isn't read at all.

Everything runs locally on your machine: [RapidOCR](https://github.com/RapidAI/RapidOCR) (ONNX models ship inside the package, nothing is downloaded at runtime) and [zxing-cpp](https://github.com/zxing-cpp/zxing-cpp) for barcodes. Prebuilt packages exist for Windows, Linux x86_64 and ARM64 (Raspberry Pi), Python 3.10–3.14. The text model reads Latin letters and digits; Cyrillic isn't recognized.

Tips: shoot the label close up, straight on, in good light, without glare. Once a paint is saved with its barcode, the next pot of it is found instantly.

**Camera modes on a phone** (chosen per device, remembered by the browser):

- **Photo** — the button opens the phone's camera app; works over plain `http`.
- **Live + photo** — live video right in the page: point at a barcode and the paint is found in a moment (with **+1 pot** right there, scanning continues for the next pot); for a new barcode press **📸 Snap the label** to read the label. Browsers allow live video only over HTTPS — on Android Chrome you can allow it for the app's address with the flag `chrome://flags/#unsafely-treat-insecure-origin-as-secure` (the app shows the steps). Barcodes are read by Chrome's built-in detector when available, otherwise frames go to the server.

On a computer there is just an upload button.

### Sign-in (optional)

Off by default: anyone on your home network can open the app. To ask for a password, open **⚙ Settings → Password sign-in**, set a password for the user `admin` and turn it on. The device you did it from stays signed in; other phones and computers sign in once and stay signed in for a year. Changing the password asks for the current one and signs everyone else out. Without signing in, the API and `/docs` answer 401; links from QR labels lead to the login screen and back to the storage.

The password is stored as a salted hash; after 5 wrong tries in a row an address waits a minute. Over plain `http` it travels unencrypted, so don't reuse an important password.

Forgot it? Stop the app and run `python run.py --reset-auth` — sign-in turns off, set a new password in Settings.

### Running the tests

```bash
pip install -r requirements-dev.txt   # includes the photo packages
pytest
```

## Web UI

- **Paints** — list with color swatches, search (every word you type is looked for in the name, manufacturer, code, barcode and tags: `vallejo black`, `70950`), filters by storage, manufacturer, tags, "at hand" and stock, plus **🏷 No tags** for what hasn't been sorted into tags yet; quick +/− for the number of pots; add, edit, delete; manufacturer picked from a list, a new one can be added right in the form
- **List / Table** — on a wide (PC) screen the paint list can be switched to a table: name, manufacturer, code, where the pots lie, tags and the number of pots; click the Name, Manufacturer or Pots header to sort. The choice is remembered on this device; on a phone the list is always used. In the table the number of pots is read-only — change it on the paint's page
- **Storages** — boxes and drawers with paint and pot counts and a search by name; a storage page has its own search and filters (manufacturer, tags), +/− work in that storage; each storage has a short link `http://<address>:8642/storage/<id>` and its QR code; **🖨 QR labels** makes a printable sheet of labels (pick storages, 3 / 4 / 5.5 cm wide, cut lines) or saves it as a PNG at 300 dpi, so it prints at the chosen size; ✎ next to a storage's name renames it — the phone's camera opens the storage from the label. The address is the computer's LAN IP, so reserve it in your router (DHCP reservation) before printing
- **Where pots lie** — a paint can be in several storages at once ("At hand 1, Box 2"), and any storage can hold several pots of a paint. When adding you pick a storage (the **default** one is preselected; on a new database it's “At hand”). Adding a second pot of a paint to an **"at hand"** storage asks first: put it there anyway, or into another storage (each storage can name one to suggest). Adding a paint that already lies elsewhere warns about it; when "at hand" runs out, the app offers to take a pot from where it's left. The paint list shows the total over all storages
- **Catalogs** — two tabs, manufacturers and tags. Tap a row to see its paints; ✎ renames (Enter saves, Esc cancels); 🗑 deletes (a manufacturer only when it has no paints). Tags show their color (tap to change), paint count and label words. Counters count different paints: the same paint entered twice (same manufacturer and name, e.g. once with a code and once without) counts once; the same name with different codes counts as different paints. Starter manufacturers: AK, Citadel, Green Stuff World, Kaleido, Kimera, Mr. Hobby, Scale75, Tamiya, Vallejo. Starter tags carry the words that suggest them on a label or in a shop's title — including **Alcohol-based** for what is thinned with alcohol or a lacquer thinner ("alcohol", "lacquer"). A database made before a tag or a manufacturer became starter data is given it on the next start, unless one of that name is already there
- **Statistics** (📊) — totals; which storages are the busiest (bars, share of all pots, empty ones); paints with several pots and those lying in several storages; **possible duplicate records** (same manufacturer and name, different code or barcode — open one and press **Merge**); breakdown by manufacturer and tag; how many paints have no tag, color or barcode. The same numbers are at `GET /api/stats`

No build step and no external libraries — plain HTML, CSS and JavaScript in `paint_accountant/web/`, so it works on a home network without internet. The scripts are ES modules: `app.js` starts the app, `i18n.js` holds the texts, and each screen has its own file in `web/js/` (the list of them is at the top of `app.js`).

## API

| Method | Path | What it does |
|---|---|---|
| GET | `/api/paints` | list paints; filters: `q` (words; each must be in the name, manufacturer, code, barcode or a tag), `barcode`, `uuid` (repeatable), `manufacturer_id` or `manufacturer` (name), `storage_id`, `no_storage`, `tag_id` (repeatable), `no_tags` (only paints with no tag at all), `at_hand`, `in_stock` |
| POST | `/api/paints` | add a paint; give `manufacturer_id`, or `manufacturer` by name (a new name is added to the list) |
| GET / PATCH / DELETE | `/api/paints/{id}` | read, change (name, manufacturer, code, barcode, color, tags), delete a paint |
| GET | `/api/paints/by-uuid/{uuid}` | read a paint by its permanent `uuid` |
| POST | `/api/paints/{id}/add` | add pots: `{"count": 1}` to the default storage, `{"storage_id": N}` there; a second pot in an "at hand" storage needs `"allow_duplicate": true`, else 409 `duplicate_at_hand` with a suggested storage |
| POST | `/api/paints/{id}/remove` | a pot is used up: from "at hand" first, or from `storage_id` |
| POST | `/api/paints/{id}/move` | `{"from_storage_id", "to_storage_id", "count"}` |
| POST | `/api/paints/bulk` | several paints at once: `{"paint_ids": […], "action": "move" \| "add_tags" \| "remove_tags", …}`, all or nothing |
| POST | `/api/paints/{id}/merge` | fold a duplicate into this paint: `{"from_id": N, "barcode": "keep"\|"from"}` — its pots and tags move here, the duplicate is deleted (with its uuid) |
| GET / POST | `/api/manufacturers` | list (with paint counts, `q` to filter) / add manufacturers |
| PATCH / DELETE | `/api/manufacturers/{id}` | rename; delete only works when no paints use it |
| GET / POST | `/api/storages` | list (with paint and pot counts) / add storages; names per language work as for tags (below) |
| GET / PATCH / DELETE | `/api/storages/{id}` | settings include `is_default` and `copies_storage_id` (storage suggested for duplicates); deleting keeps the pots, without a storage (the default one can't be deleted); `{"name": …, "lang": "en"}` renames in one language |
| GET / POST | `/api/tags` | list / add tags; a tag has a name per interface language in `names` (`name` is the Russian one); a new tag gets the same name in every language unless `names` says otherwise |
| PATCH / DELETE | `/api/tags/{id}` | `{"name": …, "lang": "en"}` renames in one language, `name` alone — in all |
| GET | `/api/recognize` | whether photo recognition is installed |
| POST | `/api/recognize` | send a photo as the body (`Content-Type: image/jpeg`); returns label text, barcodes, form suggestions and matching paints; `?barcode_only=true` skips text (for live camera frames) |
| PUT | `/api/settings/language` | `{"language": "auto" \| "ru" \| "en"}` — saved into `config.ini` |
| PUT | `/api/settings/lookup` | `{"enabled": true \| false}` — online barcode lookup on or off, saved into `config.ini` |
| GET | `/api/lookup/upcitemdb?barcode=…` | ask UPCitemdb about a barcode: `found`, the raw `item` (title, brand…) and a `suggestion` for the form (name, manufacturer or `new_manufacturer`, code, tag ids), `remaining` requests today; errors carry `detail.code`: `lookup_disabled` (409), `lookup_limit` / `lookup_too_fast` (429), `lookup_unreachable` (502) |
| GET | `/api/qr.svg?data=…` | QR code as SVG (for storage labels) |
| GET | `/api/auth` | whether sign-in is on and this device is signed in |
| POST | `/api/auth/login` · `/api/auth/logout` | `{"password": "…"}`; sets / clears the session cookie |
| POST | `/api/auth/password` | turn sign-in on (`{"password": "…"}`, at least 4 characters) or change the password (also `"current": "…"`) |
| POST | `/api/auth/disable` | turn sign-in off |
| GET | `/api/stats` | the numbers of the Statistics page |
| GET | `/api/export/paints.md` | the list as Markdown (see Exporting the list) |
| GET | `/api/backup/paints.csv` · `/api/backup/dump` | the CSV list / the full backup |
| POST | `/api/backup/restore` | replace the catalog with a backup sent as the body |
| GET | `/api/info` | app version, this machine's address in the network, interface language |
| GET | `/api/health` | status and schema version |

Every paint has two ids. `id` is a short number for this app's pages and API calls. `uuid` is given once when the paint is added and never changes, even when the database is rebuilt or merged: other apps (a project tracker, a script) should keep the `uuid` and look paints up by it.

When a new version changes the database schema, the old file is copied first, next to it: `paints.before-v15.sqlite3` is the database as it was at schema 15. Delete these copies once the new version works for you. A database or a backup from before schema 15 can't be opened directly: start the app once as it was at commit `0ee8e28` (`git checkout 0ee8e28`) to bring it to schema 15, then update again.

Full details at `/docs` on a running server.

## Idea

- **Catalog your paints** — name, manufacturer, product code, hex color, number of pots, tags.
- **Storage boxes with QR labels** — print a label, stick it on a box, point the phone's camera at it to open the list of paints inside. The same link can be written into an NFC tag. Labels are optional: a box can just be a named shelf.
- **"At hand" storage** — mark a drawer or organizer on your desk as within reach, and filter by what you can grab right now.
- **Add paints by photo, not by typing** — barcode scan first, then local OCR of the label with fuzzy matching against paints you already have. Everything runs locally, no cloud AI APIs.
- **Brand-agnostic** — no built-in brand list is required. The catalog grows from what you add yourself; external paint datasets can be imported as optional hints.
- **Tags** — primer, base, layer, contrast, shade/wash, effects, metallic, ink, varnish, airbrush, technical (thinners, mediums, retarders, cleaners, putty); editable and extendable.

## Stack

| Part | Choice |
|---|---|
| Server | Python, FastAPI, SQLite (3.35 or newer) |
| Barcode | zxing-cpp |
| OCR | RapidOCR (ONNX, runs locally) |
| Fuzzy search | rapidfuzz |
| QR labels | segno |
| Web UI | plain HTML, CSS and JavaScript; plain HTTP on the local network, no certificates needed |
| Bot (planned) | Telegram, optionally through a SOCKS5/MTProto proxy |
| Platforms | Linux (x86_64, ARM64) and Windows |

Development and testing happen on a Raspberry Pi 5 (8 GB), but the Pi is not a requirement.

## Portability goals

- Only dependencies with prebuilt wheels for Windows, Linux x86_64 and Linux ARM64
- No hardcoded paths: the database goes to a configurable folder, by default `data/` inside the app folder
- One command to run on any OS; autostart recipes (systemd, Windows Task Scheduler) are optional extras — see [docs/autostart.md](docs/autostart.md)
- Photo recognition is an optional module — the catalog works without it

## Roadmap

1. ✔ Skeleton and manual entry — data model, list/card views, storages, tags, search and filters
2. ✔ Storages and QR labels — a page per storage, printable labels with its link
3. ✔ Photo recognition in the web UI — barcode → OCR → confirm
4. Telegram bot — send photos or albums, confirm with inline buttons, pick a storage
5. Import of external paint datasets (name, manufacturer, code, hex)

Later ideas (return-to-box, cross-brand substitutes by color distance, and more) are listed at the end of [plans.md](plans.md).

## Acknowledgements

Third-party code or data used in this project will be credited here, with its original license kept in the repository.

## License

Paint Accountant is free software, licensed under the [GNU General Public License v3.0](LICENSE).
