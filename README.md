# 📚 ABS Tidy Library

A web UI for automatically organising your [Audiobookshelf](https://www.audiobookshelf.org/) library. Reads your metadata directly from ABS, renames audio files consistently, and reorganises your folders into a clean hierarchy — all with a live preview before anything is touched.

[![Docker Pulls](https://img.shields.io/docker/pulls/donaldwin/abs-tidy-library)](https://hub.docker.com/r/donaldwin/abs-tidy-library)
[![Build Status](https://github.com/Donald-Win/abs-tidy-library/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/Donald-Win/abs-tidy-library/actions)

---

## ✨ What It Does

- **Scans** your ABS library and shows statistics — books, authors, series, total play-time and size
- **Proposes changes** as diff cards showing exactly what folder and file names will change
- **Metadata preflight** — flags books with missing author, title, or series number before you apply
- **Inline metadata editing** — fix title, author, or series directly on each card; changes are pushed to ABS before files move
- **Dry Run** mode simulates every move with zero changes made to disk
- **Selective apply** — tick the books you want moved instead of doing them all at once
- **Per-item ABS scan** after each move — preserves listen progress and avoids duplicate entries
- **Rollback** — one click to undo the last apply run, even after a page reload
- **Collision detection** — warns you before applying if two books would resolve to the same path
- **Filesystem check** — warns if a move would cross filesystem boundaries and risk losing progress
- **Cleans empty directories** left over after moves
- **Persistent settings** — naming templates survive container restarts

---

## 🚀 Quick Start

### 1. Get the compose file

```bash
curl -O https://raw.githubusercontent.com/Donald-Win/abs-tidy-library/main/compose.yaml
```

### 2. Edit the required values

```yaml
volumes:
  - /your/audiobooks:/library   # same host path as your ABS compose (see note below)
  - /any/config/folder:/config  # any persistent folder for settings

environment:
  ABS_SERVER_URL:  "http://192.168.1.100:13378"  # LAN IP — used for API calls
  ABS_EXTERNAL_URL: "http://192.168.1.100:13378" # browser-facing URL — used for deep links
  ABS_TOKEN:       "your-api-key-here"           # from ABS → Settings → API Keys
```

### 3. Start

```bash
docker compose up -d
```

Open **http://your-server:5050** — if your env vars are set it will connect automatically. Hit **Scan Library**.

---

## ⚠️ Common Gotchas

**Volume path must match your ABS setup**
The left side of the audiobooks volume mount must point to the same host directory that ABS is already serving. The right side (container path) can be anything, but must match `LIBRARY_PATH`.

```yaml
# Your ABS compose might have:
- /media/Audiobooks:/audiobooks

# So this tool needs the same left side:
- /media/Audiobooks:/library
```

**Use your LAN IP, not localhost**
Even if ABS is running on the same machine, containers can't reach each other via `localhost` or `127.0.0.1`. Use the actual LAN IP of your server (e.g. `192.168.1.100`).

**Running behind a reverse proxy (Caddy, Nginx, etc.)**
If ABS is accessed via a hostname like `http://abs.internal`, set `ABS_SERVER_URL` to the internal container address for API calls (e.g. `http://audiobookshelf:80`) and `ABS_EXTERNAL_URL` to the browser-facing address (e.g. `http://abs.internal`). Without this, deep links to books will fail with a DNS error.

```yaml
ABS_SERVER_URL:   "http://audiobookshelf:80"  # internal — for API calls
ABS_EXTERNAL_URL: "http://abs.internal"       # external — for browser links
```

**The API key needs to be from an admin account**
The tool reads library metadata, updates book metadata, and triggers rescans. A non-admin token won't have access to do this.

**`LIBRARY_PATH` must match your volume mount**
If you mount as `:/library`, set `LIBRARY_PATH: "/library"`. They must agree.

**The config mount must be a folder, not a file**
Docker will create it automatically if it doesn't exist yet, as long as the path doesn't conflict with an existing file.

---

## ⚙️ Configuration Reference

| Variable | Required | Description |
|---|---|---|
| `ABS_SERVER_URL` | Yes | Internal ABS address used for API calls. Use LAN IP or container name — not localhost |
| `ABS_EXTERNAL_URL` | Recommended | Browser-facing ABS address used for deep links. Set this if you use a reverse proxy or custom domain. Falls back to `ABS_SERVER_URL` if not set |
| `ABS_TOKEN` | Yes | Admin API key from ABS → Settings → API Keys |
| `ABS_LIBRARY_ID` | Optional | Auto-selects a library on load. Find it in ABS → Settings → Libraries → click your library → copy the ID from the URL |
| `LIBRARY_PATH` | Optional | Path inside the container where books are mounted. Must match your volume mount. Default: `/library` |
| `CONFIG_PATH` | Optional | Where naming config is saved. Default: `/config/naming.json` |

---

## 🗂️ Naming Templates

Templates are configured in the **Settings** drawer and save automatically. Available tokens:

| Token | Example |
|---|---|
| `{Author}` | `Brandon Sanderson` |
| `{Title}` | `The Final Empire` |
| `{Series}` | `Mistborn` |
| `{Series-Index}` | `01` |
| `{Narrator}` | `Michael Kramer` |
| `{Year}` | `2006` |
| `{Part-Index}` | `02` |
| `{Part-Total}` | `12` |
| `{Subtitle}` `{Publisher}` `{Genre}` `{Language}` `{ISBN}` `{ASIN}` | if set in ABS |

Default output looks like:

```
Brandon Sanderson/
├── The Way of Kings/
│   └── Brandon Sanderson - The Way of Kings.m4b
└── Mistborn/
    ├── 01 The Final Empire/
    │   └── Brandon Sanderson - Mistborn 01 - The Final Empire.m4b
    └── 02 The Well of Ascension/
        ├── Brandon Sanderson - Mistborn 02 - The Well of Ascension (Part 01 of 08).m4b
        └── Brandon Sanderson - Mistborn 02 - The Well of Ascension (Part 02 of 08).m4b
```

---

## 🔒 Recommended Workflow

Nothing is moved until you explicitly click Apply.

1. **Scan** — reads metadata from ABS, builds the change plan
2. **Review metadata issues** — fix any flagged books in ABS first, then re-scan
3. **Edit inline** — correct any remaining metadata directly on the diff cards
4. **Dry Run** — simulates all moves and logs them, nothing touches disk
5. **Apply** (or **Apply Selected** for a subset)
6. **Rollback** if anything looks wrong — reverts all files from the last apply

---

## 🔧 Permissions

If you hit permission errors, add a `user` line matching your host user:

```yaml
services:
  abs-tidy-library:
    user: "1000:1000"   # run `id` on your host to find your UID:GID
```

---

## 🔄 Updating

```bash
docker compose pull && docker compose up -d
```

---

## 📄 License

MIT — see [LICENSE](LICENSE).

