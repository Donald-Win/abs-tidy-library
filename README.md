# 📚 ABS Tidy Library

A web UI for automatically organising your [Audiobookshelf](https://www.audiobookshelf.org/) library. Reads your metadata directly from ABS, renames audio files consistently, and reorganises your folders into a clean hierarchy — all with a live preview before anything is touched.

[![Docker Pulls](https://img.shields.io/docker/pulls/donaldwin/abs-tidy-library)](https://hub.docker.com/r/donaldwin/abs-tidy-library)
[![Build Status](https://github.com/Donald-Win/abs-tidy-library/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/Donald-Win/abs-tidy-library/actions)

---

## ✨ What It Does

- **Scans** your ABS library and shows statistics — books, authors, series, total play-time and size
- **Proposes changes** as rich diff cards showing exactly what folder and file names will change
- **Dry Run** mode simulates every move with zero changes made to disk
- **Selective apply** — tick the books you want moved instead of doing them all at once
- **Rollback** — one click to undo the last apply run, even after a page reload
- **Collision detection** — warns you before applying if two books would resolve to the same path
- **Auto re-scan prompt** — nudges you if you edit naming templates after a scan
- **Cleans empty directories** left over after moves
- **Live terminal** streams progress as it runs
- **Persistent settings** — naming templates survive container restarts

---

## 🚀 Quick Start

### 1. Get the compose file

```bash
curl -O https://raw.githubusercontent.com/Donald-Win/abs-tidy-library/main/compose.yaml
```

### 2. Edit the two required lines

Open `compose.yaml` and change the volume paths to match your setup:

```yaml
volumes:
  - /your/actual/audiobooks:/library   # where your books live on the host
  - /your/actual/config:/config        # any persistent folder for settings
```

And fill in your ABS connection details:

```yaml
environment:
  ABS_SERVER_URL: "http://192.168.1.100:13378"
  ABS_TOKEN:      "your-api-key-here"
```

> **Finding your API key:** In Audiobookshelf go to **Settings → API Keys → Add API Key**, give it a name, and copy the key.

### 3. Start

```bash
docker compose up -d
```

Open **http://your-server-ip:5050** in a browser. If your env vars are set correctly, it will connect and auto-select your library automatically — just hit **Scan Library**.

---

## ⚙️ Configuration Reference

All settings can be configured in the UI. The environment variables below are optional — they pre-fill the UI and act as a fallback if no saved config exists.

| Variable | Required | Description |
|---|---|---|
| `ABS_SERVER_URL` | Recommended | Your ABS server address e.g. `http://192.168.1.100:13378` |
| `ABS_TOKEN` | Recommended | API key from ABS → Settings → API Keys |
| `ABS_LIBRARY_ID` | Optional | Auto-selects a specific library on load |
| `LIBRARY_PATH` | Optional | Path inside the container where books are mounted. Default: `/library` |
| `CONFIG_PATH` | Optional | Where naming config is saved. Default: `/config/naming.json` |
| `PORT` | Optional | Internal web server port. Default: `8080` |
| `NAMING_FOLDER_STANDALONE` | Optional | Folder template for books not in a series |
| `NAMING_FOLDER_SERIES` | Optional | Folder template for series books |
| `NAMING_FILE_SINGLE` | Optional | Filename for standalone single-file books |
| `NAMING_FILE_MULTI` | Optional | Filename for each part of standalone multi-part books |
| `NAMING_FILE_SINGLE_SERIES` | Optional | Filename for series single-file books |
| `NAMING_FILE_MULTI_SERIES` | Optional | Filename for each part of series multi-part books |

---

## 🗂️ Naming Templates

Templates are built from tokens that are replaced with real metadata from ABS. Configure them in the **Settings** drawer — they save automatically.

### Available tokens

| Token | Example output |
|---|---|
| `{Author}` | `Brandon Sanderson` |
| `{Title}` | `The Final Empire` |
| `{Subtitle}` | `Book One of the Mistborn Saga` |
| `{Series}` | `Mistborn` |
| `{Series-Index}` | `01` |
| `{Narrator}` | `Michael Kramer` |
| `{Year}` | `2006` |
| `{Publisher}` | `Tor Books` |
| `{Genre}` | `Fantasy` |
| `{Language}` | `English` |
| `{ISBN}` | `9780765311788` |
| `{ASIN}` | `B002V0QVYO` |
| `{Part-Index}` | `02` |
| `{Part-Total}` | `12` |

### Default templates

```
Standalone folder:      {Author}/{Title}
Series folder:          {Author}/{Series}/{Series-Index} {Title}

Standalone file:        {Author} - {Title}.m4b
Standalone multi-part:  {Author} - {Title} (Part 02 of 12).m4b

Series file:            {Author} - {Series} 01 - {Title}.m4b
Series multi-part:      {Author} - {Series} 01 - {Title} (Part 02 of 12).m4b
```

### Example output

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

## 🔒 Safety

**Nothing is moved until you explicitly click Apply.** The recommended workflow is:

1. **Scan** — reads metadata from ABS, builds the change plan
2. **Review** — inspect every proposed change in the diff cards
3. **Dry Run** — simulates all moves and logs them, nothing touches disk
4. **Apply** (or **Apply Selected** for a subset)
5. **Rollback** if anything looks wrong — reverts all files from the last apply

The tool also checks for **naming collisions** before you apply — if two books would end up with the same folder or filename, it warns you to fix your templates or ABS metadata first.

---

## 🔧 Permissions

The container needs **read and write access** to your library directory. If you run into permission errors, add a `user` line matching your host user's UID and GID:

```yaml
services:
  abs-tidy-library:
    image: donaldwin/abs-tidy-library:latest
    user: "1000:1000"   # replace with your UID:GID (run `id` on the host to find these)
```

---

## 🔄 Updating

```bash
docker compose pull
docker compose up -d
```

---

## 🛠️ Building Locally

```bash
git clone https://github.com/Donald-Win/abs-tidy-library.git
cd abs-tidy-library
docker build -t abs-tidy-library:dev .
docker run -d \
  -p 5050:8080 \
  -v /your/audiobooks:/library \
  -v /your/config:/config \
  -e ABS_SERVER_URL="http://192.168.1.100:13378" \
  -e ABS_TOKEN="your-api-key" \
  abs-tidy-library:dev
```

To run without Docker:

```bash
cd app
pip install flask gunicorn requests
python web.py
```

---

## 📄 License

MIT — see [LICENSE](LICENSE).
