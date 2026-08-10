"""What resolution should Cloud Vision be fed?

The part PDFs carry **no fonts**: 34 images, every page a single 1187x1679 raster. They are 144
dpi scans. So the pipeline's 400 dpi render is a 2.78x upsample of pixels that already exist,
300 dpi is a 2.09x upsample of the same pixels, and the native image holds everything either of
them does.

That matters because **Vision bills per image submitted**, and how many pages fit in one image
is set by their size. A page is 15.5 MP at 400 dpi and 2.0 MP native, so the state costs $366
one way and about $50 the other, for pixels that came from the same scanner.

There is reason to think the upsample may be actively harmful. ``assam_rolls.render`` already
records that an interpolating filter on these scans invents ink: LANCZOS read ``start_serial``
correctly on 0 of 30 pages where NEAREST got 30 of 30, because ringing thickened a "1" into a
"4". ``pdftoppm`` interpolates. Against that, ``electors.extract`` notes a box is ~1000px wide
at 400 dpi, "which is where the Assamese conjuncts in a name become legible" -- at native it is
~395px. Both arguments are about tesseract. Neither has been tested on Vision.

**Two measurements, because they answer different questions.** Ground truth exists for 34 boxes,
which can detect a collapse but not a few points. Agreement between arms needs no truth at all
and runs over thousands of boxes, so it can say whether resolution changes the answer *at all*.
Agreement is reported as agreement and never as accuracy: three arms of one engine can be wrong
together, and are likelier to be than three different engines would be.
"""

from __future__ import annotations

import collections
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from PIL import Image

from assam_rolls import render

from . import evaluate, extract, vision, vision_part

#: ``p13_pg4_r4c0`` -- part, page, box row, box column. The key the eval set is written in.
KEY = re.compile(r"p(\d+)_pg(\d+)_r(\d+)c(\d+)")

#: Fields compared between arms. Relation is left out for the same reason the second pass leaves
#: it out: it is not what this measurement is about.
FIELDS = ("name", "age", "house", "sex")


def native_renderer(pdf_bytes: bytes, workdir: Path) -> List[Image.Image]:
    """Every page as the embedded image, byte-exact, with nothing resampled.

    ``pdfimages`` is run once for the whole PDF rather than once per page: the per-page helper
    in ``assam_rolls.render`` writes the PDF to a fresh temporary directory each call, which is
    34 copies of a 14 MB file for one part.
    """
    import subprocess

    pdf = workdir / "part.pdf"
    pdf.write_bytes(pdf_bytes)
    subprocess.run(
        ["pdfimages", "-png", str(pdf), str(workdir / "img")],
        check=True,
        capture_output=True,
    )
    paths = sorted(workdir.glob("img-*.png"), key=lambda p: int(p.stem.split("-")[-1]))
    return [Image.open(path).convert("L") for path in paths]


#: The arms, each a renderer. Everything downstream of the renderer is identical, which is what
#: makes them comparable.
ARMS: Dict[str, Callable[[bytes, Path], List[Image.Image]]] = {
    "400 dpi": vision_part.rasterize(400),
    "300 dpi": vision_part.rasterize(300),
    "native": native_renderer,
}


@dataclass
class ArmResult:
    """One arm over one part: what it read, and what it cost."""

    arm: str
    part: int
    boxes: Dict[Tuple[int, int, int], Dict[str, Any]] = field(default_factory=dict)
    images_billed: int = 0
    pages: int = 0
    error: str = ""


def read_arm(
    zip_path: Path,
    pdf_name: str,
    part_no: int,
    arm: str,
    api_key: str,
    pages_per_image: Optional[int] = None,
) -> ArmResult:
    """One part through one arm, keyed by box position so arms can be lined up."""
    result = vision_part.read_part(
        zip_path,
        pdf_name,
        api_key=api_key,
        pages_per_image=pages_per_image,
        renderer=ARMS[arm],
    )
    out = ArmResult(
        arm=arm,
        part=part_no,
        images_billed=result.images_billed,
        pages=result.page_count,
        error=result.error,
    )
    for row in result.electors:
        out.boxes[(row["page_no"], row["box_row"], row["box_col"])] = {
            "name": row.get("name", ""),
            "age": row.get("age"),
            "house": row.get("house_no", ""),
            "sex": row.get("sex", ""),
        }
    return out


