"""Writing the shards, and the manifest that makes them checkable from the repo.

One Parquet file per assembly constituency. Tens of millions of elector rows will not live in a
repository whose published dataset is 12 MB, and a single combined file could not be built
incrementally as zips arrive -- but a shard is exactly one downloadable unit of source data,
so the boundary matches how the corpus actually arrives.

The shards are not committed. What is committed is `dataset/electors_manifest.json`: per
shard, the row count, the SHA-256, and the reconciliation against the part totals published
on the info pages. That is enough for someone with the repo alone to know what exists, how
big it is, whether their copy is intact, and how far to trust it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

import pyarrow as pa
import pyarrow.parquet as pq

from .extract import COLUMNS

SHARD_DIR = Path("out/electors")
MANIFEST = Path("dataset/electors_manifest.json")

#: Written explicitly rather than inferred. Inference makes ``age`` an int in one shard and a
#: float in the next as soon as one part fails to read a single age, and the shards then
#: refuse to concatenate.
SCHEMA = pa.schema(
    [
        ("ac_no", pa.int32()),
        ("part_no", pa.int32()),
        ("serial_no", pa.int32()),
        ("epic_no", pa.string()),
        ("name", pa.string()),
        ("relation_name", pa.string()),
        ("relation_type", pa.string()),
        ("house_no", pa.string()),
        ("age", pa.int32()),
        ("sex", pa.string()),
        # Struck off the roll. Counted in the part's printed total, so a row count is not an
        # elector count until these are excluded.
        ("deleted", pa.bool_()),
        ("status_code", pa.string()),
        ("lang", pa.string()),
        ("roll_section", pa.string()),
        ("page_no", pa.int32()),
        ("box_row", pa.int32()),
        ("box_col", pa.int32()),
        ("serial_no_ocr", pa.int32()),
        ("flags", pa.string()),
        ("needs_review", pa.bool_()),
        ("source_zip", pa.string()),
        ("source_pdf", pa.string()),
        ("pdf_sha256", pa.string()),
        ("roll_type", pa.string()),
        ("revision", pa.int32()),
        ("year", pa.int32()),
        ("engine", pa.string()),
        ("pipeline_version", pa.string()),
        ("extracted_at", pa.string()),
    ]
)


def shard_path(ac_no: int, directory: Path = SHARD_DIR) -> Path:
    return directory / f"AC{ac_no:03d}.parquet"


def write_shard(rows: Sequence[Dict[str, Any]], ac_no: int, directory: Path = SHARD_DIR) -> Path:
    """Write one AC's electors, atomically."""
    directory.mkdir(parents=True, exist_ok=True)
    columns = {name: [row.get(name) for row in rows] for name in COLUMNS}
    table = pa.Table.from_pydict(columns, schema=SCHEMA)
    path = shard_path(ac_no, directory)
    temporary = path.with_suffix(".parquet.tmp")
    pq.write_table(table, temporary, compression="zstd")
    temporary.replace(path)
    return path


def read_shard(path: Path) -> List[Dict[str, Any]]:
    return pq.read_table(path).to_pylist()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def entry_path(ac_no: int, directory: Path = SHARD_DIR) -> Path:
    return directory / f"AC{ac_no:03d}.entry.json"


def write_entry(
    ac_no: int, path: Path, reconciliation: Dict[str, Any], directory: Path = SHARD_DIR
) -> Path:
    """One constituency's record, in its own file.

    A single manifest is read-modify-write, which is safe for one machine and lossy for four:
    each worker rsyncs its own copy of the file back to the bucket and the last writer wins,
    so constituencies disappear from the record without anything failing. Per-AC entries have
    no shared mutable state, so concurrency cannot lose one.

    :func:`merge_entries` rebuilds the combined manifest from them for publishing.
    """
    directory.mkdir(parents=True, exist_ok=True)
    entry = {
        "file": path.name,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        **reconciliation,
    }
    target = entry_path(ac_no, directory)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(entry, indent=1) + "\n", encoding="utf-8")
    temporary.replace(target)
    return target


def merge_entries(directory: Path = SHARD_DIR) -> Dict[str, Any]:
    """Every constituency's record, gathered from the per-AC files."""
    out: Dict[str, Any] = {}
    for found in sorted(directory.glob("AC*.entry.json")):
        ac_no = int(found.name[2:5])
        out[str(ac_no)] = json.loads(found.read_text(encoding="utf-8"))
    return dict(sorted(out.items(), key=lambda kv: int(kv[0])))


def update_manifest(
    ac_no: int,
    path: Path,
    reconciliation: Dict[str, Any],
    manifest: Path = MANIFEST,
) -> Dict[str, Any]:
    """Record one shard in the committed manifest, replacing any earlier entry for that AC."""
    entries: Dict[str, Any] = {}
    if manifest.exists():
        entries = json.loads(manifest.read_text(encoding="utf-8"))
    entries[str(ac_no)] = {
        "file": path.name,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        **reconciliation,
    }
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(dict(sorted(entries.items(), key=lambda kv: int(kv[0]))), indent=2) + "\n",
        encoding="utf-8",
    )
    return entries[str(ac_no)]


def sample(rows: Iterable[Dict[str, Any]], limit: int = 200) -> List[Dict[str, Any]]:
    """A small committed extract, so the schema is inspectable without the shards."""
    return [dict(row) for _, row in zip(range(limit), rows)]
