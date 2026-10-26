# Paint Accountant

A self-hosted catalog for miniature paints: what you own, how many pots, and which box they're in.

Built to run on a Raspberry Pi in your home network, with a web UI for your phone and a Telegram bot for adding paints by photo from anywhere.

> **Status:** planning. Nothing is implemented yet — see [plans.md](plans.md) (in Russian) for the spec and roadmap.

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
| Hardware | Raspberry Pi 5 (8 GB) |

## Roadmap

1. Skeleton and manual entry — data model, list/card views, storages, tags, search and filters
2. Storages and NFC/QR tags — a page per storage, links to write into tags
3. Photo recognition in the web UI — barcode → OCR → confirm
4. Telegram bot — send photos or albums, confirm with inline buttons, pick a storage
5. Import of external paint datasets (name, manufacturer, code, hex)

Later ideas (return-to-box, cross-brand substitutes by color distance, shopping list, and more) are listed at the end of [plans.md](plans.md).
