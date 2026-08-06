"""Turn source zips of roll PDFs into page images for extraction.

The source PDFs carry no text layer -- each page is a single embedded 1187x1679 RGB
image at 144 dpi. ``pdfimages`` pulls that image out **byte-exact**, with no
resampling; rendering with ``pdftoppm`` at a higher dpi would only upsample a 144 dpi
scan, inflating token cost for no added information.

Extraction uses the **full** page. An earlier pass cropped to the top 74% (the four
form sections), but that clips the footer where ``total_pages`` lives, and a part with
a long area list could push content past the cut. The full page costs ~700 more image
tokens -- tens of dollars across the whole state -- which is not worth trading against
silent data loss. ``form_crop`` survives for review thumbnails only.
"""

from __future__ import annotations

import hashlib
import io
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Optional

from PIL import Image

from .schema import parse_source_filename

#: Native page geometry of the 2026 Assam info pages, used as a sanity check.
EXPECTED_PAGE_SIZE = (1187, 1679)

#: Fraction of page height holding the four form sections. Thumbnails only.
FORM_CROP_FRACTION = 0.74

PAGE_FORM = 1
PAGE_IMAGERY = 2


class RenderError(RuntimeError):
    """Raised when a PDF cannot be turned into a page image."""


@dataclass(frozen=True)
class PartRef:
    """One part: where it came from and which AC/part the filename claims it is."""

    zip_name: str
    pdf_name: str
    ac_no: int
    part_no: int

    @property
    def key(self) -> str:
        return f"{self.ac_no:03d}-{self.part_no:04d}"


def require_poppler() -> None:
    """Fail loudly and early if poppler is missing, rather than per-PDF."""
    if shutil.which("pdfimages") is None:
        raise RenderError(
            "pdfimages not found. Install poppler "
            "(macOS: brew install poppler; Debian: apt-get install poppler-utils)."
        )


def iter_zip_parts(zip_path: Path) -> Iterator[PartRef]:
    """Yield a ``PartRef`` for every recognizable roll PDF in ``zip_path``.

    Entries whose names do not match the publisher's pattern are skipped -- callers
    that care can compare counts against ``zipfile.namelist()``.
    """
    refs: List[PartRef] = []
    with zipfile.ZipFile(zip_path) as archive:
        for name in archive.namelist():
            if not name.lower().endswith(".pdf"):
                continue
            parsed = parse_source_filename(Path(name).name)
            if parsed is None:
                continue
            refs.append(
                PartRef(
                    zip_name=zip_path.name,
                    pdf_name=name,
                    ac_no=parsed["ac_no"],
                    part_no=parsed["part_no"],
                )
            )
    # Numeric order, not lexicographic: part 99 must not sort after part 154.
    yield from sorted(refs, key=lambda ref: (ref.ac_no, ref.part_no))


def unrecognized_pdfs(zip_path: Path) -> List[str]:
    """PDF entries whose filenames do not match the publisher's pattern.

    A non-empty result means the archive holds something the pipeline would silently
    skip, so callers should surface it rather than ignore it.
    """
    with zipfile.ZipFile(zip_path) as archive:
        return sorted(
            name
            for name in archive.namelist()
            if name.lower().endswith(".pdf") and parse_source_filename(Path(name).name) is None
        )


def read_pdf_bytes(zip_path: Path, pdf_name: str) -> bytes:
    """Read one PDF out of a zip without extracting the whole archive to disk."""
    with zipfile.ZipFile(zip_path) as archive:
        return archive.read(pdf_name)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def extract_page_image(pdf_bytes: bytes, page: int = PAGE_FORM) -> Image.Image:
    """Return the embedded image for ``page`` byte-exact, via ``pdfimages``.

    Falls back to rasterizing with ``pdftoppm`` when a page holds something other than
    a single full-page image, so an unexpected PDF degrades rather than crashes.
    """
    require_poppler()
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        pdf_path = tmp / "part.pdf"
        pdf_path.write_bytes(pdf_bytes)

        result = subprocess.run(
            [
                "pdfimages",
                "-png",
                "-f",
                str(page),
                "-l",
                str(page),
                str(pdf_path),
                str(tmp / "img"),
            ],
            capture_output=True,
            text=True,
        )
        images = sorted(tmp.glob("img-*.png"))
        if result.returncode == 0 and len(images) == 1:
            with Image.open(images[0]) as handle:
                return normalize_page(handle.convert("RGB").copy())

        # Zero or several embedded images: rasterize at the native 144 dpi instead.
        rasterized = _rasterize_page(pdf_path, tmp, page)
        if rasterized is not None:
            return normalize_page(rasterized)

        raise RenderError(
            f"could not extract page {page}: pdfimages rc={result.returncode}, "
            f"{len(images)} image(s) found. stderr={result.stderr.strip()[:200]}"
        )


