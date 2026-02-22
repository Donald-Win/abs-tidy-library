"""
ABS Tidy Library – Audiobookshelf API Client
Wraps the ABS REST API for connection testing, library listing,
item fetching, and triggering rescans after file moves.
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
    root_path: str          # first folder's fullPath
    media_type: str         # "book" | "podcast"
    item_count: int = 0


@dataclass
class ABSBookItem:
    """A single audiobook item as returned by the ABS API, normalised."""
    item_id: str
    title: str
    author: str
    narrator: str
    series_name: str        # e.g. "The Stormlight Archive"
    series_sequence: str    # e.g. "1" or "2.5"
    duration: float
    size: int               # bytes
    book_path: str          # full filesystem path to book directory
    audio_files: List[str]  # full filesystem paths, sorted
    all_files: List[str]    # all files (audio + cover + metadata etc.)


# ── Client ─────────────────────────────────────────────────────────────────────

class ABSClient:
    """Lightweight Audiobookshelf API client using requests."""

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

    # ── Internal ───────────────────────────────────────────────────────────────

    def _get(self, path: str, **kwargs) -> Any:
        url = f"{self.base_url}{path}"
        resp = self.session.get(url, timeout=self.timeout, **kwargs)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, **kwargs) -> Any:
        url = f"{self.base_url}{path}"
        resp = self.session.post(url, timeout=self.timeout, **kwargs)
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    # ── Public API ─────────────────────────────────────────────────────────────

    def ping(self) -> Tuple[bool, str]:
        """
        Validate connectivity and token in one step via /api/me.
        (ABS does not have a /api/ping endpoint on all versions.)
        Returns (ok: bool, message: str).
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

    def get_libraries(self) -> List[ABSLibrary]:
        """Return all libraries on the server (books only)."""
        data = self._get("/api/libraries")
        libs: List[ABSLibrary] = []
        for raw in data.get("libraries", []):
            if raw.get("mediaType") != "book":
                continue
            folders = raw.get("folders", [])
            root = folders[0].get("fullPath", "") if folders else ""
            libs.append(ABSLibrary(
                id=raw["id"],
                name=raw.get("name", "Unnamed"),
                root_path=root,
                media_type=raw.get("mediaType", "book"),
            ))
        return libs

    def get_library_items(
        self,
        library_id: str,
        emit=None,
    ) -> List[ABSBookItem]:
        """
        Fetch all book items in a library.
        Uses limit=0 to get everything in one request.
        Falls back to paginated fetching if the server rejects limit=0.
        """
        if emit:
            emit("Fetching library items from Audiobookshelf…")

        try:
            data = self._get(
                f"/api/libraries/{library_id}/items",
                params={"limit": 0, "minified": 0, "include": "rssfeed"},
            )
            raw_items = data.get("results", [])
        except Exception:
            # Paginated fallback
            raw_items = []
            page = 0
            limit = 100
            while True:
                data = self._get(
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

        return [self._normalise_item(r) for r in raw_items if r]

    def trigger_library_scan(self, library_id: str) -> bool:
        """Ask ABS to rescan a library (after file moves)."""
        try:
            self._post(f"/api/libraries/{library_id}/scan")
            return True
        except Exception:
            return False

    def trigger_item_scan(self, item_id: str) -> bool:
        """Ask ABS to rescan a single item."""
        try:
            self._post(f"/api/items/{item_id}/scan")
            return True
        except Exception:
            return False

    # ── Normalisation ──────────────────────────────────────────────────────────

    AUDIO_EXTS = {'.mp3', '.m4b', '.m4a', '.flac', '.ogg', '.opus', '.aac', '.wma'}

    def _normalise_item(self, raw: dict) -> Optional[ABSBookItem]:
        try:
            media    = raw.get("media", {})
            meta     = media.get("metadata", {})
            book_path = raw.get("path", "")

            title    = self._first(meta, ["title"]) or "Unknown Title"
            author   = self._first(meta, ["authorName"]) or "Unknown Author"
            narrator = self._first(meta, ["narratorName"]) or ""

            series_name, series_seq = self._parse_series(meta)

            duration = float(media.get("duration") or 0)
            size     = int(media.get("size") or raw.get("size") or 0)

            # Collect files from libraryFiles
            all_files  = []
            audio_files = []
            for lf in raw.get("libraryFiles", []):
                fmeta = lf.get("metadata", {})
                fpath = fmeta.get("path", "")
                if not fpath:
                    continue
                all_files.append(fpath)
                if Path(fpath).suffix.lower() in self.AUDIO_EXTS:
                    audio_files.append(fpath)

            # Natural sort audio files
            audio_files = sorted(audio_files, key=lambda p: _natural_key(Path(p).name))

            return ABSBookItem(
                item_id=raw.get("id", ""),
                title=title,
                author=author,
                narrator=narrator,
                series_name=series_name,
                series_sequence=series_seq,
                duration=duration,
                size=size,
                book_path=book_path,
                audio_files=audio_files,
                all_files=all_files,
            )
        except Exception:
            return None

    def _first(self, meta: dict, keys: List[str]) -> str:
        for k in keys:
            v = meta.get(k)
            if v:
                if isinstance(v, list):
                    v = v[0] if v else None
                if v:
                    s = str(v)
                    if "," in s:
                        s = s.split(",")[0]
                    return s.strip()
        return ""

    def _parse_series(self, meta: dict) -> Tuple[str, str]:
        """
        Extract (series_name, sequence) from ABS item metadata.
        Handles both the new series-array format and the legacy seriesName string.
        """
        # New format: meta.series = [{"name": "...", "sequence": "1"}]
        series_arr = meta.get("series")
        if series_arr and isinstance(series_arr, list):
            first = series_arr[0]
            if isinstance(first, dict):
                name = first.get("name", "").strip()
                seq  = str(first.get("sequence") or "").strip()
                return name, seq

        # Legacy format: meta.seriesName = "Series Name #1"
        series_str = meta.get("seriesName", "")
        if series_str and "#" in series_str:
            parts = series_str.split("#", 1)
            return parts[0].strip(), parts[1].strip()
        if series_str:
            return series_str.strip(), ""

        return "", ""


# ── Helpers ────────────────────────────────────────────────────────────────────

def _natural_key(s: str) -> list:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', s)]
