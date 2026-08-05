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

import base64
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Protocol

from PIL import Image

DIGIT_WHITELIST = "0123456789"

# Upscaling is not one-size-fits-all here; all three values are measured on this corpus.
#
#   digits            scale 2 -> 100.0%  scale 3 -> 99.0%  scale 4 -> 86.9%  (sum check)
#   single-line text  scale 2 correct;   scale 3 loses long values outright -- it
#                     returned "" for "যোৰহাট ইঞ্জিনিয়াৰিং কলেজ" on 6 pages
#   multi-line text   scale 2 silently DROPS a whole line of a two-line address under
#                     every psm; scale 3 reads both
#
# So scale is chosen per region rather than per engine. This is also the second reason
# read_digits and read_text are separate calls (the first being that the Assamese model
# renders Western 8 as ৪).
DEFAULT_DIGIT_SCALE = 2
DEFAULT_TEXT_SCALE = 2
MULTILINE_TEXT_SCALE = 3

#: ``psm 6`` ("uniform block") beat 7/8/13 on isolated numbers across the Assamese
#: corpus. It is *not* reliable on a lone digit, though the Assamese sample suggested it
#: was: on Bengali pages it misses the lone "0" in the third-gender column about a third
#: of the time, where psm 7 reads it -- and on Assamese the reverse. ``read_digits``
#: therefore falls back rather than relying on any single mode.
DEFAULT_PSM = 6

#: "Treat the image as a single character." The fallback for a digit cell holding one
#: glyph, which no single psm reads reliably across languages.
SINGLE_CHAR_PSM = 10

ASCII_DIGITS = re.compile(r"[0-9]+")
ANY_DIGITS = re.compile(r"\d+")


class OCRError(RuntimeError):
    """Raised when an OCR engine is unavailable or fails."""


class Engine(Protocol):
    """The contract every engine implements."""

    name: str

    def read_text(self, image: Image.Image, scale: Optional[int] = None) -> str:
        """Transcribe a cell containing script.

        ``scale`` lets a caller override upscaling for a region whose shape needs it;
        engines that do not rescale may ignore it.
        """
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
    digit_scale: int = DEFAULT_DIGIT_SCALE
    text_scale: int = DEFAULT_TEXT_SCALE
    psm: int = DEFAULT_PSM

    def _run(
        self,
        image: Image.Image,
        lang: str,
        whitelist: Optional[str],
        scale: int,
        psm: Optional[int] = None,
    ) -> str:
        if shutil.which("tesseract") is None:
            raise OCRError("tesseract not found (macOS: brew install tesseract)")
        grey = image.convert("L")
        if scale != 1:
            grey = grey.resize(
                (grey.width * scale, grey.height * scale),
                Image.Resampling.LANCZOS,
            )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "cell.png")
            grey.save(path)
            command = ["tesseract", path, "stdout", "-l", lang, "--psm", str(psm or self.psm)]
            if whitelist:
                command += ["-c", f"tessedit_char_whitelist={whitelist}"]
            result = subprocess.run(command, capture_output=True, text=True)
            if result.returncode != 0:
                raise OCRError(f"tesseract failed: {result.stderr.strip()[:200]}")
            return result.stdout.strip()

    def read_text(self, image: Image.Image, scale: Optional[int] = None) -> str:
        raw = self._run(image, self.lang, None, scale or self.text_scale)
        return re.sub(r"\s+", " ", raw).strip()

    def read_digits(self, image: Image.Image) -> str:
        """Read a digit-only cell, retrying a lone digit as a single character.

        A single digit standing alone in a wide cell is the fragile case, and no one
        ``--psm`` handles it everywhere. ``psm 6`` reads the lone ``0`` in the
        third-gender column on Assamese pages but returns empty on many Bengali ones;
        ``psm 7`` is the other way round. Measured over Bengali samples, ``psm 6`` at the
        default scale missed 3 of 5 lone zeros while ``psm 10`` -- "treat the image as a
        single character" -- read all 5, and it also reads the Assamese ones.

        So the primary mode is unchanged and a second pass runs only when the first
        returns nothing. Cells that hold several digits never reach it, and a genuinely
        empty cell costs one extra call and still returns "".

        Left unguarded this silently zeroed nothing -- it produced ``None``, which failed
        the elector-sum check on 36% of Bengali parts. Visible, but wrong.
        """
        text = re.sub(r"\D", "", self._run(image, "eng", DIGIT_WHITELIST, self.digit_scale))
        if text:
            return text
        return re.sub(
            r"\D",
            "",
            self._run(image, "eng", DIGIT_WHITELIST, self.digit_scale, psm=SINGLE_CHAR_PSM),
        )


