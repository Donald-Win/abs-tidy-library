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

## 🛠️ Development — Build Locally

### Prerequisites

- Docker Desktop (or Docker Engine + Compose)
- Git

### Clone and run

```bash
git clone https://github.com/Donald-Win/abs-tidy-library.git
cd abs-tidy-library

# Build image locally
docker build -t abs-tidy-library:dev .

# Run with your library mounted
docker run -d \
  -p 8080:8080 \
  -v /your/audiobooks:/library \
  abs-tidy-library:dev
```

### Run without Docker (Python)

```bash
cd app
pip install flask gunicorn
python web.py
```

---

## 📦 Publishing to DockerHub & GitHub

### 1. Create GitHub repository

```bash
cd abs-tidy-library
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/Donald-Win/abs-tidy-library.git
git push -u origin main
```

### 2. Add DockerHub secrets to GitHub

Go to your GitHub repo → **Settings → Secrets and variables → Actions → New repository secret**

| Secret name          | Value                                      |
|----------------------|--------------------------------------------|
| `DOCKERHUB_USERNAME` | `donaldwin`                                |
| `DOCKERHUB_TOKEN`    | Your DockerHub access token (see below)    |

**How to create a DockerHub access token:**

1. Log in to [hub.docker.com](https://hub.docker.com)
2. Click your avatar → **Account Settings → Security → New Access Token**
3. Name it `github-actions`, set **Read, Write, Delete** permissions
4. Copy the token and paste it as the `DOCKERHUB_TOKEN` secret on GitHub

### 3. Automatic builds

From this point on, every push to `main` automatically:

- Builds a multi-arch image (`linux/amd64` + `linux/arm64`)
- Pushes `donaldwin/abs-tidy-library:latest` to DockerHub
- Updates the DockerHub repository description with this README

### 4. Release a versioned tag

```bash
git tag v1.0.0
git push origin v1.0.0
```

This creates additional tags: `1.0.0`, `1.0`, `1` on DockerHub.

---

## 🔄 Updating the image

After making code changes:

```bash
git add .
git commit -m "Your change description"
git push
```

GitHub Actions will rebuild and push automatically. Users can pull the new image with:

```bash
docker compose pull
docker compose up -d
```

---

## ⚠️ Safety Notes

- **Always do a Dry Run first** before applying changes to a large library.
- The tool reads `metadata.json` files created by Audiobookshelf — point it at the Audiobookshelf library root.
- The container needs **read + write** access to your library directory.
- To avoid permission issues, add `user: "UID:GID"` to `docker-compose.yml` matching your host user.

---

## 📄 License

MIT — see [LICENSE](LICENSE).