#: Resampling filter for the rescale. **Not** a cosmetic choice: measured over 30 of the
#: affected pages, LANCZOS read ``start_serial`` correctly on **0 of 30** while NEAREST got
#: **30 of 30**. Its ringing thickens the thin "1" glyph enough that Tesseract calls it a
#: "4" -- and because the elector sum does not involve the start serial, the error passed
#: every check. LANCZOS balanced 30/30 while being wrong on every one.
#:
#: An interpolating filter invents intermediate pixels; on a 144 dpi scan of printed text
#: that invention is indistinguishable from ink. NEAREST adds nothing that was not there.
RESAMPLING = Image.Resampling.NEAREST

#: The page size every downstream pixel constant is expressed in -- the rule positions in
#: ``layout``, the value-column offsets in the language profiles, the column defaults.
CANONICAL_PAGE_SIZE = (1187, 1679)


def normalize_page(image: Image.Image) -> Image.Image:
    """Scale a page to the canonical size, if the publisher issued it at another.

    Most constituencies embed a 1187x1679 image at 144 dpi, but **ACs 64-73 are issued at
    949x1343** -- a uniform 0.80x, about 115 dpi. Nothing else about them differs: the same
    form, the same generator, the same rules in the same *relative* places.

    Every pixel constant downstream is expressed against the canonical size, so rather than
    scaling all of them per page (rule positions, frame bounds, six column defaults, the
    per-language value offsets, the label search width), the page is brought to the size
    they already assume. One conversion instead of a dozen, and it keeps a single set of
    measured constants rather than a set plus a scaling rule.

    Left unhandled, these pages failed grid detection outright -- 1,554 flagged rows before
    the block was even finished. A safe failure, but a whole-constituency hole.

    See ``RESAMPLING`` for why the filter matters as much as the rescale itself.
    """
    if image.size == CANONICAL_PAGE_SIZE:
        return image
    return image.resize(CANONICAL_PAGE_SIZE, RESAMPLING)


def _rasterize_page(pdf_path: Path, workdir: Path, page: int) -> Optional[Image.Image]:
    """Rasterize a page at the source's native 144 dpi as a fallback."""
    result = subprocess.run(
        [
            "pdftoppm",
            "-r",
            "144",
            "-png",
            "-f",
            str(page),
            "-l",
            str(page),
            str(pdf_path),
            str(workdir / "page"),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    pages = sorted(workdir.glob("page-*.png"))
    if not pages:
        return None
    with Image.open(pages[0]) as handle:
        return handle.convert("RGB").copy()


def form_crop(image: Image.Image, fraction: float = FORM_CROP_FRACTION) -> Image.Image:
    """Crop to the form sections. For review thumbnails, not for extraction."""
    width, height = image.size
    return image.crop((0, 0, width, int(round(height * fraction))))


def encode_png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def render_zip(
    zip_path: Path,
    out_dir: Path,
    page: int = PAGE_FORM,
    limit: Optional[int] = None,
    overwrite: bool = False,
) -> List[PartRef]:
    """Write one PNG per part into ``out_dir``, skipping work already done.

    Returns the parts rendered or already present, so the caller can drive extraction
    off the same list. Resumable: an interrupted run picks up where it stopped.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    rendered: List[PartRef] = []

    for ref in iter_zip_parts(zip_path):
        if limit is not None and len(rendered) >= limit:
            break
        target = out_dir / f"{ref.key}.png"
        if target.exists() and not overwrite:
            rendered.append(ref)
            continue
        pdf_bytes = read_pdf_bytes(zip_path, ref.pdf_name)
        image = extract_page_image(pdf_bytes, page=page)
        target.write_bytes(encode_png(image))
        rendered.append(ref)

    return rendered
