"""Many crops, one tesseract invocation.

A page holds thirty boxes and each box has four text lines, so reading a page a crop at a
time costs about 130 process spawns -- 8,700 for a part, 1.3M for one constituency. Startup
dominates: measured on a real page, the same crops cost **0.512s each** one at a time and
**0.168s each** fed to a single invocation as a file list. Three times faster, and the gap
widens as the machine gets busier, which is exactly when it matters.

**The hazard is alignment, and it is worth being paranoid about.** Tesseract writes one
form-feed-separated page per input image, so outputs are matched to inputs *by position*. If
a blank crop ever produced no page, every crop after it would shift by one and electors would
be given each other's names -- a corruption that no field-level check would catch, because
every value would still look perfectly plausible.

Measured: a blank crop does emit its (empty) page, and 118 real crops return 118 chunks. But
"measured once" is not a guarantee across tesseract versions, so the count is **asserted on
every call** and a mismatch falls back to reading that batch one crop at a time. The fallback
is slower and always correct; the fast path is only ever taken when it demonstrably lines up.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional, Sequence

from PIL import Image

from assam_rolls.ocr import OCRError

#: Tesseract separates the output of each input image with a form feed.
PAGE_BREAK = "\x0c"

#: Beyond this many crops per invocation the command line and memory stop being free, and a
#: failure costs more to redo. A page is ~130 crops, so this batches a page at a time.
MAX_BATCH = 256


def _write(images: Sequence[Image.Image], scale: int, workdir: Path) -> List[Path]:
    paths: List[Path] = []
    for index, image in enumerate(images):
        grey = image.convert("L")
        if scale != 1:
            grey = grey.resize((grey.width * scale, grey.height * scale), Image.Resampling.LANCZOS)
        path = workdir / f"crop_{index:05d}.png"
        grey.save(path)
        paths.append(path)
    return paths


def _invoke(target: str, lang: str, psm: int, whitelist: Optional[str]) -> str:
    if shutil.which("tesseract") is None:
        raise OCRError("tesseract not found (macOS: brew install tesseract)")
    command = ["tesseract", target, "stdout", "-l", lang, "--psm", str(psm)]
    if whitelist:
        command += ["-c", f"tessedit_char_whitelist={whitelist}"]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise OCRError(f"tesseract failed: {result.stderr.strip()[:200]}")
    return result.stdout


def _split(raw: str, expected: int) -> Optional[List[str]]:
    """Split one invocation's output into per-image chunks, or ``None`` if it does not line up."""
    chunks = raw.split(PAGE_BREAK)
    if chunks and not chunks[-1].strip():
        chunks = chunks[:-1]
    if len(chunks) != expected:
        return None
    return [" ".join(chunk.split()) for chunk in chunks]


def texts(
    images: Sequence[Image.Image],
    lang: str = "asm",
    psm: int = 7,
    scale: int = 1,
    whitelist: Optional[str] = None,
) -> List[str]:
    """Read every image, in order. The returned list is always the same length as ``images``."""
    if not images:
        return []

    out: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        for start in range(0, len(images), MAX_BATCH):
            chunk = images[start : start + MAX_BATCH]
            paths = _write(chunk, scale, workdir)
            listing = workdir / "batch.txt"
            listing.write_text("\n".join(str(p) for p in paths), encoding="utf-8")

            texts_out = _split(_invoke(str(listing), lang, psm, whitelist), len(paths))
            if texts_out is None:
                # Outputs did not line up with inputs. Reading one at a time cannot
                # misalign, so correctness is preserved at the cost of speed.
                texts_out = [
                    " ".join(_invoke(str(path), lang, psm, whitelist).split()) for path in paths
                ]
            out.extend(texts_out)
            for path in paths:
                path.unlink(missing_ok=True)
    return out
