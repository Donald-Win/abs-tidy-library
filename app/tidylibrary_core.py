"""
ABS Tidy Library – Core Module
Supports two scan modes:
  • FILE MODE   – reads metadata.json files directly from the filesystem
  • ABS MODE    – uses the Audiobookshelf API for metadata, filesystem for moves

Leading-zero fix:
  After building all BookMoves, pad_series_numbers() is called to ensure every
  series' sequence numbers use consistent zero-padding derived from the widest
  sequence number in that series (minimum 2 digits).
  e.g., a 12-book series: "01", "02", ... "12"; a 9-book series: "01" ... "09".
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

AUDIO_EXTENSIONS = {'.mp3', '.m4b', '.m4a', '.flac', '.ogg', '.opus', '.aac', '.wma'}
INVALID_FILENAME_CHARS = '<>:"/\\|?*'
BYTES_PER_GB       = 1024 ** 3
SECONDS_PER_DAY    = 86400
SECONDS_PER_HOUR   = 3600
SECONDS_PER_MINUTE = 60

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
    series_sequence: str        # stored after zero-padding is applied
    old_dir: Path
    target_dir: Path
    move_plan: List[Tuple[Path, Path]]
    abs_item_id: str = ""       # set in ABS mode; used for post-move rescan

    def to_dict(self, root_path: Path) -> dict:
        def rel(p: Path) -> str:
            try:
                return str(p.relative_to(root_path))
            except ValueError:
                return str(p)

        return {
            "title":      self.title,
            "author":     self.author,
            "series":     f"{self.series_name} #{self.series_sequence}" if self.series_name else "",
            "old_dir":    rel(self.old_dir),
            "target_dir": rel(self.target_dir),
            "files": [
                {"from": f.name, "to": t.name}
                for f, t in self.move_plan
                if f.name != t.name or f.parent != t.parent
            ],
        }


# ── String / path helpers ──────────────────────────────────────────────────────

def natural_sort_key(s: Any) -> List:
    return [int(t) if t.isdigit() else t.lower()
            for t in re.split(r'(\d+)', str(s))]


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


# ── Series number zero-padding ─────────────────────────────────────────────────

def pad_sequence(seq: str, width: int) -> str:
    """
    Zero-pad only the integer part of a sequence number to `width` digits.
    "1"   → "01"  (width=2)
    "1.5" → "01.5" (width=2)
    "10"  → "10"  (width=2)
    "001" → "01"  (width=2, re-normalises over-padded values too)
    """
    if not seq:
        return seq
    m = re.match(r'^(\d+)(.*)', seq.strip())
    if not m:
        return seq
    return m.group(1).zfill(width) + m.group(2)


def _seq_int_part(seq: str) -> int:
    m = re.match(r'^(\d+)', seq.strip()) if seq else None
    return int(m.group(1)) if m else 0


def pad_series_numbers(planned_moves: List[BookMove]) -> List[BookMove]:
    """
    Post-processing pass — gives every series consistent zero-padded sequences.

    Groups books by (author, series_name), finds the maximum integer sequence
    number, determines required digit width (min 2), then rebuilds target_dir
    and move_plan destinations for any book whose sequence changes.
    """
    # Build groups: key → list of indices into planned_moves
    groups: Dict[Tuple[str, str], List[int]] = {}
    for i, bm in enumerate(planned_moves):
        if not bm.series_name:
            continue
        key = (bm.author, bm.series_name)
        groups.setdefault(key, []).append(i)

    for key, indices in groups.items():
        seqs    = [planned_moves[i].series_sequence for i in indices]
        max_int = max((_seq_int_part(s) for s in seqs), default=0)
        width   = max(2, len(str(max_int)) if max_int > 0 else 2)

        for i in indices:
            bm      = planned_moves[i]
            old_seq = bm.series_sequence
            new_seq = pad_sequence(old_seq, width)

            if new_seq == old_seq:
                continue

            c_title      = clean_filename(bm.title)
            folder_label = f"{new_seq} {c_title}".strip() if new_seq else c_title
            # target_dir structure: root / author / series / folder_label
            # bm.target_dir.parent.parent is root / author / series
            new_target   = bm.target_dir.parent.parent / bm.series_name / folder_label

            new_plan = [(old_f, new_target / dest.name)
                        for old_f, dest in bm.move_plan]

            planned_moves[i] = BookMove(
                title=bm.title,
                author=bm.author,
                series_name=bm.series_name,
                series_sequence=new_seq,
                old_dir=bm.old_dir,
                target_dir=new_target,
                move_plan=new_plan,
                abs_item_id=bm.abs_item_id,
            )

    return planned_moves


# ── Series / filename parsing ──────────────────────────────────────────────────

def parse_series_info(series_field: str) -> Tuple[str, str]:
    """
    Extract (series_name, raw_sequence) from a legacy 'Name #N' string.
    Zero-padding is applied later by pad_series_numbers().
    """
    if not series_field:
        return "", ""
    parts       = series_field.split("#", 1)
    series_name = clean_filename(parts[0].strip())
    sequence    = parts[1].strip() if len(parts) > 1 else ""
    return series_name, sequence


def build_move_plan(
    old_book_dir: Path,
    target_dir: Path,
    audio_files: List[Path],
    all_items: List[Path],
    c_author: str,
    c_title: str,
) -> List[Tuple[Path, Path]]:
    """Return list of (source_path, dest_path) for every file in the book dir."""
    move_plan: List[Tuple[Path, Path]] = []
    audio_set  = set(audio_files)
    num_audio  = len(audio_files)
    # minimum 2-digit part numbers so Part 01, Part 02 ... sort correctly
    pad_width  = max(2, len(str(num_audio)))

    for idx, old_f in enumerate(audio_files, 1):
        ext = old_f.suffix.lower()
        if num_audio == 1:
            new_name = f"{c_author} - {c_title}{ext}"
        else:
            part_num = str(idx).zfill(pad_width)
            new_name = f"{c_author} - {c_title} - Part {part_num}{ext}"
        move_plan.append((old_f, target_dir / new_name))

    for item in all_items:
        if item not in audio_set and item.is_file():
            move_plan.append((item, target_dir / item.name))

    return move_plan


# ── File-mode scanning ─────────────────────────────────────────────────────────

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
    narrator     = get_metadata_value(data, ['narratorName', 'narrator', 'narrators']) or ""
    series_field = get_metadata_value(data, ['seriesName', 'series']) or ""
    duration_raw = data.get('duration') or data.get('metadata', {}).get('duration') or 0

    stats.authors.add(author)
    if narrator:
        stats.narrators.add(narrator)

    series_title, sequence = parse_series_info(series_field)
    if series_title:
        stats.series.add(series_title)
    else:
        stats.standalone_count += 1

    try:
        stats.total_duration += float(duration_raw)
    except (ValueError, TypeError):
        pass

    c_author = clean_filename(author)
    c_title  = clean_filename(book_title)

    if series_title:
        folder_label = f"{sequence} {c_title}".strip() if sequence else c_title
        target_dir   = root_path / c_author / series_title / folder_label
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

    move_plan = build_move_plan(old_book_dir, target_dir, audio_files, all_items, c_author, c_title)

    needs_changes = (
        old_book_dir.resolve() != target_dir.resolve() or
        any(old.name != new.name for old, new in move_plan)
    )

    if needs_changes:
        return BookMove(
            title=book_title,
            author=author,
            series_name=series_title,
            series_sequence=sequence,
            old_dir=old_book_dir,
            target_dir=target_dir,
            move_plan=move_plan,
        )
    return None


def scan_library(
    root_path: Path,
    emit: Callable[[str], None] = _noop,
) -> Tuple[LibraryStats, List[BookMove]]:
    """File-mode: scan metadata.json files, plan all moves."""
    meta_files  = list(root_path.rglob('metadata.json'))
    total_found = len(meta_files)

    if total_found == 0:
        emit("ERROR: No metadata.json files found. Check the library path.")
        return LibraryStats(), []

    emit(f"Found {total_found} books in library.")
    stats: LibraryStats     = LibraryStats()
    planned_moves: List[BookMove] = []

    for idx, meta_path in enumerate(meta_files, 1):
        if idx % 10 == 0 or idx == total_found:
            emit(f"Analysing: {idx}/{total_found} books…")
        bm = process_metadata_file(meta_path, root_path, stats)
        if bm:
            planned_moves.append(bm)

    planned_moves = pad_series_numbers(planned_moves)
    emit(f"Scan complete. {len(planned_moves)} books need tidying.")
    return stats, planned_moves


# ── ABS-mode scanning ──────────────────────────────────────────────────────────

def scan_library_abs(
    abs_items,      # List[ABSBookItem] from abs_api.py
    root_path: Path,
    emit: Callable[[str], None] = _noop,
) -> Tuple[LibraryStats, List[BookMove]]:
    """ABS mode: use API-provided metadata + filesystem paths to plan moves."""
    stats: LibraryStats     = LibraryStats()
    planned_moves: List[BookMove] = []
    total = len(abs_items)

    emit(f"Planning changes for {total} books…")

    for idx, item in enumerate(abs_items, 1):
        if idx % 10 == 0 or idx == total:
            emit(f"Processing: {idx}/{total}…")

        stats.books += 1
        stats.authors.add(item.author)
        if item.narrator:
            stats.narrators.add(item.narrator)
        if item.series_name:
            stats.series.add(item.series_name)
        else:
            stats.standalone_count += 1
        stats.total_duration += item.duration
        stats.total_size     += item.size

        c_author = clean_filename(item.author)
        c_title  = clean_filename(item.title)
        seq      = item.series_sequence     # raw; padded later

        old_book_dir = Path(item.book_path)

        if item.series_name:
            folder_label = f"{seq} {c_title}".strip() if seq else c_title
            target_dir   = root_path / c_author / item.series_name / folder_label
        else:
            target_dir = root_path / c_author / c_title

        audio_paths = [Path(p) for p in item.audio_files]
        all_paths   = [Path(p) for p in item.all_files]

        # Catch any extra files not listed in libraryFiles
        try:
            if old_book_dir.exists():
                known = {p.resolve() for p in all_paths}
                for f in old_book_dir.iterdir():
                    if f.is_file() and f.resolve() not in known:
                        all_paths.append(f)
        except PermissionError:
            pass

        move_plan = build_move_plan(old_book_dir, target_dir, audio_paths, all_paths, c_author, c_title)

        needs_changes = (
            old_book_dir.resolve() != target_dir.resolve() or
            any(old.name != new.name for old, new in move_plan)
        )

        if needs_changes:
            planned_moves.append(BookMove(
                title=item.title,
                author=item.author,
                series_name=item.series_name,
                series_sequence=seq,
                old_dir=old_book_dir,
                target_dir=target_dir,
                move_plan=move_plan,
                abs_item_id=item.item_id,
            ))

    planned_moves = pad_series_numbers(planned_moves)
    emit(f"Planning complete. {len(planned_moves)} books need tidying.")
    return stats, planned_moves


# ── Execution ──────────────────────────────────────────────────────────────────

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
        else:
            emit(f"  {prefix}Would create dir: {book.target_dir}")

        for old_f, new_f in book.move_plan:
            if not old_f.is_file():
                continue
            if old_f.resolve() == new_f.resolve():
                continue
            if new_f.exists():
                log_event(log_file, f"  {prefix}CONFLICT: {new_f.name}")
                collisions.add(new_f.name)
                emit(f"  CONFLICT: {new_f.name} already exists at target")
                continue
            if dry_run:
                log_event(log_file, f"  {prefix}Would move: {old_f.name} → {new_f.name}")
                emit(f"  {prefix}{old_f.name}  →  {new_f.name}")
            else:
                shutil.move(str(old_f), str(new_f))
                log_event(log_file, f"  MOVED: {old_f.name} → {new_f.name}")

        if not dry_run and book.old_dir.exists():
            try:
                if not any(book.old_dir.iterdir()):
                    shutil.rmtree(book.old_dir)
                    log_event(log_file, f"  CLEANUP: Removed empty dir {book.old_dir.name}")
            except (PermissionError, OSError):
                pass

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
    collisions: Set[str] = set()
    exec_stats = {"applied": 0, "skipped": 0, "errors": 0}
    total  = len(planned_moves)
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

    emit(
        f"{prefix}Done. "
        f"Applied: {exec_stats['applied']}, "
        f"Errors: {exec_stats['errors']}, "
        f"Collisions: {len(collisions)}"
    )
    return exec_stats


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
