"""Google Cloud Vision, billed per image rather than per page.

Vision charges for each **image submitted**, not for each page of source, so stacking pages
into one tall PNG divides the bill by the stacking factor. ``in-rolls/google_vision_ocr`` uses
exactly this and reports 12,694 pages for about $1.50. At eight pages an image a constituency
is roughly **$0.85** against $6.75 sent one page at a time.

**And it needs no asynchronous API.** ``images:annotate`` takes 16 images per request at 1,800
requests a minute, so a whole constituency is 563 stacked images in about 36 calls. The
``asyncBatchAnnotate`` methods read and write Cloud Storage URIs and want a service account;
they would buy nothing at this size.

**The reason to want it is the coordinates, not the price.** Every other engine here was handed
a crop and asked what it said, and this stage has spent its whole life fighting that direction:
band assignment by ink projection produced the elector's own name in the relation field, the
swapped name and relation, and the house numbers that were never found. Vision returns every
word with a bounding box, and the geometry on this side is already exact -- ``grid.build`` gives
box rectangles and ``grid.text_bands`` gives line rectangles. Matching words into rectangles
inverts the problem: nothing has to be cropped, and nothing has to be guessed.
"""

from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Tuple

from assam_rolls import schema

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime, needed only for annotations
    from .fields import Elector

ENDPOINT = "https://vision.googleapis.com/v1/images:annotate"

#: Hard limits from the API: 20 MB per file, 75 megapixels for OCR.
MAX_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 75_000_000

#: Images per ``images:annotate`` request.
IMAGES_PER_REQUEST = 16

#: Most pages that may be stacked into one image, whatever their size allows. A ceiling, not a
#: target: :func:`pages_that_fit` decides the real number from the pages in hand.
PAGES_PER_IMAGE = 8

#: Fraction of the pixel ceiling a stack is allowed to use.
#:
#: Pages within a part vary -- a supplement page renders taller than a main-roll one -- and the
#: stacking factor is chosen from the first pages seen. Filling the ceiling exactly would then
#: have a later, larger page tip a stack over it and lose the part.
PIXEL_MARGIN = 0.9


