"""Package the built dataset for publication.

Compresses the files in ``out/`` into ``dataset/`` and **verifies each archive
decompresses byte-identical to its source** before finishing. A packaging step that
silently truncated an archive would be indistinguishable from a smaller dataset, so it is
checked rather than assumed.

Gzip rather than raw: ``parts.jsonl`` is 93 MB, within 7% of GitHub's 100 MB hard limit
and over its 50 MB warning, and git keeps every version forever -- each regeneration would
add another 93 MB permanently. Compressed it is under 6 MB, and readers pay nothing for
it: pandas reads ``.csv.gz`` natively and ``gzip.open`` handles ``.jsonl.gz`` in one line.

``report.json`` ships uncompressed. It is under a kilobyte and worth being able to read in
a diff.

    .venv/bin/python scripts/package_dataset.py
"""

from __future__ import annotations

import gzip
import hashlib
import shutil
import sys
from pathlib import Path

OUT = Path("out")
DATASET = Path("dataset")

#: Compressed on the way out. Level 9 -- this is written once and read many times.
COMPRESS = ("parts.jsonl", "parts.csv", "part_sections.csv")

#: Copied verbatim: small enough to read in a diff.
VERBATIM = ("report.json",)

COMPRESSION_LEVEL = 9


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _compress(source: Path, target: Path) -> None:
    with source.open("rb") as raw, gzip.open(target, "wb", compresslevel=COMPRESSION_LEVEL) as gz:
        shutil.copyfileobj(raw, gz)


def _decompresses_identically(source: Path, archive: Path) -> bool:
    """Compare the archive's contents to the source a chunk at a time.

    Streamed rather than read whole: the largest file is 93 MB, and holding two copies in
    memory to check a compression step would be a silly way to run out of it.
    """
    with source.open("rb") as raw, gzip.open(archive, "rb") as gz:
        while True:
            a, b = raw.read(1 << 20), gz.read(1 << 20)
            if a != b:
                return False
            if not a:
                return True


def main() -> int:
    missing = [name for name in (*COMPRESS, *VERBATIM) if not (OUT / name).exists()]
    if missing:
        print(f"missing from {OUT}/: {missing}", file=sys.stderr)
        print("run `assam-rolls build` first", file=sys.stderr)
        return 1

    DATASET.mkdir(exist_ok=True)
    published: list[Path] = []

    for name in COMPRESS:
        source, archive = OUT / name, DATASET / f"{name}.gz"
        _compress(source, archive)
        if not _decompresses_identically(source, archive):
            print(f"FAIL: {archive} does not decompress to {source}", file=sys.stderr)
            return 1
        raw_mb, gz_mb = source.stat().st_size / 1e6, archive.stat().st_size / 1e6
        print(f"  {name:22s} {raw_mb:>7.1f} MB -> {gz_mb:>5.1f} MB  ({1 - gz_mb / raw_mb:.0%} smaller), verified")
        published.append(archive)

    for name in VERBATIM:
        target = DATASET / name
        shutil.copyfile(OUT / name, target)
        print(f"  {name:22s} {target.stat().st_size:>7,d} B   copied verbatim")
        published.append(target)

    checksums = DATASET / "SHA256SUMS"
    checksums.write_text(
        "".join(f"{_sha256(p)}  {p.name}\n" for p in sorted(published)), encoding="utf-8"
    )
    print(f"  {'SHA256SUMS':22s} {len(published)} entries")

    total = sum(p.stat().st_size for p in DATASET.iterdir()) / 1e6
    print(f"\n{DATASET}/ is {total:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
