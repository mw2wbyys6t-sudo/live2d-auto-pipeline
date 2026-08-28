#!/usr/bin/env python3
"""
Live2D Master Agent - File Utility Functions

Helpers for directory management, filename sanitization, timestamps,
cleanup, hashing, file sizing, and recursive image discovery.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Tuple, Union

from core.logger import get_logger

log = get_logger("utils.file")

PathLike = Union[str, Path]


def ensure_dir(path: PathLike) -> Path:
    """Create a directory (and parents) if it does not already exist.

    Args:
        path: Directory path to create.

    Returns:
        The resolved :class:`Path` object.
    """
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p.resolve()


# Allow ASCII letters/digits, common separators, and Unicode word characters
# (so that Chinese/Japanese/Korean and accented characters are preserved).
_SAFE_NAME_RE = re.compile(r"[^\w.\-]+", re.UNICODE)


def safe_filename(name: str, fallback: str = "untitled") -> str:
    """Sanitize a string for use as a filename across OSes.

    - Strips path separators and control characters.
    - Replaces runs of unsafe chars with underscores.
    - Collapses leading/trailing dots and spaces.

    Args:
        name: Proposed filename.
        fallback: Value returned if ``name`` is empty after sanitization.

    Returns:
        A filesystem-safe filename string.
    """
    if not name:
        return fallback
    # Drop directory components if user passes a path
    base = os.path.basename(name.strip())
    cleaned = _SAFE_NAME_RE.sub("_", base)
    cleaned = cleaned.strip("._ ")
    # Reserve Windows device names
    if cleaned.upper().split(".")[0] in {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        cleaned = f"_{cleaned}"
    if not cleaned:
        return fallback
    # Cap length to be safe across filesystems
    if len(cleaned) > 200:
        stem, ext = os.path.splitext(cleaned)
        cleaned = stem[: 200 - len(ext)] + ext
    return cleaned


def get_timestamp(fmt: str = "%Y%m%d_%H%M%S") -> str:
    """Return a formatted timestamp string for the current local time.

    Args:
        fmt: strftime format string.

    Returns:
        Formatted timestamp string.
    """
    return datetime.now().strftime(fmt)


def cleanup_old_files(directory: PathLike, max_age_days: int = 7) -> int:
    """Remove files under ``directory`` older than ``max_age_days``.

    Subdirectories are traversed recursively. Empty directories left behind
    after cleanup are also pruned.

    Args:
        directory: Root directory to scan.
        max_age_days: Age threshold in days. Files older are deleted.

    Returns:
        Number of files removed.
    """
    root = Path(directory)
    if not root.is_dir():
        log.debug(f"cleanup_old_files: directory does not exist: {root}")
        return 0

    cutoff = time.time() - max_age_days * 86400
    removed = 0
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        dp = Path(dirpath)
        for fname in filenames:
            fp = dp / fname
            try:
                if fp.is_file() and fp.stat().st_mtime < cutoff:
                    fp.unlink()
                    removed += 1
            except OSError as exc:
                log.warning(f"Failed to remove {fp}: {exc}")
        # Prune empty directories (but never the root)
        if dp != root:
            try:
                if not any(dp.iterdir()):
                    dp.rmdir()
            except OSError:
                pass

    if removed:
        log.info(f"Cleaned up {removed} file(s) older than {max_age_days}d in {root}")
    return removed


def get_file_size_mb(path: PathLike) -> float:
    """Return a file's size in megabytes (1 MB = 1024*1024 bytes).

    Args:
        path: Path to the file.

    Returns:
        Size in MB as a float. Raises FileNotFoundError if missing.
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Not a file: {path}")
    return p.stat().st_size / (1024.0 * 1024.0)


def hash_file(path: PathLike, algorithm: str = "sha256", chunk_size: int = 1 << 20) -> str:
    """Compute a hex digest of a file using the given hashlib algorithm.

    Args:
        path: File path.
        algorithm: Hash algorithm name (``"sha256"``, ``"sha1"``, ``"md5"``, ...).
        chunk_size: Read chunk size in bytes (default 1 MiB).

    Returns:
        Hexadecimal digest string.
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Not a file: {path}")
    try:
        h = hashlib.new(algorithm)
    except ValueError as exc:
        raise ValueError(f"Unsupported hash algorithm '{algorithm}': {exc}") from exc
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


_DEFAULT_IMG_EXTS: Tuple[str, ...] = (".png", ".jpg", ".jpeg", ".webp")


def find_images(
    directory: PathLike,
    extensions: Iterable[str] = _DEFAULT_IMG_EXTS,
    recursive: bool = True,
) -> List[str]:
    """Recursively find image files in a directory.

    Args:
        directory: Root directory to search.
        extensions: File extensions to include (case-insensitive).
        recursive: Whether to descend into subdirectories.

    Returns:
        Sorted list of absolute path strings.
    """
    root = Path(directory)
    if not root.is_dir():
        log.debug(f"find_images: directory does not exist: {root}")
        return []
    exts = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in extensions}
    results: List[str] = []
    if recursive:
        walker = root.rglob("*")
    else:
        walker = root.glob("*")
    for fp in walker:
        try:
            if fp.is_file() and fp.suffix.lower() in exts:
                results.append(str(fp.resolve()))
        except OSError:
            continue
    results.sort()
    return results
