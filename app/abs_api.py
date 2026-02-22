"""
ABS Tidy Library – Audiobookshelf API Client
All metadata is sourced exclusively from the ABS API.
No metadata.json files are read.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from pathlib import Path

try:
    import requests
    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False


# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class ABSLibrary:
    id: str
    name: str
    root_path: str
    media_type: str


@dataclass
class ABSBookItem:
    """A single audiobook item, normalised from the ABS API response."""
    item_id: str
    title: str
    author: str
    narrator: str
    series_name: str
    series_sequence: str    # raw sequence e.g. "1", "2.5" — padded later
    year: str               # publish year as string, e.g. "2001" or ""
    duration: float
    size: int
    book_path: str          # filesystem path to book directory
    audio_files: List[str]  # sorted filesystem paths of audio files
    all_files: List[str]    # all files including covers, metadata etc.


# ── Client ─────────────────────────────────────────────────────────────────────

class ABSClient:

    def __init__(self, base_url: str, token: str, timeout: int = 30):
        if not _REQUESTS_OK:
            raise RuntimeError("requests library is not installed")
        self.base_url = base_url.rstrip("/")
        self.token    = token
        self.timeout  = timeout
        self.session  = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept":        "application/json",
        })

    def _get(self, path: str, **kwargs) -> Any:
        url  = f"{self.base_url}{path}"
        resp = self.session.get(url, timeout=self.timeout, **kwargs)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, **kwargs) -> Any:
        url  = f"{self.base_url}{path}"
        resp = self.session.post(url, timeout=self.timeout, **kwargs)
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    # ── Connection ─────────────────────────────────────────────────────────────

    def ping(self) -> Tuple[bool, str]:
        """
        Validate connectivity and token via /api/me.
        Returns (ok, message) — message is username on success or error detail.
        """
        try:
            me       = self._get("/api/me")
            username = me.get("username") or me.get("name") or "unknown user"
            return True, f"Connected as '{username}'"
        except requests.exceptions.ConnectionError:
            return False, "Cannot reach server. Check the URL and that ABS is running."
        except requests.exceptions.Timeout:
            return False, "Connection timed out."
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else "?"
            if status == 401:
                return False, "Token rejected (401). Make sure you copied the complete API Key."
            if status == 403:
                return False, "Access denied (403). The key may lack permissions."
            return False, f"Server returned HTTP {status}."
        except Exception as e:
            return False, f"Unexpected error: {e}"

    # ── Libraries ──────────────────────────────────────────────────────────────

    def get_libraries(self) -> List[ABSLibrary]:
        data = self._get("/api/libraries")
        libs: List[ABSLibrary] = []
        for raw in data.get("libraries", []):
            if raw.get("mediaType") != "book":
                continue
            folders = raw.get("folders", [])
            root    = folders[0].get("fullPath", "") if folders else ""
            libs.append(ABSLibrary(
                id=raw["id"],
                name=raw.get("name", "Unnamed"),
                root_path=root,
                media_type="book",
            ))
        return libs

    # ── Items ──────────────────────────────────────────────────────────────────

    def get_library_items(
        self,
        library_id: str,
        emit=None,
    ) -> List[ABSBookItem]:
        """Fetch all book items; tries limit=0 first then paginates."""
        if emit:
            emit("Fetching library items from Audiobookshelf API…")

        try:
            data      = self._get(
                f"/api/libraries/{library_id}/items",
                params={"limit": 0, "minified": 0},
            )
            raw_items = data.get("results", [])
        except Exception:
            raw_items = []
            page, limit = 0, 100
            while True:
                data  = self._get(
                    f"/api/libraries/{library_id}/items",
                    params={"limit": limit, "page": page, "minified": 0},
                )
                batch = data.get("results", [])
                raw_items.extend(batch)
                if emit and page % 5 == 0:
                    emit(f"  Fetched {len(raw_items)} items…")
                if len(batch) < limit:
                    break
                page += 1

        if emit:
            emit(f"Retrieved {len(raw_items)} items from ABS.")

        items = [self._normalise_item(r) for r in raw_items if r]
        return [i for i in items if i is not None]

    # ── Rescan ─────────────────────────────────────────────────────────────────

    def trigger_library_scan(self, library_id: str) -> bool:
        try:
            self._post(f"/api/libraries/{library_id}/scan")
            return True
        except Exception:
            return False

    # ── Normalisation ──────────────────────────────────────────────────────────

    AUDIO_EXTS = {'.mp3', '.m4b', '.m4a', '.flac', '.ogg', '.opus', '.aac', '.wma'}

    def _normalise_item(self, raw: dict) -> Optional[ABSBookItem]:
        try:
            media = raw.get("media", {})
            meta  = media.get("metadata", {})

            title     = self._scalar(meta, "title")     or "Unknown Title"
            author    = self._scalar(meta, "authorName") or "Unknown Author"
            narrator  = self._scalar(meta, "narratorName") or ""
            year      = str(meta.get("publishedYear") or meta.get("publishYear") or "").strip()

            series_name, series_seq = self._parse_series(meta)

            duration  = float(media.get("duration") or 0)
            size      = int(media.get("size") or raw.get("size") or 0)
            book_path = raw.get("path", "")

            all_files   = []
            audio_files = []
            for lf in raw.get("libraryFiles", []):
                fpath = lf.get("metadata", {}).get("path", "")
                if not fpath:
                    continue
                all_files.append(fpath)
                if Path(fpath).suffix.lower() in self.AUDIO_EXTS:
                    audio_files.append(fpath)

            audio_files = sorted(audio_files, key=lambda p: _natural_key(Path(p).name))

            return ABSBookItem(
                item_id=raw.get("id", ""),
                title=title, author=author, narrator=narrator,
                series_name=series_name, series_sequence=series_seq,
                year=year, duration=duration, size=size,
                book_path=book_path,
                audio_files=audio_files, all_files=all_files,
            )
        except Exception:
            return None

    def _scalar(self, meta: dict, key: str) -> str:
        v = meta.get(key)
        if not v:
            return ""
        if isinstance(v, list):
            v = v[0] if v else ""
        s = str(v).strip()
        return s.split(",")[0].strip() if "," in s else s

    def _parse_series(self, meta: dict) -> Tuple[str, str]:
        # New format: series = [{"name": "...", "sequence": "1"}]
        series_arr = meta.get("series")
        if series_arr and isinstance(series_arr, list):
            first = series_arr[0]
            if isinstance(first, dict):
                return first.get("name", "").strip(), str(first.get("sequence") or "").strip()

        # Legacy: seriesName = "Name #1"
        series_str = meta.get("seriesName", "")
        if series_str and "#" in series_str:
            parts = series_str.split("#", 1)
            return parts[0].strip(), parts[1].strip()
        if series_str:
            return series_str.strip(), ""

        return "", ""


def _natural_key(s: str) -> list:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', s)]
