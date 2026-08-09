"""Re-reading the boxes the cheap pass could not, with a model that can.

Tesseract loses the house number on a third of boxes and the age on a fifth, and the line cache
showed why: the crop yields no digits at all, so no parser change recovers them. savitr's
distilled Surya reads the same crops -- on one page it took the house number from 14 of 30 to
29 and the age from 21 to 29.

**It is also right where the two disagree.** On the seven ages both engines read differently,
tesseract gave 92, 92, 85 and 96 while Surya gave 59, 52, 25 and 61. That is the same excess of
nonagenarians the age distribution had already flagged as an artefact -- 220 rows in the
nineties against 126 in the seventies -- so the disagreement is not a coin toss between two
opinions, it is one engine reproducing a known defect and the other not.

It costs about 6 seconds a box against tesseract's 1.7, which is why this runs on flagged rows
rather than on everything.

Two things the model does that have to be handled rather than hoped away:

*It runs away.* Eleven generations of thirty repeated themselves until the token cap stopped
them. The loop repeats a *complete* record, so the first cycle is kept and the rest discarded;
a response that hit the cap without ever repeating stopped mid-record and is refused outright.

*Its format varies.* The same prompt returns bare pipe-separated fields, the same wrapped in
``<p>``, and table rows padded with ``||``. The fields are found by label rather than by
position, because position is the thing that moves.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from assam_rolls import schema

#: Fields worth taking from the second pass. Name and relation are deliberately absent: the
#: cheap pass already returns them for 92% and 97% of rows and this model rewrites spellings
#: (নার্জাৰি for নাজ্জাৰা), so accepting them would churn values that were not in question.
RECOVERABLE = ("age", "house_no", "sex")

#: Label-anchored, because the layout of the response is what varies. Bengali and Latin digits
#: both appear, sometimes in the same line.
AGE_RE = re.compile(r"বয়স\s*[:：]?\s*([0-9০-৯]{1,3})")
HOUSE_RE = re.compile(r"ঘৰ\s*নং\s*[:：]?\s*([0-9০-৯]{1,6}\s*[ক-হ]?)")
SEX_RE = re.compile(r"লিঙ্গ\s*[:：]?\s*(\S+)")

SEX_WORDS = (("পুৰুষ", "M"), ("পুরুষ", "M"), ("মহিলা", "F"), ("তৃতীয়", "T"))

MIN_AGE, MAX_AGE = 18, 120

#: A response repeating itself is a runaway even when it stopped short of the cap.
REPEAT = re.compile(r"(.{20,}?)\1{2,}", re.S)


@dataclass
class Reading:
    """What the second pass made of one box, and whether it can be believed."""

    text: str
    tokens: int = 0
    capped: bool = False

    @property
    def settled(self) -> str:
        """The part of the response worth reading.

        A loop repeats a *complete* record -- ``বয়স: 54`` appears in every cycle with the same
        value -- so the first cycle is the answer and everything after it is the model failing
        to stop.

        On the page this was measured against it recovered no extra fields, because those rows
        already had values; what it bought was eleven more rows where the two engines could be
        compared at all. A response that hit the cap without ever repeating is different: it
        stopped mid-record and its tail may be half a number, so that one is refused.
        """
        repeat = REPEAT.search(self.text)
        if repeat:
            return repeat.group(1)
        return "" if self.capped else self.text

    @property
    def usable(self) -> bool:
        return bool(self.settled.strip())

    def fields(self) -> Dict[str, Any]:
        text = self.settled
        if not text.strip():
            return {}
        found: Dict[str, Any] = {}
        age = AGE_RE.search(text)
        if age:
            digits = schema.normalize_digits(age.group(1))
            if digits.isdigit() and MIN_AGE <= int(digits) <= MAX_AGE:
                found["age"] = int(digits)
        house = HOUSE_RE.search(text)
        if house:
            value = schema.normalize_digits(house.group(1)).strip()
            if value:
                found["house_no"] = value
        sex = SEX_RE.search(text)
        if sex:
            for word, code in SEX_WORDS:
                if word in sex.group(1):
                    found["sex"] = code
                    break
        return found


def wanted(row: Dict[str, Any]) -> bool:
    """Whether this row has something the second pass could plausibly recover.

    Deliberately narrower than the escalation router. The router flags anything doubtful; this
    asks only whether a field it can actually supply is missing, because a second read that
    returns what we already had is 6 seconds spent on nothing.
    """
    return row.get("age") is None or not row.get("house_no") or not row.get("sex")


def merge(row: Dict[str, Any], reading: Reading) -> Dict[str, Any]:
    """The row with recovered fields filled in, never overwritten.

    A value the cheap pass produced is left alone even where the two disagree. That is the
    conservative choice and not obviously the right one -- the disagreements measured so far
    favour the second engine -- but overwriting silently would replace a known quantity with an
    unmeasured one across the whole corpus. Disagreements are recorded so the choice can be
    revisited with evidence rather than reversed on a hunch.
    """
    out = dict(row)
    recovered, disagreed = [], []
    for field, value in reading.fields().items():
        if field not in RECOVERABLE:
            continue
        if out.get(field) in (None, ""):
            out[field] = value
            recovered.append(field)
        elif str(out[field]) != str(value):
            disagreed.append(f"{field}:{out[field]}!={value}")

    flags = [f for f in (out.get("flags") or "").split(",") if f]
    if recovered:
        flags.append("second_pass_" + "_".join(recovered))
    if disagreed:
        flags.append("second_pass_disagreed")
    out["flags"] = ",".join(flags)
    out["second_pass_disagreements"] = ";".join(disagreed)
    return out


def summarise(before: Sequence[Dict[str, Any]], after: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """What the second pass added, per field, and where it contradicted the first."""
    out: Dict[str, Any] = {"rows": len(after)}
    for field in RECOVERABLE:
        had = sum(1 for r in before if r.get(field) not in (None, ""))
        has = sum(1 for r in after if r.get(field) not in (None, ""))
        out[field] = {"before": had, "after": has, "recovered": has - had}
    out["disagreements"] = sum(1 for r in after if r.get("second_pass_disagreements"))
    return out


def render(found: Dict[str, Any]) -> str:
    lines = [f"SECOND PASS over {found['rows']:,} rows", ""]
    for field in RECOVERABLE:
        stat = found[field]
        total = found["rows"] or 1
        lines.append(
            f"   {field:<10} {stat['before']:>6,} -> {stat['after']:>6,} "
            f"({stat['before'] / total:.1%} -> {stat['after'] / total:.1%}, "
            f"+{stat['recovered']:,})"
        )
    lines += [
        "",
        f"   rows where the two engines contradicted each other: {found['disagreements']:,}",
        "   the first engine's value was kept in every one of them",
    ]
    return "\n".join(lines)


def parse_reading(payload: Dict[str, Any]) -> Optional[Reading]:
    if "text" not in payload:
        return None
    return Reading(
        text=payload.get("text") or "",
        tokens=int(payload.get("tokens") or 0),
        capped=bool(payload.get("capped")),
    )


def readings_from(payloads: Sequence[Dict[str, Any]]) -> List[Reading]:
    return [r for r in (parse_reading(p) for p in payloads) if r is not None]


class TerseEngine:
    """savitr's distilled roll model, held resident in its own virtualenv.

    Its dependencies conflict with this package's, so it runs as a subprocess and speaks one
    JSON object per line. The model stays loaded between calls, which is the whole point: a
    process per box would pay about a second of model load for six seconds of work, on boxes
    numbering in the tens of thousands.
    """

    def __init__(
        self,
        python: str = ".venv-surya/bin/python",
        worker: str = "scripts/surya_worker.py",
        max_tokens: int = 160,
    ) -> None:
        self._python, self._worker, self._max_tokens = python, worker, max_tokens
        self._process: Optional[Any] = None

    def _start(self) -> Any:
        import subprocess
        from pathlib import Path

        if self._process and self._process.poll() is None:
            return self._process
        if not Path(self._python).exists():
            raise RuntimeError(
                f"{self._python} not found; create it with `uv venv --python 3.12 .venv-surya "
                "&& uv pip install --python .venv-surya/bin/python 'savitr[backend]'`"
            )
        self._process = subprocess.Popen(
            [self._python, self._worker, "--terse", "--max-tokens", str(self._max_tokens)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        ready = self._process.stdout.readline()
        if not ready or "ready" not in ready:
            raise RuntimeError(f"surya worker failed to start: {ready!r}")
        return self._process

    def read(self, image: Any) -> Reading:
        import base64
        import io
        import json as _json

        process = self._start()
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        process.stdin.write(
            _json.dumps({"png": base64.b64encode(buffer.getvalue()).decode("ascii")}) + "\n"
        )
        process.stdin.flush()
        reply = _json.loads(process.stdout.readline())
        if "error" in reply:
            # One unreadable box must not end a run of tens of thousands.
            return Reading(text="", capped=True)
        return parse_reading(reply) or Reading(text="", capped=True)

    def close(self) -> None:
        if self._process and self._process.poll() is None:
            self._process.terminate()
            self._process.wait(timeout=10)