def pages_that_fit(width: int, height: int, limit: int = MAX_PIXELS) -> int:
    """How many pages of this size stack into one image without breaching the OCR ceiling.

    Derived rather than assumed. This was the constant ``8``, which is right for 300 dpi -- and
    the pipeline renders at 400, where eight pages is 124 MP against a 75 MP limit. Every part
    would have been refused. The guard caught it before a request was spent, but the number had
    no business being a guess when the pages are right there to measure.
    """
    per_page = max(1, width * height)
    return max(1, min(PAGES_PER_IMAGE, int(limit * PIXEL_MARGIN) // per_page))


#: Assamese first, Bengali second. They are one script, and naming both lets the recogniser use
#: whichever lexicon fits -- the roll is Assamese but the letterforms are shared.
LANGUAGE_HINTS = ("as", "bn")

#: Dollars per 1,000 images, above the first 1,000 a month.
COST_PER_1000 = 1.50


@dataclass
class Word:
    """One word and where it sat, in the coordinate space of the image submitted."""

    text: str
    left: int
    top: int
    right: int
    bottom: int

    @property
    def middle_y(self) -> float:
        return (self.top + self.bottom) / 2

    def shifted(self, dy: int) -> "Word":
        """The same word in a single page's coordinates, given the page's origin in the stack."""
        return Word(self.text, self.left, self.top - dy, self.right, self.bottom - dy)


@dataclass
class Stack:
    """Several pages in one image, and where each page starts in it."""

    image: Any
    offsets: List[int] = field(default_factory=list)

    @property
    def pixels(self) -> int:
        return self.image.width * self.image.height

    def check(self) -> Optional[str]:
        """Why this stack would be rejected, or None. Checked before spending a request."""
        if self.pixels > MAX_PIXELS:
            return f"{self.pixels:,} pixels exceeds the {MAX_PIXELS:,} OCR limit"
        return None


def stack_pages(images: Sequence[Any]) -> Stack:
    """Stack page images vertically, recording where each one begins.

    The offsets are the whole point: Vision answers in the stacked image's coordinates, and a
    word on page two has to come back to page two. Getting this wrong would misfile every field
    on every page but the first, silently and plausibly, which is why it is returned as data
    rather than recomputed by whoever needs it.
    """
    from PIL import Image

    if not images:
        raise ValueError("nothing to stack")
    width = max(image.width for image in images)
    height = sum(image.height for image in images)
    canvas = Image.new("L", (width, height), "white")
    offsets, y = [], 0
    for image in images:
        offsets.append(y)
        canvas.paste(image.convert("L"), (0, y))
        y += image.height
    return Stack(image=canvas, offsets=offsets)


def words_from(response: Dict[str, Any]) -> List[Word]:
    """Every word Vision found, with its bounding box.

    Taken from the page/block/paragraph/word tree rather than ``text_annotations``, because the
    tree is what carries reliable per-word geometry.
    """
    out: List[Word] = []
    annotation = response.get("fullTextAnnotation") or {}
    for page in annotation.get("pages", []):
        for block in page.get("blocks", []):
            for paragraph in block.get("paragraphs", []):
                for word in paragraph.get("words", []):
                    text = "".join(s.get("text", "") for s in word.get("symbols", []))
                    if not text.strip():
                        continue
                    vertices = (word.get("boundingBox") or {}).get("vertices", [])
                    xs = [v.get("x", 0) for v in vertices]
                    ys = [v.get("y", 0) for v in vertices]
                    if not xs or not ys:
                        continue
                    out.append(Word(text, min(xs), min(ys), max(xs), max(ys)))
    return out


def overlap(word: Word, top: int, bottom: int) -> float:
    """How much of the word's height falls inside a band, as a fraction of the word."""
    height = max(1, word.bottom - word.top)
    return max(0, min(word.bottom, bottom) - max(word.top, top)) / height


#: A word has to sit mostly inside a band to belong to it. Bands are padded and adjacent, so a
#: tall glyph can graze the one above; requiring the majority stops it being filed there.
BAND_SHARE = 0.5


def lines_in(
    words: Sequence[Word], bands: Sequence[Tuple[int, int]], left: int, right: int
) -> List[str]:
    """The text of each band, from the words that fall inside it.

    Words are ordered left to right within a band, which is the reading order for this script
    and the only ordering the caller needs.
    """
    out: List[str] = []
    for top, bottom in bands:
        inside = [
            w
            for w in words
            if w.left >= left - 2 and w.right <= right + 2 and overlap(w, top, bottom) >= BAND_SHARE
        ]
        inside.sort(key=lambda w: w.left)
        out.append(" ".join(w.text for w in inside))
    return out


#: Any letter of the shared Bengali-Assamese script.
ASSAMESE = re.compile(r"[\u0980-\u09FF]")

#: Two words belong to the same line when their vertical spans overlap by at least this much of
#: the shorter one. Assamese sets a tall ascender and a low matra, so adjacent lines nearly
#: touch and a permissive threshold welds them together.
#:
#: Swept over 24 boxes with hand-read truth. At 0.4 the name and relation lines merge into one
#: and exact names collapse to 33%; from 0.5 it climbs, and it is flat from 0.7 on -- 88% of
#: names near-right, 92% of ages, 88% of house numbers. Chosen at the start of the plateau
#: rather than the top of it, so a slightly tighter line spacing does not fall off the edge.
LINE_SHARE = 0.7


def grouped_lines(words: Sequence[Word], left: int, right: int) -> List[str]:
    """Group words into lines from their own coordinates, with no band finder involved.

    This is the point of using an engine that returns geometry. Every line of text in this
    stage until now came from ``grid.text_bands``, which projects ink and guesses where lines
    start and stop -- and that guess produced the elector's own name in the relation field, the
    swapped name and relation, and house numbers that were never found at all. Words that carry
    their own positions do not need to be guessed at: they cluster into lines because they are
    on the same line.

    Ordered top to bottom, and left to right within a line, which is the reading order.
    """
    inside = [w for w in words if w.left >= left - 2 and w.right <= right + 2]
    if not inside:
        return []
    inside.sort(key=lambda w: w.top)

    rows: List[List[Word]] = [[inside[0]]]
    for word in inside[1:]:
        current = rows[-1]
        top = min(w.top for w in current)
        bottom = max(w.bottom for w in current)
        shorter = max(1, min(bottom - top, word.bottom - word.top))
        shared = min(bottom, word.bottom) - max(top, word.top)
        if shared / shorter >= LINE_SHARE:
            current.append(word)
        else:
            rows.append([word])

    out = []
    for row in rows:
        row.sort(key=lambda w: w.left)
        out.append(" ".join(w.text for w in row))
    return out


def header_and_body(words: Sequence[Word], left: int, right: int) -> Tuple[List[str], List[str]]:
    """A box split into its header strip and the Assamese lines under it.

    The header carries the serial and the EPIC, which are Latin and digits; the four lines that
    matter are Assamese. The split is by script rather than by position because the strip is
    one line on most boxes and two on some, and a fixed count got the second kind wrong.
    """
    lines = grouped_lines(words, left, right)
    split = 0
    while split < len(lines) and not ASSAMESE.search(lines[split]):
        split += 1
    return lines[:split], lines[split:]


def lines_by_position(words: Sequence[Word], left: int, right: int) -> List[str]:
    """The Assamese lines of a box, in reading order.

    Left in, the header becomes line one and every field after it is read one line down, which
    cost the name on half the boxes.
    """
    return header_and_body(words, left, right)[1]


def annotate(
    images: Sequence[Any],
    api_key: str,
    language_hints: Sequence[str] = LANGUAGE_HINTS,
    timeout: int = 180,
) -> List[Dict[str, Any]]:
    """Up to :data:`IMAGES_PER_REQUEST` images in one call, returned in submission order."""
    import io

    if len(images) > IMAGES_PER_REQUEST:
        raise ValueError(f"{len(images)} images exceeds the {IMAGES_PER_REQUEST} per request")

    requests = []
    for image in images:
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        data = buffer.getvalue()
        if len(data) > MAX_BYTES:
            raise ValueError(f"{len(data):,} bytes exceeds the {MAX_BYTES:,} limit; stack fewer")
        requests.append(
            {
                "image": {"content": base64.b64encode(data).decode("ascii")},
                "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
                "imageContext": {"languageHints": list(language_hints)},
            }
        )

    payload = json.dumps({"requests": requests}).encode("utf-8")
    request = urllib.request.Request(
        f"{ENDPOINT}?key={api_key}",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as handle:
            return json.loads(handle.read()).get("responses", [])
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise RuntimeError(f"vision {exc.code}: {detail}") from exc


def elector_from(header: Sequence[str], body: Sequence[str]) -> "Elector":
    """One elector from the lines Vision returned for its box.

    Deliberately routed through :mod:`electors.second_pass` rather than through
    ``fields.assemble``. The two differ in where the fields come from: ``assemble`` calls
    ``assign_bands``, which infers what a line *is* from its position among the others, while
    ``second_pass`` reads the printed labels. Vision's 72% exact names was measured on the
    label-reading path, and routing the pipeline through the position-inferring one would
    publish a number that was never measured.

    Values are then put through the same cleaners the tesseract path uses -- ``strip_foreign``,
    ``EPIC_RE`` -- so a flag means the same thing whichever engine produced the row.
    """
    from . import fields as fields_module
    from . import second_pass

    found = second_pass.Reading("\n".join(body)).fields()
    elector = fields_module.Elector()

    elector.name, uncertain = fields_module.strip_foreign(found.get("name", ""))
    if uncertain:
        elector.flags.append("name_repaired")
    elector.relation_name, uncertain = fields_module.strip_foreign(found.get("relation_name", ""))
    if uncertain:
        elector.flags.append("relation_repaired")
    elector.relation_type = found.get("relation_type", "")
    elector.house_no = found.get("house_no", "")
    elector.age = found.get("age")
    elector.sex = found.get("sex", "")

    strip = " ".join(header)
    match = fields_module.EPIC_RE.search(fields_module.NON_ALNUM.sub("", strip).upper())
    elector.epic_no = match.group() if match else ""
    serial = re.findall(r"\d{1,4}", schema.normalize_digits(strip))
    if serial:
        elector.serial_no_ocr = int(serial[0])

    if not elector.is_empty:
        for value, flag in (
            (elector.epic_no, "no_epic"),
            (elector.name, "no_name"),
            (elector.age, "no_age"),
            (elector.sex, "no_sex"),
        ):
            if not value:
                elector.flags.append(flag)
    return elector


def cost(images: int) -> float:
    """Dollars for a number of images submitted -- the unit Vision actually bills."""
    return images / 1000 * COST_PER_1000
