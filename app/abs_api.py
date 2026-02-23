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
    subtitle: str           # subtitle field from ABS
    author: str
    narrator: str
    series_name: str
    series_sequence: str    # raw sequence e.g. "1", "2.5" — padded later
    year: str               # publish year as string, e.g. "2001" or ""
    isbn: str               # ISBN-13 or ISBN-10
    asin: str               # Amazon ASIN
    language: str           # e.g. "en", "English"
    publisher: str          # publisher name
    genre: str              # first genre tag
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

    def trigger_library_scan(self, library_id: str):
        # ABS may return plain "OK" or JSON or 204 — handle all cases.
        # Returns (success: bool, message: str).
        url = f"{self.base_url}/api/libraries/{library_id}/scan"
        try:
            resp = self.session.post(url, timeout=self.timeout)
            if resp.status_code in (200, 204):
                return True, "Rescan started."
            return False, f"ABS returned HTTP {resp.status_code}: {resp.text[:200]}"
        except Exception as e:
            return False, str(e)

    # ── Normalisation ──────────────────────────────────────────────────────────

    AUDIO_EXTS = {'.mp3', '.m4b', '.m4a', '.flac', '.ogg', '.opus', '.aac', '.wma'}

    def _normalise_item(self, raw: dict) -> Optional[ABSBookItem]:
        try:
            media = raw.get("media", {})
            meta  = media.get("metadata", {})

            title     = self._scalar(meta, "title")                        or "Unknown Title"
            subtitle  = self._scalar(meta, "subtitle")                     or ""
            author    = self._scalar(meta, "authorName",    first_only=True) or "Unknown Author"
            narrator  = self._scalar(meta, "narratorName", first_only=True) or ""
            year      = str(meta.get("publishedYear") or meta.get("publishYear") or "").strip()
            isbn      = self._scalar(meta, "isbn")      or self._scalar(meta, "isbn13") or ""
            asin      = self._scalar(meta, "asin")      or ""
            language  = self._scalar(meta, "language")  or ""
            publisher = self._scalar(meta, "publisher") or ""
            genres    = meta.get("genres") or []
            genre     = str(genres[0]).strip() if genres else ""

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
                title=title, subtitle=subtitle, author=author, narrator=narrator,
                series_name=series_name, series_sequence=series_seq,
                year=year, isbn=isbn, asin=asin, language=language,
                publisher=publisher, genre=genre,
                duration=duration, size=size,
                book_path=book_path,
                audio_files=audio_files, all_files=all_files,
            )
        except Exception:
            return None

    def _scalar(self, meta: dict, key: str, first_only: bool = False) -> str:
        """
        Extract a string value from metadata.
        first_only=True: if the value is a comma-separated list, take the first item.
                         Use for author/narrator which ABS sometimes returns as
                         "Author A, Author B" and we only want the primary.
        first_only=False (default): return the full string as-is. Never split titles.
        """
        v = meta.get(key)
        if not v:
            return ""
        if isinstance(v, list):
            v = v[0] if v else ""
        s = str(v).strip()
        if first_only and "," in s:
            return s.split(",")[0].strip()
        return s

    def _parse_series(self, meta: dict) -> Tuple[str, str]:
        import re as _re
        # Modern: series is a list of {"name":..., "sequence":...}
        # A book can have multiple series (e.g. "Stormlight Archive" + "The Cosmere").
        # Pick the first entry that has a non-empty sequence number; fall back to first.
        series_arr = meta.get("series")
        if series_arr and isinstance(series_arr, list):
            dicts = [s for s in series_arr if isinstance(s, dict)]
            if dicts:
                primary = next(
                    (s for s in dicts if str(s.get("sequence") or "").strip()),
                    dicts[0],
                )
                name = primary.get("name", "").strip()
                seq  = str(primary.get("sequence") or "").strip()
                # Keep only leading number e.g. "3" from "3, The Cosmere"
                m = _re.match(r'^(\d+(?:\.\d+)?)', seq)
                seq = m.group(1) if m else seq.split(",")[0].strip()
                return name, seq

        # Legacy: seriesName = "Name #1" or "Name #3, Secondary"
        series_str = meta.get("seriesName", "")
        if series_str and "#" in series_str:
            parts = series_str.split("#", 1)
            seq   = parts[1].strip().split(",")[0].strip()  # drop secondary series
            return parts[0].strip(), seq
        if series_str:
            return series_str.split(",")[0].strip(), ""

        return "", ""


def _natural_key(s: str) -> list:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', s)]
