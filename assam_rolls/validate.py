"""Consistency checks over extracted part rows.

The publisher encodes the AC and part number in every source filename, so those two
fields are independently known on **every** page at zero labeling cost. Checks 1 and 2
below compare them against what the model read off the page: a continuous, corpus-wide
accuracy signal that does not depend on the hand-coded gold set, and the single most
useful number for deciding whether an extraction run is trustworthy.

Checks are either **hard** -- a failure means the row is wrong and must be reviewed --
or **soft** -- a failure is suspicious but can be legitimate. Only hard failures (plus
an explicit low-confidence or off-template self-report) set ``needs_review``.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from .schema import N_ASSEMBLY_CONSTITUENCIES

HARD = "hard"
SOFT = "soft"

#: Assam pincodes occupy the 78xxxx-79xxxx range.
ASSAM_PIN_PREFIXES = ("78", "79")


@dataclass(frozen=True)
class CheckResult:
    name: str
    severity: str
    passed: bool
    detail: str = ""


@dataclass
class CorpusContext:
    """Corpus-level facts the per-row checks compare against.

    Built by ``build_context`` from the whole extraction run, so a single mangled page
    is measured against the consensus rather than against a hardcoded expectation.
    """

    parts_per_ac: Dict[int, int] = field(default_factory=dict)
    modal_qualifying_date: Optional[str] = None
    modal_publication_date: Optional[str] = None
    modal_revision_year: Optional[int] = None
    district_by_ac: Dict[int, str] = field(default_factory=dict)


def _as_int(value: Any) -> Optional[int]:
    """Coerce to int, tolerating the empty strings and floats CSV round-trips produce."""
    if value is None or value == "":
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _modal(values: Iterable[Any]) -> Optional[Any]:
    present = [v for v in values if v not in (None, "")]
    if not present:
        return None
    return Counter(present).most_common(1)[0][0]


def build_context(rows: Iterable[Dict[str, Any]], parts_per_ac: Optional[Dict[int, int]] = None):
    """Derive corpus-level expectations from the extracted rows themselves."""
    rows = list(rows)
    districts: Dict[int, List[str]] = defaultdict(list)
    for row in rows:
        ac_no = _as_int(row.get("ac_no"))
        district = row.get("district")
        if ac_no is not None and district:
            districts[ac_no].append(district)

    return CorpusContext(
        parts_per_ac=dict(parts_per_ac or {}),
        modal_qualifying_date=_modal(r.get("qualifying_date") for r in rows),
        modal_publication_date=_modal(r.get("publication_date") for r in rows),
        modal_revision_year=_modal(_as_int(r.get("revision_year")) for r in rows),
        district_by_ac={
            ac: modal for ac, names in districts.items() if (modal := _modal(names)) is not None
        },
    )


# ------------------------------------------------------------------------------ checks


def check_row(row: Dict[str, Any], context: Optional[CorpusContext] = None) -> List[CheckResult]:
    """Run every check against one part row."""
    context = context or CorpusContext()
    results: List[CheckResult] = []

    ac_no = _as_int(row.get("ac_no"))
    part_no = _as_int(row.get("part_no"))
    ac_no_file = _as_int(row.get("ac_no_file"))
    part_no_file = _as_int(row.get("part_no_file"))

    # -- hard: free ground truth from the filename -----------------------------------
    results.append(
        CheckResult(
            "ac_no_matches_filename",
            HARD,
            ac_no is not None and ac_no == ac_no_file,
            f"page={ac_no} filename={ac_no_file}",
        )
    )
    results.append(
        CheckResult(
            "part_no_matches_filename",
            HARD,
            part_no is not None and part_no == part_no_file,
            f"page={part_no} filename={part_no_file}",
        )
    )

    # -- hard: arithmetic that must hold ---------------------------------------------
    male = _as_int(row.get("electors_male"))
    female = _as_int(row.get("electors_female"))
    third = _as_int(row.get("electors_third_gender"))
    total = _as_int(row.get("electors_total"))
    if male is None or female is None or third is None or total is None:
        results.append(
            CheckResult("gender_sum_matches_total", HARD, False, "missing elector counts")
        )
    else:
        summed = male + female + third
        results.append(
            CheckResult(
                "gender_sum_matches_total",
                HARD,
                summed == total,
                f"{male}+{female}+{third}={summed} vs total={total}",
            )
        )

    start = _as_int(row.get("start_serial"))
    end = _as_int(row.get("end_serial"))
    if start is None or end is None:
        results.append(CheckResult("serial_order", HARD, False, "missing serials"))
    else:
        results.append(CheckResult("serial_order", HARD, end >= start, f"start={start} end={end}"))

    # -- soft: plausible but not guaranteed ------------------------------------------
    if start is not None and end is not None and total is not None:
        span = end - start + 1
        results.append(
            CheckResult(
                "serial_span_covers_total",
                SOFT,
                span >= total,
                f"span={span} total={total}",
            )
        )

    pin = str(row.get("pin_code") or "").strip()
    results.append(
        CheckResult(
            "pin_code_plausible",
            SOFT,
            bool(re.fullmatch(r"\d{6}", pin)) and pin.startswith(ASSAM_PIN_PREFIXES),
            f"pin={pin!r}",
        )
    )

    results.append(
        CheckResult(
            "ac_no_in_range",
            SOFT,
            ac_no is not None and 1 <= ac_no <= N_ASSEMBLY_CONSTITUENCIES,
            f"ac_no={ac_no}",
        )
    )

    expected_parts = context.parts_per_ac.get(ac_no) if ac_no is not None else None
    if expected_parts is not None and part_no is not None:
        results.append(
            CheckResult(
                "part_no_within_ac",
                SOFT,
                1 <= part_no <= expected_parts,
                f"part_no={part_no} of {expected_parts}",
            )
        )

    for name, value, expected in (
        ("qualifying_date", row.get("qualifying_date"), context.modal_qualifying_date),
        ("publication_date", row.get("publication_date"), context.modal_publication_date),
        ("revision_year", _as_int(row.get("revision_year")), context.modal_revision_year),
    ):
        if expected is not None:
            results.append(
                CheckResult(
                    f"{name}_matches_corpus",
                    SOFT,
                    value == expected,
                    f"{value!r} vs modal {expected!r}",
                )
            )

    if ac_no is not None and ac_no in context.district_by_ac:
        expected_district = context.district_by_ac[ac_no]
        results.append(
            CheckResult(
                "district_matches_ac_mode",
                SOFT,
                row.get("district") == expected_district,
                f"{row.get('district')!r} vs modal {expected_district!r}",
            )
        )

    for name in ("ps_name", "district", "ps_address"):
        results.append(CheckResult(f"{name}_present", SOFT, bool(str(row.get(name) or "").strip())))

    # -- soft: the model's own triage self-report ------------------------------------
    template_match = row.get("template_match")
    results.append(
        CheckResult(
            "template_match",
            SOFT,
            template_match in (True, "True", "true", ""),
            f"template_match={template_match!r}",
        )
    )
    confidence = str(row.get("extraction_confidence") or "").upper()
    results.append(
        CheckResult(
            "confidence_not_low",
            SOFT,
            confidence != "LOW",
            f"confidence={confidence or 'unset'}",
        )
    )

    return results


def summarize(results: List[CheckResult]) -> Dict[str, Any]:
    """Collapse check results into the QA columns written to ``parts.csv``."""
    failed = [r for r in results if not r.passed]
    hard_failed = [r for r in failed if r.severity == HARD]
    off_template = any(r.name == "template_match" and not r.passed for r in results)
    low_confidence = any(r.name == "confidence_not_low" and not r.passed for r in results)

    return {
        "checks_passed": sum(1 for r in results if r.passed),
        "checks_total": len(results),
        "flags": ";".join(sorted(r.name for r in failed)),
        "needs_review": bool(hard_failed) or off_template or low_confidence,
    }


def validate_rows(
    rows: Iterable[Dict[str, Any]], parts_per_ac: Optional[Dict[int, int]] = None
) -> List[Dict[str, Any]]:
    """Validate every row in place and return them with QA columns populated."""
    rows = list(rows)
    context = build_context(rows, parts_per_ac=parts_per_ac)
    for row in rows:
        row.update(summarize(check_row(row, context)))
    return rows


def _summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    def rate(name: str) -> float:
        hits = sum(1 for r in rows if name not in str(r.get("flags", "")).split(";"))
        return round(hits / len(rows), 4)

    return {
        "n": len(rows),
        "grid_detected": round(
            sum(1 for r in rows if r.get("template_match") is not False) / len(rows), 4
        ),
        "ac_no_agreement": rate("ac_no_matches_filename"),
        "part_no_agreement": rate("part_no_matches_filename"),
        "gender_sum_agreement": rate("gender_sum_matches_total"),
        "needs_review": sum(1 for r in rows if r.get("needs_review")),
        "clean_rows": sum(1 for r in rows if not str(r.get("flags", "")).strip()),
    }


def accuracy_report(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Corpus-level quality summary, driven by the filename ground truth.

    ``ac_no_agreement`` and ``part_no_agreement`` are true accuracy figures -- measured
    against known values on every page -- not estimates.

    The same figures are also reported **per language**. The corpus is 88% Assamese, so a
    collapse confined to the 13 Bengali constituencies or the single English one would
    move the overall numbers by a fraction of a percent and pass unnoticed. Each language
    is read with its own Tesseract model and its own derived label table, so each can fail
    independently and has to be measured independently.
    """
    rows = list(rows)
    if not rows:
        return {"n": 0}

    report = _summary(rows)
    by_language: Dict[str, Any] = {}
    for row in rows:
        by_language.setdefault(str(row.get("lang") or "UNKNOWN"), []).append(row)
    report["by_language"] = {code: _summary(group) for code, group in sorted(by_language.items())}
    return report
