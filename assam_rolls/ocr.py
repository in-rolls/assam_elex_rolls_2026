"""OCR engines behind one contract, so engines can be compared and swapped.

Mirrors the ``ocr_pdf(...) -> pages`` idea from
``parse_unsearchable_rolls/scripts/manipur/ocr_engines.py``, narrowed to what this form
needs: read a **cell crop** as either text or digits.

Two calls, deliberately kept apart:

``read_digits``
    Latin digits only (``-l eng`` plus a digit whitelist). Used exclusively on cells that
    contain nothing but a number.

``read_text``
    Assamese (``-l asm``). Used on everything else.

**They must not be swapped.** Tesseract's Assamese model transcribes the Western digit
``8`` as Assamese ``৪`` (U+09EA), and because Python's ``\\d`` and ``int()`` both accept
Unicode decimal digits, that misread silently becomes the number 4. Conversely, running a
digit whitelist over Assamese text forces every glyph to a digit and returns pure noise.
Digits are therefore only ever read from crops known to hold digits alone.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Dict, Optional, Protocol

from PIL import Image

DIGIT_WHITELIST = "0123456789"

#: 144 dpi source; Tesseract does better with a larger glyph, and 2x measured best.
DEFAULT_SCALE = 2

#: ``psm 6`` ("uniform block") beat 7/8/13 on isolated numbers -- notably it is the only
#: mode that reliably reads a lone "0", which 7 returns as empty.
DEFAULT_PSM = 6

ASCII_DIGITS = re.compile(r"[0-9]+")
ANY_DIGITS = re.compile(r"\d+")


class OCRError(RuntimeError):
    """Raised when an OCR engine is unavailable or fails."""


class Engine(Protocol):
    """The contract every engine implements."""

    name: str

    def read_text(self, image: Image.Image) -> str:
        """Transcribe a cell containing script."""
        ...

    def read_digits(self, image: Image.Image) -> str:
        """Transcribe a cell containing only Latin digits."""
        ...


@dataclass
class TesseractEngine:
    """Tesseract 5 with the ``asm`` (Assamese) model.

    Requires the traineddata::

        curl -L -o $(brew --prefix)/share/tessdata/asm.traineddata \\
          https://github.com/tesseract-ocr/tessdata_best/raw/main/asm.traineddata
    """

    name: str = "tesseract"
    lang: str = "asm"
    scale: int = DEFAULT_SCALE
    psm: int = DEFAULT_PSM

    def _run(self, image: Image.Image, lang: str, whitelist: Optional[str]) -> str:
        if shutil.which("tesseract") is None:
            raise OCRError("tesseract not found (macOS: brew install tesseract)")
        grey = image.convert("L")
        if self.scale != 1:
            grey = grey.resize(
                (grey.width * self.scale, grey.height * self.scale),
                Image.Resampling.LANCZOS,
            )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "cell.png")
            grey.save(path)
            command = ["tesseract", path, "stdout", "-l", lang, "--psm", str(self.psm)]
            if whitelist:
                command += ["-c", f"tessedit_char_whitelist={whitelist}"]
            result = subprocess.run(command, capture_output=True, text=True)
            if result.returncode != 0:
                raise OCRError(f"tesseract failed: {result.stderr.strip()[:200]}")
            return result.stdout.strip()

    def read_text(self, image: Image.Image) -> str:
        return re.sub(r"\s+", " ", self._run(image, self.lang, None)).strip()

    def read_digits(self, image: Image.Image) -> str:
        return re.sub(r"\D", "", self._run(image, "eng", DIGIT_WHITELIST))


ENGINES: Dict[str, type] = {"tesseract": TesseractEngine}


def get_engine(name: str = "tesseract", **kwargs) -> Engine:
    if name not in ENGINES:
        raise OCRError(f"unknown engine {name!r}; available: {sorted(ENGINES)}")
    return ENGINES[name](**kwargs)


# ------------------------------------------------------------------- value extraction


def int_or_none(text: str) -> Optional[int]:
    """First **ASCII** integer in ``text``.

    Deliberately stricter than ``re.search(r"\\d+")``: that also matches Assamese and
    Devanagari digits, so an OCR script confusion (``8`` read as ``৪``) would convert
    cleanly to a wrong number instead of being caught.
    """
    match = ASCII_DIGITS.search(text or "")
    return int(match.group()) if match else None


def has_non_ascii_digit(text: str) -> bool:
    """True when a non-Latin digit appears -- either real Assamese numerals or a misread."""
    return bool(ANY_DIGITS.search(text or "")) and not bool(
        ASCII_DIGITS.fullmatch("".join(ANY_DIGITS.findall(text or "")))
    )


def value_after_label(text: str) -> str:
    """The part of a ``label : value`` line after the colon.

    Tesseract sometimes drops the space around the colon, so the split is on the
    character rather than a padded pattern.
    """
    return text.split(":", 1)[1].strip() if ":" in text else text.strip()


def leading_int(text: str) -> Optional[int]:
    """Leading ASCII integer of a ``"<n> - <name>"`` value."""
    return int_or_none(value_after_label(text))
