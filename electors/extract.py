"""One part PDF in, one list of electors out.

The unit of work is the **part**, not the page, for two reasons that both come from the data
rather than from convenience. Serial numbers run consecutively through a part and restart at
the next one, so numbering can only be assigned with the whole part in view. And each part
has a published elector total on its info page, so the part is also the smallest unit that
can be *checked* -- which makes it the right thing to cache, retry and resume.

Rendering is the expensive half. ``pdftoppm`` costs about a second a page at 400 dpi, and a
part is thirty-odd pages, so pages are rasterized once in a single invocation and read from
disk rather than re-rendered per field.
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image

from assam_rolls import ocr, render, schema

from . import fields, grid, pages

#: Everything downstream is calibrated to this. The boxes are ~1000px wide here, which is
#: where the Assamese conjuncts in a name become legible; at 200 dpi they are not.
DPI = 400

PIPELINE_VERSION = "1.0.0"


@dataclass
class PartResult:
    """Every elector in one part, with what the run could and could not establish."""

    ac_no: int
    part_no: int
    lang: str
    source_zip: str
    source_pdf: str
    pdf_sha256: str
    electors: List[Dict[str, Any]] = field(default_factory=list)
    page_count: int = 0
    elector_pages: int = 0
    unknown_pages: List[int] = field(default_factory=list)
    error: str = ""

    @property
    def count(self) -> int:
        return len(self.electors)


def _render_pages(pdf_bytes: bytes, workdir: Path) -> List[Path]:
    """Rasterize the whole PDF once. Returns page images in page order."""
    pdf = workdir / "part.pdf"
    pdf.write_bytes(pdf_bytes)
    subprocess.run(
        ["pdftoppm", "-r", str(DPI), "-png", str(pdf), str(workdir / "page")],
        check=True,
        capture_output=True,
    )
    return sorted(workdir.glob("page-*.png"), key=lambda p: int(p.stem.split("-")[-1]))


def read_part(
    zip_path: Path,
    pdf_name: str,
    engine: Optional[Any] = None,
) -> PartResult:
    """Extract every elector from one part PDF."""
    meta = schema.parse_source_filename(pdf_name) or {}
    pdf_bytes = render.read_pdf_bytes(zip_path, pdf_name)
    result = PartResult(
        ac_no=meta.get("ac_no", 0),
        part_no=meta.get("part_no", 0),
        lang=meta.get("lang", ""),
        source_zip=zip_path.name,
        source_pdf=pdf_name,
        pdf_sha256=render.sha256_bytes(pdf_bytes),
    )
    engine = engine or ocr.get_engine("tesseract", lang="asm")

    try:
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            page_paths = _render_pages(pdf_bytes, workdir)
            result.page_count = len(page_paths)
            serial = 0

            for index, path in enumerate(page_paths, start=1):
                image = Image.open(path)
                signature = pages.classify(image, index)
                if signature.kind is pages.PageKind.UNKNOWN:
                    result.unknown_pages.append(index)
                    continue
                if not signature.is_elector:
                    continue

                boxes = grid.build(signature.h_rules, signature.v_rules)
                if not boxes:
                    # Classified as an elector page but its geometry did not resolve. Recorded
                    # rather than parsed: guessing thirty box positions here would produce
                    # thirty plausible rows of nothing.
                    result.unknown_pages.append(index)
                    continue
                result.elector_pages += 1

                for box in boxes:
                    elector = fields.read_box(engine, image, box)
                    if elector.is_empty:
                        continue
                    serial += 1
                    elector.serial_no = serial
                    # The OCR'd serial is *recorded*, not used to flag. It disagreed on 68%
                    # of the first run's rows -- because it is a small number alone in a wide
                    # strip, not because the derived sequence was wrong -- and flagging it
                    # pushed needs_review to 95%, drowning every signal that mattered.
                    result.electors.append(_row(result, elector, page=index, box=box, meta=meta))
                image.close()
    except (subprocess.CalledProcessError, OSError, ocr.OCRError) as exc:
        result.error = f"{type(exc).__name__}: {exc}"
    return result


def _row(
    result: PartResult,
    elector: fields.Elector,
    page: int,
    box: grid.Box,
    meta: Dict[str, Any],
) -> Dict[str, Any]:
    """One output row: the elector, where it came from, and how it was read."""
    row = asdict(elector)
    row["flags"] = ",".join(elector.flags)
    row.update(
        {
            "ac_no": result.ac_no,
            "part_no": result.part_no,
            "lang": result.lang,
            "page_no": page,
            "box_row": box.row,
            "box_col": box.column,
            "needs_review": elector.needs_review,
            "source_zip": result.source_zip,
            "source_pdf": result.source_pdf,
            "pdf_sha256": result.pdf_sha256,
            "roll_type": meta.get("roll_type", ""),
            "revision": meta.get("revision"),
            "year": meta.get("year"),
            "engine": "tesseract",
            "pipeline_version": PIPELINE_VERSION,
            "extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    )
    return row


#: Column order for the Parquet shards. Identity first, then content, then provenance --
#: the same shape as ``dataset/parts.jsonl.gz`` so the two read alike.
COLUMNS = [
    "ac_no",
    "part_no",
    "serial_no",
    "epic_no",
    "name",
    "relation_name",
    "relation_type",
    "house_no",
    "age",
    "sex",
    "lang",
    "page_no",
    "box_row",
    "box_col",
    "serial_no_ocr",
    "flags",
    "needs_review",
    "source_zip",
    "source_pdf",
    "pdf_sha256",
    "roll_type",
    "revision",
    "year",
    "engine",
    "pipeline_version",
    "extracted_at",
]
