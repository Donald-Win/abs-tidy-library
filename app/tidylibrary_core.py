"""
ABS Tidy Library - Core Module
Audiobookshelf Library Tidy Tool (Web-adapted version)
Original CLI logic refactored to use callbacks instead of print/input.
"""

import os
import json
import re
import shutil
import datetime
from pathlib import Path
from typing import Dict, List, Tuple, Set, Any, Optional, Callable
from dataclasses import dataclass, field


# ── Constants ──────────────────────────────────────────────────────────────────

AUDIO_EXTENSIONS = {'.mp3', '.m4b', '.m4a', '.flac', '.ogg', '.opus', '.aac', '.wma'}
INVALID_FILENAME_CHARS = '<>:"/\\|?*'
BYTES_PER_GB = 1024 ** 3
SECONDS_PER_DAY  = 86400
SECONDS_PER_HOUR = 3600
SECONDS_PER_MINUTE = 60

# Default no-op emit so functions are always callable without a callback
_noop: Callable[[str], None] = lambda msg: None


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
            "books": self.books,
            "authors": len(self.authors),
            "narrators": len(self.narrators),
            "series": len(self.series),
            "standalone": self.standalone_count,
            "total_duration": format_total_duration(self.total_duration),
            "total_size_gb": round(self.total_size / BYTES_PER_GB, 2),
        }


@dataclass
class BookMove:
    title: str
    author: str
    old_dir: Path
    target_dir: Path
    move_plan: List[Tuple[Path, Path]]

    def to_dict(self, root_path: Path) -> dict:
        def rel(p: Path) -> str:
            try:
                return str(p.relative_to(root_path))
            except ValueError:
                return str(p)

        return {
            "title": self.title,
            "author": self.author,
            "old_dir": rel(self.old_dir),
            "target_dir": rel(self.target_dir),
            "files": [
                {"from": f.name, "to": t.name}
                for f, t in self.move_plan
                if f.name != t.name or f.parent != t.parent
            ],
        }


# ── Helpers ────────────────────────────────────────────────────────────────────

def natural_sort_key(s: Any) -> List:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', str(s))]


