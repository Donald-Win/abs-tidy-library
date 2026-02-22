# 📚 ABS Tidy Library

A web UI for organising your [Audiobookshelf](https://www.audiobookshelf.org/) library.  
Renames files consistently and reorganises your folders into a clean **Author → Series → Book** hierarchy.

[![Docker Pulls](https://img.shields.io/docker/pulls/donaldwin/abs-tidy-library)](https://hub.docker.com/r/donaldwin/abs-tidy-library)
[![Build Status](https://github.com/Donald-Win/abs-tidy-library/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/Donald-Win/abs-tidy-library/actions)

---

## ✨ Features

- **Scan** your library and see statistics (books, authors, series, play-time, size)
- **Preview proposed changes** before anything is touched
- **Dry Run** mode — simulate every move with zero risk
- **Apply All** — reorganise your entire library in one click
- **Clean Empty Dirs** — sweep up leftover empty folders
- **Live terminal output** streams progress in real time
- **Log viewer** — inspect the full `tidy_library_log.txt` in the browser

---

## 🚀 Quick Start (Docker)

### Option A — `docker run`

```bash
docker run -d \
  --name abs-tidy-library \
  --restart unless-stopped \
  -p 8080:8080 \
  -v /path/to/your/audiobooks:/library \
  donaldwin/abs-tidy-library:latest
```

Then open **http://localhost:8080** in your browser.

### Option B — `docker-compose`

1. Download `docker-compose.yml`:

```bash
curl -O https://raw.githubusercontent.com/Donald-Win/abs-tidy-library/main/docker-compose.yml
```

2. Edit the volume path:

```yaml
volumes:
  - /path/to/your/audiobooks:/library   # ← change left side
```

3. Start:

```bash
docker compose up -d
```

---

## ⚙️ Configuration

| Environment Variable | Default    | Description                                    |
|----------------------|------------|------------------------------------------------|
| `LIBRARY_PATH`       | `/library` | Default path shown in the UI                  |
| `PORT`               | `8080`     | Port the web server listens on                |

Example custom port:

```bash
docker run -d \
  -p 3000:3000 \
  -e PORT=3000 \
  -e LIBRARY_PATH=/library \
  -v /your/books:/library \
  donaldwin/abs-tidy-library:latest
```

---

## 🗂️ Folder Structure

The tool organises your library into:

```
Library Root/
└── Author Name/
    ├── Standalone Book Title/
    │   ├── Author Name - Book Title.m4b
    │   └── metadata.json
    └── Series Name/
        └── 1 First Book Title/
            ├── Author Name - First Book Title.m4b
            └── metadata.json
```

---

## 📄 License

MIT — see [LICENSE](LICENSE).