def common_boxes(arms: Sequence[ArmResult]) -> List[Tuple[int, int, int]]:
    """Boxes every arm returned -- the only basis on which arms can be compared.

    A resolution that resolves fewer boxes has failed at geometry, and scoring it on the boxes
    it did resolve would flatter it. This project has twice had a ranking reversed by columns
    with different denominators.
    """
    if not arms:
        return []
    shared = set(arms[0].boxes)
    for other in arms[1:]:
        shared &= set(other.boxes)
    return sorted(shared)


@dataclass
class Agreement:
    """How often the arms returned the same value, per field."""

    boxes: int = 0
    same: Dict[str, int] = field(default_factory=lambda: {f: 0 for f in FIELDS})
    examples: Dict[str, List[Tuple[Any, ...]]] = field(
        default_factory=lambda: {f: [] for f in FIELDS}
    )

    def rate(self, name: str) -> float:
        return self.same[name] / self.boxes if self.boxes else 0.0


def comparable_parts(arms: Sequence[ArmResult]) -> List[Tuple[int, List[ArmResult]]]:
    """Parts where every arm returned something, grouped so their boxes can be lined up.

    Two things this exists to prevent, both of which produced an empty comparison the first
    time it ran over real data:

    - **Box keys are only unique within a part.** ``(page, row, column)`` repeats in every part,
      so pooling six parts and intersecting compares part 7's page 4 with part 64's.
    - **One failed arm silently voids everything.** Part 25's native arm lost its request to a
      DNS failure and part 33's 400 dpi arm to a broken pipe. Intersecting across the whole run
      then yields nothing, and an empty intersection reads as "the arms never agree" rather than
      "two requests failed".
    """
    by_part: Dict[int, List[ArmResult]] = collections.defaultdict(list)
    for arm in arms:
        by_part[arm.part].append(arm)
    names = {arm.arm for arm in arms}
    return [
        (part, group)
        for part, group in sorted(by_part.items())
        if {a.arm for a in group} == names and all(a.boxes for a in group)
    ]


def agreement(arms: Sequence[ArmResult], keep_examples: int = 4) -> Agreement:
    """Whether the arms return the same answer, box by box and field by field.

    Needs no ground truth, which is the point: it runs over every box in the sample rather than
    the handful that have been read by eye. Unanimity is required -- two of three agreeing is a
    disagreement, because the question is whether resolution changes the answer at all.
    """
    found = Agreement()
    for part, group in comparable_parts(arms):
        for key in common_boxes(group):
            found.boxes += 1
            for name in FIELDS:
                values = [_normalise(name, arm.boxes[key].get(name)) for arm in group]
                if len(set(values)) == 1:
                    found.same[name] += 1
                elif len(found.examples[name]) < keep_examples:
                    found.examples[name].append(
                        ((part,) + key, dict(zip([a.arm for a in group], values)))
                    )
    return found


def _normalise(name: str, value: Any) -> Any:
    """Compare values the way the scorer does, so agreement and accuracy cannot disagree."""
    if name == "age":
        return value
    text = evaluate.fold(value)
    return text.replace(" ", "") if name == "house" else text


def score_against_truth(
    arms: Sequence[ArmResult], truth: Dict[str, Dict[str, Any]]
) -> Dict[str, evaluate.Score]:
    """Each arm scored on the hand-read boxes that *every* arm returned."""
    wanted: Dict[Tuple[int, int, int, int], str] = {}
    for key in truth:
        match = KEY.match(key)
        if match:
            part, page, row, col = (int(g) for g in match.groups())
            wanted[(part, page, row, col)] = key

    by_part: Dict[int, List[ArmResult]] = collections.defaultdict(list)
    for arm in arms:
        by_part[arm.part].append(arm)

    tallies = {arm.arm: evaluate.Score(arm.arm) for arm in arms}
    for part, group in by_part.items():
        for page, row, col in common_boxes(group):
            key = wanted.get((part, page, row, col))
            if not key:
                continue
            for arm in group:
                evaluate.score_one(arm.boxes[(page, row, col)], truth[key], tallies[arm.arm])
    return tallies


