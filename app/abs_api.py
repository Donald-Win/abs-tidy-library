"""
ABS Tidy Library – Core Module
All metadata comes from the Audiobookshelf API (ABSBookItem objects).
Naming is driven by a NamingConfig with user-editable token templates.
"""

from __future__ import annotations

import os
import re
import json
import shutil
import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field


# ── Constants ──────────────────────────────────────────────────────────────────

AUDIO_EXTENSIONS   = {'.mp3', '.m4b', '.m4a', '.flac', '.ogg', '.opus', '.aac', '.wma'}
INVALID_FILENAME_CHARS = '<>:"/\\|?*'
BYTES_PER_GB       = 1024 ** 3
SECONDS_PER_DAY    = 86400
SECONDS_PER_HOUR   = 3600
SECONDS_PER_MINUTE = 60

_noop: Callable[[str], None] = lambda msg: None

# Config persistence path — mounted volume so it survives container restarts
CONFIG_PATH = Path(os.environ.get("CONFIG_PATH", "/config/naming.json"))


# ── Config persistence ─────────────────────────────────────────────────────────

def load_naming_config() -> "NamingConfig":
    """Load naming config from disk, falling back to env vars then defaults."""
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return NamingConfig.from_dict(json.load(f))
        except Exception:
            pass
    return NamingConfig.from_env()


def save_naming_config(naming: "NamingConfig") -> None:
    """Persist naming config to disk."""
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(naming.to_dict(), f, indent=2)
    except Exception:
        pass


# ── Naming configuration ───────────────────────────────────────────────────────