def clean_metadata(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return clean_metadata(value[0]) if value else ""
    s = str(value)
    if "," in s:
        s = s.split(",")[0]
    s = re.sub(r"^\[['\"]", "", s)
    s = re.sub(r"['\"]\]$", "", s)
    return s.strip()


def clean_filename(name: str) -> str:
    if not name:
        return ""
    for ch in INVALID_FILENAME_CHARS:
        name = name.replace(ch, '')
    return re.sub(r'\s+', ' ', name).strip()


def get_metadata_value(data: Dict[str, Any], key_names: List[str]) -> str:
    if isinstance(key_names, str):
        key_names = [key_names]
    for key in key_names:
        if data.get(key) is not None:
            return clean_metadata(data[key])
    meta = data.get('metadata', {})
    if isinstance(meta, dict):
        for key in key_names:
            if meta.get(key) is not None:
                return clean_metadata(meta[key])
    return ""


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


def parse_series_info(series_field: str) -> Tuple[str, str]:
    """Return (series_name, book_number) from a raw series string."""
    if not series_field:
        return "", ""
    parts = series_field.split("#")
    series_name = clean_filename(parts[0].strip())
    book_number = ""
    if len(parts) > 1:
        raw_num = parts[1].strip()
        try:
            num = float(raw_num)
            book_number = str(int(num)) if num == int(num) else raw_num
        except ValueError:
            book_number = raw_num
    return series_name, book_number


def build_move_plan(
    old_book_dir: Path,
    target_dir: Path,
    audio_files: List[Path],
    all_items: List[Path],
    c_author: str,
    series_title: str,
    book_number: str,
    c_title: str,
) -> List[Tuple[Path, Path]]:
    move_plan: List[Tuple[Path, Path]] = []
    audio_set = set(audio_files)
    num_audio = len(audio_files)

    for old_f in audio_files:
        ext = old_f.suffix.lower()
        if num_audio == 1:
            new_name = f"{c_author} - {c_title}{ext}"
        else:
            idx = audio_files.index(old_f) + 1
            pad = len(str(num_audio))
            new_name = f"{c_author} - {c_title} - Part {str(idx).zfill(pad)}{ext}"
        move_plan.append((old_f, target_dir / new_name))

    for item in all_items:
        if item not in audio_set and item.is_file():
            move_plan.append((item, target_dir / item.name))

    return move_plan


def process_metadata_file(
    meta_path: Path,
    root_path: Path,
    stats: LibraryStats,
) -> Optional[BookMove]:
    old_book_dir = meta_path.parent
    try:
        with open(meta_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError):
        return None

    stats.books += 1
    author       = get_metadata_value(data, ['authorName', 'author', 'authors', 'bookAuthor']) or "Unknown Author"
    book_title   = get_metadata_value(data, ['title', 'bookTitle']) or "Unknown Title"
    narrator     = get_metadata_value(data, ['narratorName', 'narrator', 'narrators']) or "Unknown Narrator"
    series_field = get_metadata_value(data, ['seriesName', 'series']) or ""
    duration_raw = data.get('duration') or data.get('metadata', {}).get('duration') or 0

    stats.authors.add(author)
    if narrator != "Unknown Narrator":
        stats.narrators.add(narrator)

    if series_field:
        series_name = series_field.split("#")[0].strip()
        stats.series.add(series_name)
    else:
        stats.standalone_count += 1

    try:
        stats.total_duration += float(duration_raw)
    except (ValueError, TypeError):
        pass

    series_title, book_number = parse_series_info(series_field)
    c_author = clean_filename(author)
    c_title  = clean_filename(book_title)

    if series_title:
        folder_label = f"{book_number} {c_title}".strip() if book_number else c_title
        target_dir = root_path / c_author / series_title / folder_label
    else:
        target_dir = root_path / c_author / c_title

    try:
        all_items = list(old_book_dir.iterdir())
    except PermissionError:
        return None

    audio_files = sorted(
        [f for f in all_items if f.suffix.lower() in AUDIO_EXTENSIONS],
        key=lambda x: natural_sort_key(x.name),
    )

    for item in all_items:
        if item.is_file():
            try:
                stats.total_size += item.stat().st_size
            except OSError:
                pass

    move_plan = build_move_plan(
        old_book_dir, target_dir, audio_files, all_items,
        c_author, series_title, book_number, c_title,
    )

    needs_changes = (
        old_book_dir.resolve() != target_dir.resolve() or
        any(old.name != new.name for old, new in move_plan)
    )

    if needs_changes:
        return BookMove(
            title=book_title,
            author=author,
            old_dir=old_book_dir,
            target_dir=target_dir,
            move_plan=move_plan,
        )
    return None


# ── Public API ─────────────────────────────────────────────────────────────────

def scan_library(
    root_path: Path,
    emit: Callable[[str], None] = _noop,
) -> Tuple[LibraryStats, List[BookMove]]:
    """
    Scan the library and return (stats, planned_moves).
    emit() is called with progress messages.
    """
    meta_files = list(root_path.rglob('metadata.json'))
    total_found = len(meta_files)

    if total_found == 0:
        emit("ERROR: No metadata.json files found. Check the library path.")
        return LibraryStats(), []

    emit(f"Found {total_found} books in library.")

    stats = LibraryStats()
    planned_moves: List[BookMove] = []

    for idx, meta_path in enumerate(meta_files, 1):
        if idx % 10 == 0 or idx == total_found:
            emit(f"Analysing: {idx}/{total_found} books…")
        bm = process_metadata_file(meta_path, root_path, stats)
        if bm:
            planned_moves.append(bm)

    emit(f"Scan complete. {len(planned_moves)} books need tidying.")
    return stats, planned_moves


def execute_book_move(
    book: BookMove,
    log_file: Path,
    collisions: Set[str],
    dry_run: bool,
    emit: Callable[[str], None] = _noop,
) -> bool:
    prefix = "[DRY RUN] " if dry_run else ""
    try:
        if not dry_run:
            book.target_dir.mkdir(parents=True, exist_ok=True)

        for old_f, new_f in book.move_plan:
            if not old_f.is_file():
                continue
            if old_f.resolve() == new_f.resolve():
                continue
            if new_f.exists():
                log_event(log_file, f"  {prefix}CONFLICT: {new_f.name}")
                collisions.add(new_f.name)
                continue
            if dry_run:
                log_event(log_file, f"  {prefix}Would move: {old_f.name} → {new_f.name}")
            else:
                shutil.move(str(old_f), str(new_f))
                log_event(log_file, f"  MOVED: {old_f.name} → {new_f.name}")

        if book.old_dir.exists() and not any(book.old_dir.iterdir()):
            if not dry_run:
                shutil.rmtree(book.old_dir)
                log_event(log_file, f"  CLEANUP: Removed empty dir {book.old_dir.name}")

        return True
    except (PermissionError, OSError, Exception) as e:
        log_event(log_file, f"!!! ERROR on '{book.title}': {e}")
        emit(f"ERROR on '{book.title}': {e}")
        return False


def apply_changes(
    planned_moves: List[BookMove],
    root_path: Path,
    log_file: Path,
    dry_run: bool = False,
    emit: Callable[[str], None] = _noop,
) -> Dict[str, int]:
    """Apply (or dry-run) all planned book moves."""
    collisions: Set[str] = set()
    exec_stats = {"applied": 0, "skipped": 0, "errors": 0}
    total = len(planned_moves)
    prefix = "[DRY RUN] " if dry_run else ""

    log_event(log_file, f"--- SESSION START {prefix}: {total} books ---")
    emit(f"{prefix}Starting {'preview' if dry_run else 'apply'} of {total} books…")

    for i, book in enumerate(planned_moves, 1):
        emit(f"{prefix}[{i}/{total}] {book.title}")
        if execute_book_move(book, log_file, collisions, dry_run, emit):
            exec_stats["applied"] += 1
        else:
            exec_stats["errors"] += 1

    log_event(log_file, f"--- SESSION END: applied={exec_stats['applied']} errors={exec_stats['errors']} ---")

    if collisions:
        emit(f"Collisions ({len(collisions)} files already at target): " + ", ".join(sorted(collisions)))

    summary = (
        f"{prefix}Done. "
        f"Applied: {exec_stats['applied']}, "
        f"Errors: {exec_stats['errors']}, "
        f"Collisions: {len(collisions)}"
    )
    emit(summary)
    return exec_stats


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
    """Find and remove empty directories, with recursive multi-pass."""
    prefix = "[DRY RUN] " if dry_run else ""
    stats = {"removed": 0, "failed": 0}
    pass_number = 1

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
        pass_number += 1
        if pass_number > 100:
            break

    log_event(log_file, f"{prefix}--- EMPTY DIR CLEANUP END: removed={stats['removed']} failed={stats['failed']} ---")
    emit(f"{prefix}Done. Removed: {stats['removed']}, Failed: {stats['failed']}")
    return stats