def cost_for_state(arms: Sequence[ArmResult], pages_in_state: int = 921_000) -> Dict[str, float]:
    """What each arm would cost over the whole state, from its measured pages-per-image."""
    per_arm: Dict[str, List[ArmResult]] = collections.defaultdict(list)
    for arm in arms:
        per_arm[arm.arm].append(arm)
    out = {}
    for name, group in per_arm.items():
        billed = sum(a.images_billed for a in group)
        pages = sum(a.pages for a in group)
        if billed and pages:
            out[name] = vision.cost(round(pages_in_state / (pages / billed)))
    return out


def report(
    arms: Sequence[ArmResult],
    truth: Optional[Dict[str, Dict[str, Any]]] = None,
) -> str:
    """The whole comparison, in the two units the decision is actually made in."""
    names = list(dict.fromkeys(a.arm for a in arms))
    lines: List[str] = []

    costs = cost_for_state(arms)
    lines += ["WHAT EACH ARM COSTS", ""]
    lines.append(f"   {'arm':<12}{'pages':>8}{'images':>8}{'pages/image':>13}{'state':>10}")
    for name in names:
        group = [a for a in arms if a.arm == name]
        pages = sum(a.pages for a in group)
        billed = sum(a.images_billed for a in group)
        ratio = pages / billed if billed else 0
        lines.append(
            f"   {name:<12}{pages:>8}{billed:>8}{ratio:>13.1f}"
            f"{'$' + format(costs.get(name, 0), '.0f'):>10}"
        )

    found = agreement(arms)
    lines += ["", f"AGREEMENT BETWEEN ARMS over {found.boxes:,} boxes all of them returned", ""]
    lines.append("   (this is agreement, not accuracy -- three arms of one engine can be wrong")
    lines.append("    together, and are likelier to be than three engines would be)")
    lines.append("")
    for name in FIELDS:
        lines.append(
            f"   {name:<10} {found.same[name]:>7,}/{found.boxes:<7,} {found.rate(name):>6.2%}"
        )
    for name in FIELDS:
        for key, values in found.examples[name]:
            lines.append(f"      {name} differs at page {key[0]} r{key[1]}c{key[2]}: {values}")

    if truth:
        tallies = score_against_truth(arms, truth)
        tallies = {k: v for k, v in tallies.items() if v.total}
        if tallies:
            lines += ["", evaluate.render(tallies)]
    return "\n".join(lines)


def save(arms: Sequence[ArmResult], path: Path) -> Path:
    """Write what each arm read, so the analysis can be redone without paying again.

    The first run of this comparison spent three hours and every request, and then reported
    nothing because the agreement function pooled parts whose box keys collide. The readings
    were fine; only the arithmetic over them was wrong, and there was no way to redo it.
    """
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "arm": a.arm,
            "part": a.part,
            "pages": a.pages,
            "images_billed": a.images_billed,
            "error": a.error,
            # JSON has no tuple keys, so the box position is written as a string and split back.
            "boxes": {f"{p},{r},{c}": v for (p, r, c), v in a.boxes.items()},
        }
        for a in arms
    ]
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def load(path: Path) -> List[ArmResult]:
    """Arm results written by :func:`save`."""
    import json

    out = []
    for row in json.loads(path.read_text(encoding="utf-8")):
        boxes = {
            tuple(int(n) for n in key.split(",")): value  # type: ignore[misc]
            for key, value in row["boxes"].items()
        }
        out.append(
            ArmResult(
                arm=row["arm"],
                part=row["part"],
                boxes=boxes,
                pages=row["pages"],
                images_billed=row["images_billed"],
                error=row["error"],
            )
        )
    return out


def parts_of(zip_path: Path, limit: int) -> List[Any]:
    """The first ``limit`` parts of a constituency zip."""
    return [p for p in render.iter_zip_parts(zip_path)][:limit]


#: Pages the hand-read boxes come from, as ``part -> pages``. Running whole parts for six pages
#: would cost thirty times what the truth arm needs.
def truth_parts(truth: Dict[str, Dict[str, Any]]) -> Dict[int, List[int]]:
    found: Dict[int, set] = collections.defaultdict(set)
    for key in truth:
        match = KEY.match(key)
        if match:
            found[int(match.group(1))].add(int(match.group(2)))
    return {part: sorted(pages) for part, pages in sorted(found.items())}


__all__ = [
    "ARMS",
    "Agreement",
    "ArmResult",
    "agreement",
    "common_boxes",
    "cost_for_state",
    "extract",
    "native_renderer",
    "read_arm",
    "report",
    "score_against_truth",
    "truth_parts",
]
