"""
ABS Tidy Library – Core Module
All metadata comes from the Audiobookshelf API (ABSBookItem objects).
Naming is driven by a NamingConfig with user-editable token templates.
"""

from __future__ import annotations

import os
import re
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


# ── Naming configuration ───────────────────────────────────────────────────────

@dataclass
class NamingConfig:
    """
    Token-based naming templates, similar to Radarr/Sonarr.

    Available tokens:
      {Author}        – Author name
      {Title}         – Book title
      {Series}        – Series name
      {Series-Index}  – Zero-padded series sequence  e.g. 01, 02, 12
      {Narrator}      – Narrator name
      {Year}          – Publish year (if available)
      {Part-Index}    – Zero-padded part number for multi-file books

    folder_standalone : folder path for books not in a series
    folder_series     : folder path for books in a series
                        (relative to library root — slashes create subfolders)
    file_single       : filename (no extension) when a book has ONE audio file
    file_multi        : filename (no extension) for each part when multi-file
    """
    folder_standalone: str = "{Author}/{Title}"
    folder_series:     str = "{Author}/{Series}/{Series-Index} {Title}"
    file_single:       str = "{Author} - {Title}"
    file_multi:        str = "{Author} - {Title} - Part {Part-Index}"

    # Preset library
    PRESETS: dict = field(default_factory=lambda: {
        "default": {
            "label": "Default",
            "folder_standalone": "{Author}/{Title}",
            "folder_series":     "{Author}/{Series}/{Series-Index} {Title}",
            "file_single":       "{Author} - {Title}",
            "file_multi":        "{Author} - {Title} - Part {Part-Index}",
        },
        "series-first": {
            "label": "Series First",
            "folder_standalone": "{Author}/{Title}",
            "folder_series":     "{Series}/{Series-Index} {Title}",
            "file_single":       "{Author} - {Title}",
            "file_multi":        "{Author} - {Title} - Part {Part-Index}",
        },
        "plex": {
            "label": "Plex-friendly",
            "folder_standalone": "{Author}/{Title}",
            "folder_series":     "{Author}/{Series}/{Series-Index} - {Title}",
            "file_single":       "{Title}",
            "file_multi":        "{Title} - Part {Part-Index}",
        },
        "minimal": {
            "label": "Minimal",
            "folder_standalone": "{Author}/{Title}",
            "folder_series":     "{Author}/{Series}/{Series-Index} {Title}",
            "file_single":       "{Title}",
            "file_multi":        "{Title} - Part {Part-Index}",
        },
        "series-in-filename": {
            "label": "Series in Filename",
            "folder_standalone": "{Author}/{Title}",
            "folder_series":     "{Author}/{Series}/{Series-Index} {Title}",
            "file_single":       "{Author} - {Series} {Series-Index} - {Title}",
            "file_multi":        "{Author} - {Series} {Series-Index} - {Title} - Part {Part-Index}",
        },
    }, init=False, repr=False, compare=False)

    def to_dict(self) -> dict:
        return {
            "folder_standalone": self.folder_standalone,
            "folder_series":     self.folder_series,
            "file_single":       self.file_single,
            "file_multi":        self.file_multi,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "NamingConfig":
        return cls(
            folder_standalone = d.get("folder_standalone", cls.__dataclass_fields__["folder_standalone"].default),
            folder_series     = d.get("folder_series",     cls.__dataclass_fields__["folder_series"].default),
            file_single       = d.get("file_single",       cls.__dataclass_fields__["file_single"].default),
            file_multi        = d.get("file_multi",        cls.__dataclass_fields__["file_multi"].default),
        )

    @classmethod
    def from_env(cls) -> "NamingConfig":
        return cls(
            folder_standalone = os.environ.get("NAMING_FOLDER_STANDALONE", cls.__dataclass_fields__["folder_standalone"].default),
            folder_series     = os.environ.get("NAMING_FOLDER_SERIES",     cls.__dataclass_fields__["folder_series"].default),
            file_single       = os.environ.get("NAMING_FILE_SINGLE",       cls.__dataclass_fields__["file_single"].default),
            file_multi        = os.environ.get("NAMING_FILE_MULTI",        cls.__dataclass_fields__["file_multi"].default),
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
    # Clean up any double-spaces or trailing spaces per path segment
    parts = result.split("/")
    parts = [re.sub(r'\s+', ' ', p).strip() for p in parts]
    parts = [p for p in parts if p]  # drop empty segments
    return "/".join(parts)


def make_tokens(
    author: str,
    title: str,
    series_name: str,
    series_sequence: str,
    narrator: str = "",
    year: str = "",
) -> Dict[str, str]:
    return {
        "Author":       author,
        "Title":        title,
        "Series":       series_name,
        "Series-Index": series_sequence,
        "Narrator":     narrator,
        "Year":         year,
    }


# ── Series zero-padding ────────────────────────────────────────────────────────

def pad_sequence(seq: str, width: int) -> str:
    if not seq:
        return seq
    m = re.match(r'^(\d+)(.*)', seq.strip())
    if not m:
        return seq
    return m.group(1).zfill(width) + m.group(2)


def _seq_int_part(seq: str) -> int:
    m = re.match(r'^(\d+)', seq.strip()) if seq else None
    return int(m.group(1)) if m else 0


def pad_series_numbers(
    planned_moves: List[BookMove],
    naming: NamingConfig,
    root_path: Path,
) -> List[BookMove]:
    """
    Ensure consistent zero-padding for all books in the same series,
    then rebuild target_dir and move_plan using the naming template.
    """
    groups: Dict[Tuple[str, str], List[int]] = {}
    for i, bm in enumerate(planned_moves):
        if not bm.series_name:
            continue
        groups.setdefault((bm.author, bm.series_name), []).append(i)

    for (author, series_name), indices in groups.items():
        seqs    = [planned_moves[i].series_sequence for i in indices]
        max_int = max((_seq_int_part(s) for s in seqs), default=0)
        width   = max(2, len(str(max_int)) if max_int > 0 else 2)

        for i in indices:
            bm      = planned_moves[i]
            old_seq = bm.series_sequence
            new_seq = pad_sequence(old_seq, width)

            if new_seq == old_seq:
                continue

            # Rebuild target_dir with padded sequence via naming template
            tokens = make_tokens(bm.author, bm.title, bm.series_name, new_seq)
            folder_rel = render_template(naming.folder_series, tokens)
            new_target = root_path / Path(folder_rel)
            new_plan   = [(old_f, new_target / dest.name)
                          for old_f, dest in bm.move_plan]

            planned_moves[i] = BookMove(
                title=bm.title, author=bm.author,
                series_name=bm.series_name, series_sequence=new_seq,
                old_dir=bm.old_dir, target_dir=new_target,
                move_plan=new_plan, abs_item_id=bm.abs_item_id,
            )

    return planned_moves


# ── Move plan builder ──────────────────────────────────────────────────────────

def build_move_plan(
    old_book_dir: Path,
    target_dir: Path,
    audio_files: List[Path],
    all_items: List[Path],
    tokens: Dict[str, str],
    naming: NamingConfig,
) -> List[Tuple[Path, Path]]:
    move_plan: List[Tuple[Path, Path]] = []
    audio_set = set(audio_files)
    num_audio = len(audio_files)
    pad_width = max(2, len(str(num_audio)))

    for idx, old_f in enumerate(audio_files, 1):
        ext = old_f.suffix.lower()
        if num_audio == 1:
            stem = render_template(naming.file_single, tokens)
        else:
            part_tokens = {**tokens, "Part-Index": str(idx).zfill(pad_width)}
            stem = render_template(naming.file_multi, part_tokens)
        move_plan.append((old_f, target_dir / f"{stem}{ext}"))

    for item in all_items:
        if item not in audio_set and item.is_file():
            move_plan.append((item, target_dir / item.name))

    return move_plan


# ── ABS-mode scan ──────────────────────────────────────────────────────────────

def scan_library_abs(
    abs_items,
    root_path: Path,
    naming: NamingConfig,
    emit: Callable[[str], None] = _noop,
    abs_library_root: str = "",
) -> Tuple[LibraryStats, List[BookMove]]:
    """
    Build move plans for all ABS items using API metadata and naming templates.
    No metadata.json files are read — all data comes from the ABS API.

    abs_library_root: the path ABS uses internally (e.g. /media/Audiobooks).
    root_path:        where those same files are mounted in this container.
    Book paths from ABS are remapped so comparisons happen in container space.
    """
    stats: LibraryStats       = LibraryStats()
    planned_moves: List[BookMove] = []
    total = len(abs_items)
    abs_root = Path(abs_library_root) if abs_library_root else None

    emit(f"Planning changes for {total} books using ABS API metadata…")

    for idx, item in enumerate(abs_items, 1):
        if idx % 10 == 0 or idx == total:
            emit(f"Processing: {idx}/{total}…")

        # ── Stats ──────────────────────────────────────────────────────────────
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

        # ── Build target directory via naming template ──────────────────────────
        tokens = make_tokens(
            author          = item.author,
            title           = item.title,
            series_name     = item.series_name,
            series_sequence = item.series_sequence,
            narrator        = item.narrator,
            year            = item.year,
        )

        if item.series_name:
            folder_rel = render_template(naming.folder_series, tokens)
        else:
            folder_rel = render_template(naming.folder_standalone, tokens)

        # ── Remap book_path from ABS space into container space ────────────────
        # ABS stores paths like /media/Audiobooks/Author/Book
        # Our container mounts the same files at /library/Author/Book
        # We strip the ABS root prefix and replace with our root_path so that
        # all path comparisons happen in the same coordinate space.
        abs_book_path = Path(item.book_path)
        if abs_root:
            try:
                rel_from_abs = abs_book_path.relative_to(abs_root)
                old_book_dir = root_path / rel_from_abs
            except ValueError:
                # book_path is not under abs_library_root — use as-is
                old_book_dir = abs_book_path
        else:
            old_book_dir = abs_book_path

        target_dir = root_path / Path(folder_rel)

        # ── Remap audio/all file paths the same way ────────────────────────────
        def remap(p_str: str) -> Path:
            p = Path(p_str)
            if abs_root:
                try:
                    return root_path / p.relative_to(abs_root)
                except ValueError:
                    pass
            return p

        # ── Gather files ───────────────────────────────────────────────────────
        audio_paths = [remap(p) for p in item.audio_files]
        all_paths   = [remap(p) for p in item.all_files]

        try:
            if old_book_dir.exists():
                known = {p.resolve() for p in all_paths}
                for f in old_book_dir.iterdir():
                    if f.is_file() and f.resolve() not in known:
                        all_paths.append(f)
        except PermissionError:
            pass

        move_plan = build_move_plan(
            old_book_dir, target_dir, audio_paths, all_paths, tokens, naming
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

    # After padding, some books may no longer need changes (their pre-padding
    # sequence "2" triggered inclusion, but after padding to "02" the path
    # matches what already exists on disk). Filter them out now.
    def still_needs_move(bm: BookMove) -> bool:
        folder_diff = bm.old_dir.resolve() != bm.target_dir.resolve()
        file_diff   = any(old_f.name != new_f.name for old_f, new_f in bm.move_plan)
        return folder_diff or file_diff

    before = len(planned_moves)
    planned_moves = [bm for bm in planned_moves if still_needs_move(bm)]
    filtered = before - len(planned_moves)
    if filtered:
        emit(f"  (Filtered {filtered} books already correctly named after sequence padding.)")

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
            emit(f"  {prefix}Would create: {book.target_dir}")

        for old_f, new_f in book.move_plan:
            if not old_f.is_file():
                continue
            if old_f.resolve() == new_f.resolve():
                continue
            if new_f.exists():
                log_event(log_file, f"  {prefix}CONFLICT: {new_f.name}")
                collisions.add(new_f.name)
                emit(f"  CONFLICT: {new_f.name} already at target")
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
                    log_event(log_file, f"  CLEANUP: Removed {book.old_dir.name}")
            except (PermissionError, OSError):
                pass

        return True
    except Exception as e:
        log_event(log_file, f"!!! ERROR '{book.title}': {e}")
        emit(f"ERROR '{book.title}': {e}")
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
        emit(f"Collisions ({len(collisions)}): " + ", ".join(sorted(collisions)))
    emit(f"{prefix}Done. Applied: {exec_stats['applied']}, Errors: {exec_stats['errors']}, Collisions: {len(collisions)}")
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
