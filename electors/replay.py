"""Cache what the OCR said, so a parsing change can be scored in seconds.

Most fixes in this stage change how a line is *interpreted*, not how it is read: a label
pattern, a fallback, a band assignment. Yet scoring one meant re-rasterising the PDF and
re-running tesseract over every crop -- half an hour per variant, for a change that could not
possibly alter a single character tesseract returns. The relation fix cost two such passes
before it was worth stopping to fix the loop itself.

So the expensive half is cached separately from the cheap half. ``capture`` runs the real
pipeline once and writes down every line of text, with the geometry that produced it.
``replay`` re-parses those lines with whatever the code says today. Two variants of a parser
score against **identical** input, which also removes the confound the earlier A/B ran on:
where OCR is re-run per variant, some of the difference is just tesseract being tesseract.

**What this can and cannot validate.** It is exact for anything downstream of the text:
labels, patterns, band assignment, field cleaning, sex and age matching. It says nothing about
a change to the crop geometry, the scale, or the engine -- those change the text itself, and
the cache would happily replay the old text and report no difference. `capture` therefore
stamps the settings it used, and `replay` refuses a cache whose stamp no longer matches.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from . import fields

#: Bumped when the captured text would no longer be what the pipeline produces -- a different
#: scale, crop rule or engine. A stale cache is worse than no cache: it replays the old text
#: and reports, truthfully but uselessly, that nothing changed.
CAPTURE_VERSION = 1

CACHE_DIR = Path("dataset/lines")


@dataclass
class BoxLines:
    """Every line of text one elector box gave up, and where the box was."""

    page: int
    section: str
    col: int
    row: int
    lines: List[str] = dataclass_field(default_factory=list)
    name_second: List[str] = dataclass_field(default_factory=list)
    epic_raw: str = ""
    serial_raw: str = ""


@dataclass
class PartLines:
    part: int
    ac: int
    capture_version: int
    scale: int
    psm: int
    lang: str
    boxes: List[BoxLines] = dataclass_field(default_factory=list)

    def to_json(self) -> Dict[str, Any]:
        data = asdict(self)
        return data

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> "PartLines":
        boxes = [BoxLines(**b) for b in data.pop("boxes", [])]
        return cls(boxes=boxes, **data)


def path_for(part: int, ac: int = 1, root: Path = CACHE_DIR) -> Path:
    return root / f"AC{ac}_part{part:03d}.json"


def save(captured: PartLines, root: Path = CACHE_DIR) -> Path:
    target = path_for(captured.part, captured.ac, root)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(captured.to_json(), ensure_ascii=False), encoding="utf-8")
    tmp.replace(target)
    return target


def load(part: int, ac: int = 1, root: Path = CACHE_DIR) -> Optional[PartLines]:
    """The captured lines for a part, or None if absent or captured by older code."""
    target = path_for(part, ac, root)
    if not target.exists():
        return None
    captured = PartLines.from_json(json.loads(target.read_text(encoding="utf-8")))
    if captured.capture_version != CAPTURE_VERSION:
        # Refused rather than repaired: replaying text the current pipeline would not produce
        # gives a confident answer to the wrong question.
        return None
    return captured


def cached_parts(root: Path = CACHE_DIR, ac: int = 1) -> List[int]:
    if not root.exists():
        return []
    found = []
    for target in root.glob(f"AC{ac}_part*.json"):
        part = int(target.stem.split("part")[-1])
        if load(part, ac, root) is not None:
            found.append(part)
    return sorted(found)


def parse_box(box: BoxLines, serial: int) -> Dict[str, Any]:
    """One elector, parsed from cached text by whatever the field code says today.

    Calls :func:`fields.assemble` -- the pipeline's own assembler -- rather than
    reimplementing it. A replay harness that reimplements the parser measures the harness.
    """
    elector = fields.assemble((box.lines, box.name_second), box.epic_raw, box.serial_raw)
    return {
        "serial_no": serial,
        "epic_no": elector.epic_no,
        "name": elector.name,
        "relation_name": elector.relation_name,
        "relation_type": elector.relation_type,
        "house_no": elector.house_no,
        "age": elector.age,
        "sex": elector.sex,
        "flags": ";".join(elector.flags),
        "box_col": box.col,
        "box_row": box.row,
        "page_no": box.page,
        "roll_section": box.section,
        "lang": "asm",
    }


def replay(captured: PartLines) -> List[Dict[str, Any]]:
    """Every elector in a part, re-parsed from cached text."""
    return [parse_box(box, serial) for serial, box in enumerate(captured.boxes, start=1)]


def replay_parts(parts: Sequence[int], ac: int = 1, root: Path = CACHE_DIR) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for part in parts:
        captured = load(part, ac, root)
        if captured is None:
            continue
        rows.extend(replay(captured))
    return rows


def missing(parts: Iterable[int], ac: int = 1, root: Path = CACHE_DIR) -> List[int]:
    """Which requested parts have no usable capture -- named, never silently skipped."""
    return [p for p in parts if load(p, ac, root) is None]