@dataclass
class NamingConfig:
    """
    Token-based naming templates, similar to Radarr/Sonarr.

    Available tokens:
      {Author}        – Author name
      {Title}         – Book title
      {Subtitle}      – Subtitle (if set in ABS)
      {Series}        – Series name
      {Series-Index}  – Zero-padded series sequence  e.g. 01, 02, 12
      {Narrator}      – Narrator name
      {Year}          – Publish year (if available)
      {ISBN}          – ISBN-13 or ISBN-10 (if set in ABS)
      {ASIN}          – Amazon ASIN (if set in ABS)
      {Language}      – Language (if set in ABS)
      {Publisher}     – Publisher name (if set in ABS)
      {Genre}         – First genre tag (if set in ABS)
      {Part-Index}    – Zero-padded part number for multi-file books  e.g. 04
      {Part-Total}    – Zero-padded total parts for multi-file books   e.g. 12

    folder_standalone      : folder path for books not in a series
    folder_series          : folder path for books in a series
                             (relative to library root — slashes create subfolders)
    file_single            : filename for standalone books with ONE audio file
    file_multi             : filename for each part of standalone multi-file books
    file_single_series     : filename for series books with ONE audio file
    file_multi_series      : filename for each part of series multi-file books
    """
    folder_standalone:  str = "{Author}/{Title}"
    folder_series:      str = "{Author}/{Series}/{Series-Index} {Title}"
    file_single:        str = "{Author} - {Title}"
    file_multi:         str = "{Author} - {Title} (Part {Part-Index} of {Part-Total})"
    file_single_series: str = "{Author} - {Series} {Series-Index} - {Title}"
    file_multi_series:  str = "{Author} - {Series} {Series-Index} - {Title} (Part {Part-Index} of {Part-Total})"

    # Preset library
    PRESETS: dict = field(default_factory=lambda: {
        "default": {
            "label": "Default",
            "folder_standalone": "{Author}/{Title}",
            "folder_series":     "{Author}/{Series}/{Series-Index} {Title}",
            "file_single":       "{Author} - {Title}",
            "file_multi":        "{Author} - {Title} (Part {Part-Index} of {Part-Total})",
            "file_single_series":  "{Author} - {Series} {Series-Index} - {Title}",
            "file_multi_series":   "{Author} - {Series} {Series-Index} - {Title} (Part {Part-Index} of {Part-Total})",
        },
        "series-first": {
            "label": "Series First",
            "folder_standalone": "{Author}/{Title}",
            "folder_series":     "{Series}/{Series-Index} {Title}",
            "file_single":       "{Author} - {Title}",
            "file_multi":        "{Author} - {Title} (Part {Part-Index} of {Part-Total})",
            "file_single_series":  "{Author} - {Series} {Series-Index} - {Title}",
            "file_multi_series":   "{Author} - {Series} {Series-Index} - {Title} (Part {Part-Index} of {Part-Total})",
        },
        "plex": {
            "label": "Plex-friendly",
            "folder_standalone": "{Author}/{Title}",
            "folder_series":     "{Author}/{Series}/{Series-Index} - {Title}",
            "file_single":       "{Title}",
            "file_multi":        "{Title} (Part {Part-Index} of {Part-Total})",
            "file_single_series":  "{Author} - {Series} {Series-Index} - {Title}",
            "file_multi_series":   "{Author} - {Series} {Series-Index} - {Title} (Part {Part-Index} of {Part-Total})",
        },
        "minimal": {
            "label": "Minimal",
            "folder_standalone": "{Author}/{Title}",
            "folder_series":     "{Author}/{Series}/{Series-Index} {Title}",
            "file_single":       "{Title}",
            "file_multi":        "{Title} (Part {Part-Index} of {Part-Total})",
            "file_single_series":  "{Author} - {Series} {Series-Index} - {Title}",
            "file_multi_series":   "{Author} - {Series} {Series-Index} - {Title} (Part {Part-Index} of {Part-Total})",
        },
        "series-in-filename": {
            "label": "Series in Filename",
            "folder_standalone": "{Author}/{Title}",
            "folder_series":     "{Author}/{Series}/{Series-Index} {Title}",
            "file_single":       "{Author} - {Series} {Series-Index} - {Title}",
            "file_multi":        "{Author} - {Series} {Series-Index} - {Title} (Part {Part-Index} of {Part-Total})",
            "file_single_series":  "{Author} - {Series} {Series-Index} - {Title}",
            "file_multi_series":   "{Author} - {Series} {Series-Index} - {Title} (Part {Part-Index} of {Part-Total})",
        },
    }, init=False, repr=False, compare=False)

    def to_dict(self) -> dict:
        return {
            "folder_standalone":  self.folder_standalone,
            "folder_series":      self.folder_series,
            "file_single":        self.file_single,
            "file_multi":         self.file_multi,
            "file_single_series": self.file_single_series,
            "file_multi_series":  self.file_multi_series,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "NamingConfig":
        def _d(key): return cls.__dataclass_fields__[key].default
        return cls(
            folder_standalone  = d.get("folder_standalone",  _d("folder_standalone")),
            folder_series      = d.get("folder_series",      _d("folder_series")),
            file_single        = d.get("file_single",        _d("file_single")),
            file_multi         = d.get("file_multi",         _d("file_multi")),
            file_single_series = d.get("file_single_series", _d("file_single_series")),
            file_multi_series  = d.get("file_multi_series",  _d("file_multi_series")),
        )

    @classmethod
    def from_env(cls) -> "NamingConfig":
        def _d(key): return cls.__dataclass_fields__[key].default
        return cls(
            folder_standalone  = os.environ.get("NAMING_FOLDER_STANDALONE",  _d("folder_standalone")),
            folder_series      = os.environ.get("NAMING_FOLDER_SERIES",      _d("folder_series")),
            file_single        = os.environ.get("NAMING_FILE_SINGLE",        _d("file_single")),
            file_multi         = os.environ.get("NAMING_FILE_MULTI",         _d("file_multi")),
            file_single_series = os.environ.get("NAMING_FILE_SINGLE_SERIES", _d("file_single_series")),
            file_multi_series  = os.environ.get("NAMING_FILE_MULTI_SERIES",  _d("file_multi_series")),
        )


# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class LibraryStats:
    books: int = 0
    authors: Set[str] = field(default_factory=set)
    narrators: Set[str] = field(default_factory=set)
    series: Set[str] = field(default_factory=set)
    total_size: int = 0
    total_duration: float = 0.0
    standalone_count: int = 0

    def to_dict(self) -> dict:
        return {
            "books":          self.books,
            "authors":        len(self.authors),
            "narrators":      len(self.narrators),
            "series":         len(self.series),
            "standalone":     self.standalone_count,
            "total_duration": format_total_duration(self.total_duration),
            "total_size_gb":  round(self.total_size / BYTES_PER_GB, 2),
        }


@dataclass
class BookMove:
    title: str
    author: str
    series_name: str
    series_sequence: str
    old_dir: Path
    target_dir: Path
    move_plan: List[Tuple[Path, Path]]
    abs_item_id: str = ""

    def to_dict(self, root_path: Path) -> dict:
        def rel(p: Path) -> str:
            try:
                return str(p.relative_to(root_path))
            except ValueError:
                return str(p)

        old_rel = rel(self.old_dir)
        new_rel = rel(self.target_dir)
        folder_changed = self.old_dir.resolve() != self.target_dir.resolve()

        audio_exts = {".mp3", ".m4b", ".m4a", ".flac", ".ogg", ".opus", ".aac", ".wma"}
        file_changes = [
            {"from": f.name, "to": t.name}
            for f, t in self.move_plan
            if Path(f.name).suffix.lower() in audio_exts and f.name != t.name
        ]

        return {
            "item_id":        self.abs_item_id,
            "title":          self.title,
            "author":         self.author,
            "series_name":    self.series_name,
            "series_seq":     self.series_sequence,
            "old_folder":     old_rel,
            "new_folder":     new_rel,
            "folder_changed": folder_changed,
            "file_changes":   file_changes,
            "files_renamed":  len(file_changes),
        }


# ── Metadata quality ──────────────────────────────────────────────────────────

@dataclass
class MetadataIssue:
    """A book whose ABS metadata is missing or poor quality."""
    item_id: str
    title: str
    author: str
    issues: List[str]

    def to_dict(self) -> dict:
        return {
            "item_id": self.item_id,
            "title":   self.title,
            "author":  self.author,
            "issues":  self.issues,
        }


def detect_metadata_issues(items) -> List[MetadataIssue]:
    """
    Inspect ABS items for metadata problems that would produce poor filenames.
    Returns a list of MetadataIssue for any book that has fixable problems.
    """
    problems: List[MetadataIssue] = []
    for item in items:
        issues: List[str] = []
        if not item.author or item.author == "Unknown Author":
            issues.append("Missing author")
        if not item.title or item.title == "Unknown Title":
            issues.append("Missing title")
        if item.series_name and not item.series_sequence:
            issues.append(f"In series '{item.series_name}' but has no sequence number")
        if item.series_name and re.search(r'#\s*\d', item.series_name):
            issues.append("Series name contains '#N' -- may not have been parsed correctly")
        if issues:
            problems.append(MetadataIssue(
                item_id=item.item_id,
                title=item.title,
                author=item.author,
                issues=issues,
            ))
    return problems


def check_filesystem_compatibility(
    planned_moves: List["BookMove"],
    root_path: Path,
) -> List[str]:
    """
    Check whether all planned moves stay on the same filesystem.
    Cross-device moves copy+delete rather than rename, so the inode changes
    and ABS cannot track the move -- it will create a duplicate entry and
    listen progress will be orphaned.
    Returns a list of warning strings (empty = all safe).
    """
    warnings: List[str] = []
    try:
        dst_dev = os.stat(root_path).st_dev
    except OSError:
        return []

    cross_device: List[str] = []
    for bm in planned_moves:
        if bm.old_dir.exists():
            try:
                src_dev = os.stat(bm.old_dir).st_dev
                if src_dev != dst_dev:
                    cross_device.append(bm.title)
            except OSError:
                pass

    if cross_device:
        sample = ", ".join(cross_device[:3]) + ("..." if len(cross_device) > 3 else "")
        warnings.append(
            f"{len(cross_device)} book(s) would move across filesystem boundaries "
            f"({sample}). Inodes will change -- ABS may create duplicate entries "
            f"and listen progress could be lost."
        )
    return warnings


# ── String helpers ─────────────────────────────────────────────────────────────

def natural_sort_key(s: Any) -> List:
    return [int(t) if t.isdigit() else t.lower()
            for t in re.split(r'(\d+)', str(s))]


def clean_filename(name: str) -> str:
    if not name:
        return ""
    for ch in INVALID_FILENAME_CHARS:
        name = name.replace(ch, '')
    return re.sub(r'\s+', ' ', name).strip()


def format_total_duration(seconds: float) -> str:
    if not seconds:
        return "0h 0m"
    days    = int(seconds // SECONDS_PER_DAY)
    rem     = seconds % SECONDS_PER_DAY
    hours   = int(rem // SECONDS_PER_HOUR)
    minutes = int((rem % SECONDS_PER_HOUR) // SECONDS_PER_MINUTE)
    return f"{days}d {hours}h {minutes}m" if days else f"{hours}h {minutes}m"


def log_event(log_file: Path, message: str) -> None:
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {message}\n")
    except IOError:
        pass


# ── Token rendering ────────────────────────────────────────────────────────────

def render_template(template: str, tokens: Dict[str, str]) -> str:
    """Replace {Token} placeholders, clean each segment for filesystem safety."""
    result = template
    for key, value in tokens.items():
        result = result.replace(f"{{{key}}}", clean_filename(str(value)) if value else "")

    # Clean each path segment
    parts = result.split("/")
    cleaned = []
    for part in parts:
        # Collapse runs of whitespace
        part = re.sub(r'\s+', ' ', part).strip()
        # Remove orphaned separators left by empty tokens e.g. "Author -  - Title" → "Author - Title"
        part = re.sub(r'(\s*-\s*){2,}', ' - ', part)
        part = re.sub(r'^[\s\-]+|[\s\-]+$', '', part).strip()
        if part:
            cleaned.append(part)

    return "/".join(cleaned)


def make_tokens(
    author: str,
    title: str,
    series_name: str,
    series_sequence: str,
    narrator: str = "",
    year: str = "",
    subtitle: str = "",
    isbn: str = "",
    asin: str = "",
    language: str = "",
    publisher: str = "",
    genre: str = "",
) -> Dict[str, str]:
    return {
        "Author":       author,
        "Title":        title,
        "Subtitle":     subtitle,
        "Series":       series_name,
        "Series-Index": series_sequence,
        "Narrator":     narrator,
        "Year":         year,
        "ISBN":         isbn,
        "ASIN":         asin,
        "Language":     language,
        "Publisher":    publisher,
        "Genre":        genre,
    }


# ── Move plan builder ──────────────────────────────────────────────────────────

def build_move_plan(
    old_book_dir: Path,
    target_dir: Path,
    audio_files: List[Path],
    all_items: List[Path],
    tokens: Dict[str, str],
    naming: NamingConfig,
    is_series: bool = False,
) -> List[Tuple[Path, Path]]:
    move_plan: List[Tuple[Path, Path]] = []
    audio_set = set(audio_files)
    num_audio = len(audio_files)
    pad_width = max(2, len(str(num_audio)))

    # Pick the right filename template based on series membership and part count
    tpl_single = naming.file_single_series if is_series else naming.file_single
    tpl_multi  = naming.file_multi_series  if is_series else naming.file_multi

    for idx, old_f in enumerate(audio_files, 1):
        ext = old_f.suffix.lower()
        if num_audio == 1:
            stem = render_template(tpl_single, tokens)
        else:
            part_tokens = {**tokens,
                           "Part-Index": str(idx).zfill(pad_width),
                           "Part-Total": str(num_audio).zfill(pad_width)}
            stem = render_template(tpl_multi, part_tokens)
        move_plan.append((old_f, target_dir / f"{stem}{ext}"))

    for item in all_items:
        if item not in audio_set and item.is_file():
            move_plan.append((item, target_dir / item.name))

    return move_plan


# ── ABS-mode scan ──────────────────────────────────────────────────────────────

def scan_library_abs(
    items,
    root_path: Path,
    naming: NamingConfig,
    emit: Callable[[str], None] = _noop,
    abs_library_root: str = "",
) -> Tuple[LibraryStats, List[BookMove]]:

    stats         = LibraryStats()
    planned_moves: List[BookMove] = []
    abs_root      = Path(abs_library_root) if abs_library_root else None

    emit(f"Scanning {len(items)} books…")

    for item in items:
        stats.books += 1
        stats.authors.add(item.author)
        if item.narrator:
            stats.narrators.add(item.narrator)
        if item.series_name:
            stats.series.add(item.series_name)
        else:
            stats.standalone_count += 1
        stats.total_size     += item.size
        stats.total_duration += item.duration

        tokens = make_tokens(
            author          = item.author,
            title           = item.title,
            series_name     = item.series_name,
            series_sequence = item.series_sequence,
            narrator        = item.narrator,
            year            = item.year,
            subtitle        = item.subtitle,
            isbn            = item.isbn,
            asin            = item.asin,
            language        = item.language,
            publisher       = item.publisher,
            genre           = item.genre,
        )

        if item.series_name:
            folder_rel = render_template(naming.folder_series, tokens)
        else:
            folder_rel = render_template(naming.folder_standalone, tokens)

        abs_book_path = Path(item.book_path)
        if abs_root:
            try:
                rel_from_abs = abs_book_path.relative_to(abs_root)
                old_book_dir = root_path / rel_from_abs
            except ValueError:
                old_book_dir = abs_book_path
        else:
            old_book_dir = abs_book_path

        target_dir = root_path / Path(folder_rel)

        def remap(p_str: str) -> Path:
            p = Path(p_str)
            if abs_root:
                try:
                    return root_path / p.relative_to(abs_root)
                except ValueError:
                    pass
            return p

        AUDIO_EXTS = {'.mp3', '.m4b', '.m4a', '.flac', '.ogg', '.opus', '.aac', '.wma'}

        if old_book_dir.exists():
            try:
                disk_files = sorted(old_book_dir.iterdir(), key=lambda f: natural_sort_key(f.name))
                audio_paths = [f for f in disk_files if f.is_file() and f.suffix.lower() in AUDIO_EXTS]
                all_paths   = [f for f in disk_files if f.is_file()]
            except PermissionError:
                audio_paths = [remap(p) for p in item.audio_files]
                all_paths   = [remap(p) for p in item.all_files]
        else:
            audio_paths = [remap(p) for p in item.audio_files]
            all_paths   = [remap(p) for p in item.all_files]

        move_plan = build_move_plan(
            old_book_dir, target_dir, audio_paths, all_paths, tokens, naming,
            is_series=bool(item.series_name),
        )

        needs_changes = (
            old_book_dir.resolve() != target_dir.resolve() or
            any(old.name != new.name for old, new in move_plan)
        )

        if needs_changes:
            planned_moves.append(BookMove(
                title=item.title, author=item.author,
                series_name=item.series_name, series_sequence=item.series_sequence,
                old_dir=old_book_dir, target_dir=target_dir,
                move_plan=move_plan, abs_item_id=item.item_id,
            ))

    planned_moves = pad_series_numbers(planned_moves, naming, root_path)

    def still_needs_move(bm: BookMove) -> bool:
        if bm.old_dir.resolve() == bm.target_dir.resolve():
            return any(old_f.name != new_f.name for old_f, new_f in bm.move_plan)
        if not bm.old_dir.exists() and bm.target_dir.exists():
            return False
        if not bm.old_dir.exists():
            return False
        return True

    before = len(planned_moves)
    stale_abs_path = []
    genuinely_needed = []
    for bm in planned_moves:
        if still_needs_move(bm):
            genuinely_needed.append(bm)
        else:
            if not bm.old_dir.exists() and bm.target_dir.exists():
                stale_abs_path.append(bm.title)
    planned_moves = genuinely_needed
    filtered = before - len(planned_moves)
    if filtered:
        emit(f"  (Filtered {filtered} books already correctly named after sequence padding.)")
    if stale_abs_path:
        emit(f"  (Filtered {len(stale_abs_path)} books where move was done but ABS path is stale — rescan ABS to update: {', '.join(stale_abs_path[:5])}{'…' if len(stale_abs_path)>5 else ''})")

    # ── Collision detection ────────────────────────────────────────────────────
    # Check for duplicate target directories and duplicate target file paths
    collisions = []
    target_dirs: Dict[str, List[str]] = {}
    target_files: Dict[str, List[str]] = {}

    for bm in planned_moves:
        td = str(bm.target_dir.resolve())
        target_dirs.setdefault(td, []).append(bm.title)
        for _, new_f in bm.move_plan:
            tf = str(new_f.resolve())
            target_files.setdefault(tf, []).append(bm.title)

    for td, titles in target_dirs.items():
        if len(titles) > 1:
            collisions.append(f"Folder collision: {titles} → same folder")
            emit(f"  ⚠ COLLISION: {len(titles)} books share target folder: {', '.join(titles)}")

    for tf, titles in target_files.items():
        if len(titles) > 1:
            fname = Path(tf).name
            collisions.append(f"File collision: {titles} → {fname}")
            emit(f"  ⚠ COLLISION: {len(titles)} books share target file: {fname}")

    if collisions:
        emit(f"  ⚠ {len(collisions)} collision(s) detected — review before applying!")

    emit(f"Planning complete. {len(planned_moves)} books need tidying.")
    return stats, planned_moves, collisions


# ── Execution ──────────────────────────────────────────────────────────────────

def execute_book_move(
    book: BookMove,
    log_file: Path,
    global_collisions: Set[str],
    dry_run: bool,
    emit: Callable[[str], None] = _noop,
) -> Tuple[bool, List[Dict]]:
    """
    Execute a single book move. Returns (success, rollback_entries).
    rollback_entries is a list of {from, to} dicts (actual moves made) for undo.
    On partial failure, automatically reverses any files already moved.
    """
    prefix = "[DRY RUN] " if dry_run else ""
    rollback_entries: List[Dict] = []

    try:
        if not dry_run:
            book.target_dir.mkdir(parents=True, exist_ok=True)
        else:
            emit(f"  {prefix}Would create: {book.target_dir}")

        for old_f, new_f in book.move_plan:
            if not old_f.is_file():
                if not new_f.is_file():
                    emit(f"  WARN: source file missing and not at target either: {old_f.name}")
                    log_event(log_file, f"  WARN: missing {old_f}")
                continue
            if old_f.resolve() == new_f.resolve():
                continue
            if new_f.exists():
                log_event(log_file, f"  {prefix}CONFLICT: {new_f.name}")
                global_collisions.add(new_f.name)
                emit(f"  CONFLICT: {new_f.name} already at target")
                continue
            if dry_run:
                log_event(log_file, f"  {prefix}Would move: {old_f.name} → {new_f.name}")
                emit(f"  {prefix}{old_f.name}  →  {new_f.name}")
            else:
                shutil.move(str(old_f), str(new_f))
                log_event(log_file, f"  MOVED: {old_f.name} → {new_f.name}")
                rollback_entries.append({"from": str(new_f), "to": str(old_f)})

        if not dry_run and book.old_dir.exists():
            try:
                if not any(book.old_dir.iterdir()):
                    shutil.rmtree(book.old_dir)
                    log_event(log_file, f"  CLEANUP: Removed {book.old_dir.name}")
            except (PermissionError, OSError):
                pass

        return True, rollback_entries

    except Exception as e:
        # ── Partial failure recovery: reverse files we already moved ──────────
        log_event(log_file, f"!!! ERROR '{book.title}': {e}")
        emit(f"ERROR '{book.title}': {e}")
        if rollback_entries:
            emit(f"  Reversing {len(rollback_entries)} already-moved file(s)…")
            for entry in reversed(rollback_entries):
                try:
                    shutil.move(entry["from"], entry["to"])
                    emit(f"  Reversed: {Path(entry['from']).name}")
                except Exception as re_err:
                    emit(f"  Could not reverse {Path(entry['from']).name}: {re_err}")
        return False, []


def apply_changes(
    planned_moves: List[BookMove],
    root_path: Path,
    log_file: Path,
    dry_run: bool = False,
    emit: Callable[[str], None] = _noop,
    selected_ids: Optional[List[str]] = None,
    on_book_moved: Optional[Callable[[str], None]] = None,
) -> Tuple[Dict[str, int], List[Dict]]:
    """
    Apply planned moves. Returns (exec_stats, move_log).
    If selected_ids is provided, only those item IDs are applied.
    on_book_moved is called with abs_item_id after each successful real move
    -- used to trigger per-item ABS rescans immediately after each book moves.
    """
    if selected_ids is not None:
        id_set = set(selected_ids)
        planned_moves = [bm for bm in planned_moves if bm.abs_item_id in id_set]

    collisions: Set[str] = set()
    exec_stats = {"applied": 0, "skipped": 0, "errors": 0}
    move_log:  List[Dict] = []
    total  = len(planned_moves)
    prefix = "[DRY RUN] " if dry_run else ""

    log_event(log_file, f"--- SESSION START {prefix}: {total} books ---")
    emit(f"{prefix}Starting {'preview' if dry_run else 'apply'} of {total} books...")

    for i, book in enumerate(planned_moves, 1):
        emit(f"{prefix}[{i}/{total}] {book.title}")
        ok, entries = execute_book_move(book, log_file, collisions, dry_run, emit)
        if ok:
            exec_stats["applied"] += 1
            if entries:
                move_log.append({
                    "title":   book.title,
                    "author":  book.author,
                    "moves":   entries,
                    "old_dir": str(book.old_dir),
                    "new_dir": str(book.target_dir),
                })
            # Per-item scan: notify ABS immediately after each book moves
            if not dry_run and book.abs_item_id and on_book_moved:
                try:
                    on_book_moved(book.abs_item_id)
                except Exception:
                    pass
        else:
            exec_stats["errors"] += 1

    log_event(log_file, f"--- SESSION END: applied={exec_stats['applied']} errors={exec_stats['errors']} ---")
    if collisions:
        emit(f"Collisions ({len(collisions)}): " + ", ".join(sorted(collisions)))
    emit(f"{prefix}Done. Applied: {exec_stats['applied']}, Errors: {exec_stats['errors']}, Collisions: {len(collisions)}")
    return exec_stats, move_log


def rollback_moves(
    move_log: List[Dict],
    log_file: Path,
    emit: Callable[[str], None] = _noop,
) -> Dict[str, int]:
    """Reverse a move_log produced by apply_changes."""
    stats = {"reversed": 0, "errors": 0}
    total = sum(len(entry["moves"]) for entry in move_log)
    emit(f"Rolling back {len(move_log)} book(s) ({total} file(s))…")
    log_event(log_file, f"--- ROLLBACK START: {len(move_log)} books ---")

    for entry in reversed(move_log):
        emit(f"  Reversing: {entry['title']}")
        # Re-create original dir if needed
        try:
            old_dir = Path(entry["old_dir"])
            old_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        for mv in reversed(entry["moves"]):
            src, dst = Path(mv["from"]), Path(mv["to"])
            try:
                if src.exists():
                    shutil.move(str(src), str(dst))
                    log_event(log_file, f"  ROLLED BACK: {src.name} → {dst.name}")
                    stats["reversed"] += 1
                else:
                    emit(f"  WARN: rollback source missing: {src.name}")
            except Exception as e:
                emit(f"  ERROR rolling back {src.name}: {e}")
                stats["errors"] += 1

        # Clean up the (now-empty) new dir
        try:
            new_dir = Path(entry["new_dir"])
            if new_dir.exists() and not any(new_dir.iterdir()):
                shutil.rmtree(new_dir)
        except Exception:
            pass

    log_event(log_file, f"--- ROLLBACK END: reversed={stats['reversed']} errors={stats['errors']} ---")
    emit(f"Rollback done. Reversed: {stats['reversed']}, Errors: {stats['errors']}")
    return stats


# ── Padding ────────────────────────────────────────────────────────────────────

def pad_series_numbers(
    planned_moves: List[BookMove],
    naming: NamingConfig,
    root_path: Path,
) -> List[BookMove]:
    """
    Ensure series sequence numbers are zero-padded consistently within each series.
    e.g. if a series has books 1-12, pad all to 2 digits: 01, 02 … 12.
    """
    from collections import defaultdict

    # Group by series name
    series_books: Dict[str, List[BookMove]] = defaultdict(list)
    for bm in planned_moves:
        if bm.series_name:
            series_books[bm.series_name].append(bm)

    for series_name, books in series_books.items():
        # Find max sequence number to determine pad width
        seqs = []
        for bm in books:
            m = re.match(r'^(\d+)', bm.series_sequence)
            if m:
                seqs.append(int(m.group(1)))
        if not seqs:
            continue
        pad_width = max(2, len(str(max(seqs))))

        for bm in books:
            m = re.match(r'^(\d+(?:\.\d+)?)', bm.series_sequence)
            if not m:
                continue
            raw_seq = m.group(1)
            int_part, _, dec_part = raw_seq.partition('.')
            padded = int_part.zfill(pad_width) + (f'.{dec_part}' if dec_part else '')
            if padded == bm.series_sequence:
                continue
            bm.series_sequence = padded
            # Re-render target_dir with padded sequence
            tokens = make_tokens(
                author=bm.author, title=bm.title,
                series_name=bm.series_name, series_sequence=padded,
            )
            folder_tpl = naming.folder_series if bm.series_name else naming.folder_standalone
            bm.target_dir = root_path / render_template(folder_tpl, tokens)
            # Re-render file names in move_plan
            new_plan = []
            for old_f, _ in bm.move_plan:
                is_audio = old_f.suffix.lower() in AUDIO_EXTENSIONS
                if is_audio:
                    num_audio = sum(1 for f, _ in bm.move_plan if f.suffix.lower() in AUDIO_EXTENSIONS)
                    pw = max(2, len(str(num_audio)))
                    idx = sum(1 for f, _ in new_plan if f.suffix.lower() in AUDIO_EXTENSIONS) + 1
                    is_series = bool(bm.series_name)
                    tpl = (naming.file_multi_series if is_series else naming.file_multi) if num_audio > 1 else (naming.file_single_series if is_series else naming.file_single)
                    pt = {**tokens, "Part-Index": str(idx).zfill(pw), "Part-Total": str(num_audio).zfill(pw)}
                    stem = render_template(tpl, pt)
                    new_plan.append((old_f, bm.target_dir / f"{stem}{old_f.suffix.lower()}"))
                else:
                    new_plan.append((old_f, bm.target_dir / old_f.name))
            bm.move_plan = new_plan

    return planned_moves


# ── Empty directory cleanup ────────────────────────────────────────────────────

def find_empty_directories(root_path: Path) -> List[Path]:
    empty: List[Path] = []
    try:
        for dirpath, _, _ in os.walk(root_path, topdown=False):
            current = Path(dirpath)
            if current.resolve() == root_path.resolve():
                continue
            try:
                if not any(current.iterdir()):
                    empty.append(current)
            except PermissionError:
                pass
    except Exception:
        pass
    return sorted(empty, key=lambda p: len(p.parts), reverse=True)


def clean_empty_dirs(
    root_path: Path,
    log_file: Path,
    dry_run: bool = False,
    emit: Callable[[str], None] = _noop,
) -> Dict[str, int]:
    prefix = "[DRY RUN] " if dry_run else ""
    stats  = {"removed": 0, "failed": 0}
    pass_n = 1

    emit(f"{prefix}Scanning for empty directories…")
    empty_dirs = find_empty_directories(root_path)

    if not empty_dirs:
        emit("No empty directories found.")
        return stats

    emit(f"Found {len(empty_dirs)} empty directories.")
    log_event(log_file, f"{prefix}--- EMPTY DIR CLEANUP START ---")

    while empty_dirs:
        for d in empty_dirs:
            try:
                if d.exists() and not any(d.iterdir()):
                    rel = str(d.relative_to(root_path)) if d.is_relative_to(root_path) else str(d)
                    if dry_run:
                        log_event(log_file, f"  {prefix}Would remove: {rel}")
                        emit(f"  Would remove: {rel}")
                        stats["removed"] += 1
                    else:
                        shutil.rmtree(d)
                        log_event(log_file, f"  REMOVED: {rel}")
                        stats["removed"] += 1
            except (PermissionError, OSError) as e:
                log_event(log_file, f"  ERROR: {d} - {e}")
                stats["failed"] += 1
        if dry_run:
            break
        empty_dirs = find_empty_directories(root_path)
        if not empty_dirs:
            break
        pass_n += 1
        if pass_n > 100:
            break

    log_event(log_file, f"{prefix}--- EMPTY DIR CLEANUP END: removed={stats['removed']} failed={stats['failed']} ---")
    emit(f"{prefix}Done. Removed: {stats['removed']}, Failed: {stats['failed']}")
    return stats