class SuryaEngine:
    """Surya OCR (VLM) via savitr's MLX runtime, driven as a subprocess.

    Surya's stack conflicts with this package's (it needs mlx-vlm), so it runs in its own
    venv and communicates over stdin/stdout -- the same cross-venv arrangement savitr and
    the Manipur benchmark use. ``scripts/surya_worker.py`` holds the model resident, which
    matters: a process per crop would pay ~0.8s of model load for ~0.3s of work.

    Digits fall back to Tesseract deliberately. Surya's language prior, which is what
    makes it good at conjuncts, works against it on numerals -- it renders a Latin
    pincode as ``78336০``, mixing in a Bengali zero.
    """

    name = "surya"

    def __init__(
        self,
        python: str = ".venv-surya/bin/python",
        worker: str = "scripts/surya_worker.py",
        model: str = "/Users/soodoku/Documents/GitHub/savitr/models/surya-mlx-4bit",
        digit_engine: Optional["Engine"] = None,
    ) -> None:
        self._python, self._worker, self._model = python, worker, model
        self._digits = digit_engine or TesseractEngine()
        self._process: Optional[subprocess.Popen] = None

    def _start(self) -> subprocess.Popen:
        if self._process and self._process.poll() is None:
            return self._process
        if not Path(self._python).exists():
            raise OCRError(
                f"{self._python} not found; create it with "
                "`uv venv --python 3.12 .venv-surya && "
                "uv pip install --python .venv-surya/bin/python savitr`"
            )
        self._process = subprocess.Popen(
            [self._python, self._worker, "--model", self._model],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        ready = self._process.stdout.readline()  # type: ignore[union-attr]
        if not ready or "ready" not in ready:
            raise OCRError(f"surya worker failed to start: {ready!r}")
        return self._process

    def read_text(self, image: Image.Image, scale: Optional[int] = None) -> str:
        del scale  # Surya is a VLM; it does not benefit from client-side upscaling
        process = self._start()
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        payload = json.dumps({"png": base64.b64encode(buffer.getvalue()).decode("ascii")})
        process.stdin.write(payload + "\n")  # type: ignore[union-attr]
        process.stdin.flush()  # type: ignore[union-attr]
        reply = json.loads(process.stdout.readline())  # type: ignore[union-attr]
        if "error" in reply:
            raise OCRError(reply["error"])
        return strip_html(reply.get("text", ""))

    def read_digits(self, image: Image.Image) -> str:
        return self._digits.read_digits(image)

    def close(self) -> None:
        if self._process and self._process.poll() is None:
            self._process.terminate()
            self._process.wait(timeout=10)


TAG_RE = re.compile(r"<[^>]+>")


def strip_html(text: str) -> str:
    """Flatten Surya's HTML to plain text.

    Surya emits more than one valid table layout for the same input -- sometimes with
    ``<b>``-wrapped labels, sometimes not, sometimes inside a ``<div>``. savitr's own
    findings note the same thing ("cell-structure parsers break"). So the structure is
    discarded and only the text kept; the grid already tells us what each crop is.
    """
    flattened = TAG_RE.sub(" ", text or "")
    for entity, char in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&nbsp;", " ")):
        flattened = flattened.replace(entity, char)
    return re.sub(r"\s+", " ", flattened).strip()


ENGINES: Dict[str, type] = {"tesseract": TesseractEngine, "surya": SuryaEngine}


def get_engine(name: str = "tesseract", lang: Optional[str] = None, **kwargs) -> Engine:
    """Build an engine, optionally for a specific Tesseract language model.

    ``lang`` is the model the *page* needs -- ``asm``, ``ben`` or ``eng`` -- and comes
    from the page's ``LanguageProfile``. It only affects text reading; digits are always
    read with ``eng`` regardless, since the digit-script trap is a property of the
    Indic models rather than of any one language.
    """
    if name not in ENGINES:
        raise OCRError(f"unknown engine {name!r}; available: {sorted(ENGINES)}")
    if lang and name == "tesseract":
        kwargs["lang"] = lang
    return ENGINES[name](**kwargs)


#: Vintage of each traineddata file, for provenance. Read off the files themselves::
#:
#:     $ strings asm.traineddata | grep -m1 '^[0-9]'
#:     4.00.00alpha:asm:synth20170629
#:
#: All three models this corpus needs carry the **same 2017 synthetic vintage** -- the
#: English one is no fresher than the Indic ones. Knowing which model read a page is part
#: of knowing how much to trust it, so it goes into every row's provenance.
TRAINEDDATA_VINTAGE = {"asm": "synth20170629", "ben": "synth20170629", "eng": "synth20170629"}


def engine_version(name: str = "tesseract", lang: str = "asm") -> str:
    """A version string for the engine, recorded with every row.

    Worth capturing because the recogniser and its language model move independently:
    the Tesseract binary is actively maintained while ``asm.traineddata`` has been frozen
    at ``synth20170629`` since 2017. A row's readings are attributable to both -- and
    with three languages in the corpus, to *which* model actually read the page.
    """
    if name != "tesseract":
        return name
    vintage = TRAINEDDATA_VINTAGE.get(lang, "unknown")
    try:
        result = subprocess.run(["tesseract", "--version"], capture_output=True, text=True)
        first = (result.stdout or result.stderr).splitlines()[0].strip()
        return f"{first} ({lang}={vintage})"
    except (OSError, IndexError):
        return f"tesseract ({lang}={vintage}, version unknown)"


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
