"""Per-part extraction cache, so a run can be interrupted and resumed.

A statewide run is ~28,000 pages over hours. Losing that to a laptop sleeping, a full
disk, or Ctrl-C is not acceptable, so each part's result is written as soon as it is
produced and a later run skips what is already done.

Two properties make the cache trustworthy rather than merely present:

**Writes are atomic.** The result goes to a temporary file in the same directory and is
then ``os.replace``d into place, which is atomic on POSIX. A process killed mid-write
leaves the temporary file, never a truncated JSON that would later parse as valid but
incomplete.

**Entries are keyed to the source bytes.** Each entry records the ``pdf_sha256`` it was
produced from. If the publisher re-issues a part, its hash changes and the entry is
treated as stale and re-extracted -- resumption never silently serves data derived from
a file that no longer exists.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from .log import get_logger
from .schema import PIPELINE_VERSION

logger = get_logger(__name__)


def cache_path(cache_dir: Path, key: str) -> Path:
    return cache_dir / f"{key}.json"


def write_entry(
    cache_dir: Path,
    key: str,
    row: Dict[str, Any],
    sections: Any,
    pdf_sha256: str,
) -> Path:
    """Write one part's result atomically."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_path(cache_dir, key)
    payload = {
        "key": key,
        "pdf_sha256": pdf_sha256,
        "pipeline_version": PIPELINE_VERSION,
        "row": row,
        "sections": sections,
    }
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(cache_dir), prefix=f".{key}.", suffix=".tmp", delete=False
    )
    try:
        json.dump(payload, handle, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        os.replace(handle.name, target)
    except BaseException:
        handle.close()
        Path(handle.name).unlink(missing_ok=True)
        raise
    return target


def read_entry(cache_dir: Path, key: str) -> Optional[Dict[str, Any]]:
    """Read one entry, or ``None`` if absent or unreadable.

    A corrupt entry is reported and treated as missing rather than raised: one bad file
    should cost a re-extraction, not the whole run.
    """
    path = cache_path(cache_dir, key)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        logger.warning("%s: unreadable cache entry (%s); will re-extract", key, exc)
        return None


def is_fresh(entry: Optional[Dict[str, Any]], pdf_sha256: str) -> bool:
    """Whether a cached entry may be reused for the current source bytes."""
    if not entry:
        return False
    if entry.get("pdf_sha256") != pdf_sha256:
        return False
    return entry.get("pipeline_version") == PIPELINE_VERSION


def clear(cache_dir: Path) -> int:
    """Delete every entry; returns how many were removed."""
    if not cache_dir.exists():
        return 0
    removed = 0
    for path in cache_dir.glob("*.json"):
        path.unlink()
        removed += 1
    logger.info("cleared %d cache entries from %s", removed, cache_dir)
    return removed
