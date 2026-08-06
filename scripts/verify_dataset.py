"""Verify the shipped dataset against its own promises.

Run after ``assam-rolls build``. Everything here is a property the documentation claims,
checked against the file that was actually written rather than against the pipeline's
in-memory state -- if the writer drops or mangles something, only reading the output back
will show it.

    .venv/bin/python scripts/verify_dataset.py
"""

from __future__ import annotations

import csv
import hashlib
import json
import random
import sys
import unicodedata
import zipfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assam_rolls.output import read_jsonl  # noqa: E402
from assam_rolls.parse import ALL_LOCALITY_FIELDS  # noqa: E402

OUT = Path("out")
EXPECTED_TOTAL = 31486
EXPECTED_BY_LANG = {"ASM": 27683, "BEN": 3542, "ENG": 261}
#: Printed by one edition only; blank elsewhere, and blank is not a failure to read.
EDITION_ONLY = {"gram_panchayat": "BEN", "subdivision": "ENG"}

ok = True


def check(label: str, passed: bool, detail: str = "") -> None:
    global ok
    ok = ok and passed
    print(f"  [{'PASS' if passed else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


records = read_jsonl(OUT / "parts.jsonl")
by_lang = Counter(r.get("lang") for r in records)
print(f"\n{len(records):,} records\n")

print("COVERAGE")
check("every part present", len(records) == EXPECTED_TOTAL, f"{len(records):,}/{EXPECTED_TOTAL:,}")
check("all 126 constituencies", len({r["ac_no_file"] for r in records}) == 126)
for lang, expected in EXPECTED_BY_LANG.items():
    check(f"{lang} complete", by_lang.get(lang) == expected, f"{by_lang.get(lang, 0):,}/{expected:,}")

print("\nPROVENANCE")
always_empty = [
    c
    for c in records[0]
    if c != "sections" and all(r.get(c) in (None, "") for r in records)
]
check(
    "no column empty across every row (bar anomaly_notes)",
    set(always_empty) <= {"anomaly_notes"},
    f"empty: {always_empty}" if always_empty else "",
)
keys = Counter((r["source_zip"], r["source_pdf"], r["part_no_file"]) for r in records)
check("(source_zip, source_pdf, part_no_file) unique", not [k for k, n in keys.items() if n > 1])
check(
    "engine_version names the language's own model",
    all(r["engine_version"].split("(")[-1].startswith(r["lang"].lower()[:3]) for r in records),
)
check(
    "no cross-language contamination",
    all(
        (r["lang"] == "ASM") == ("asm=" in r["engine_version"])
        for r in records
    ),
)

print("\nSTITCHING (random sample, using only what each row carries)")
random.seed(0)
for record in random.sample(records, 6):
    archive = Path(record["source_zip_dir"]) / record["source_zip"]
    with zipfile.ZipFile(archive) as zf:
        payload = zf.read(record["source_pdf"])
    check(
        f"{record['lang']} AC{record['ac_no_file']}/{record['part_no_file']}",
        hashlib.sha256(payload).hexdigest() == record["pdf_sha256"]
        and len(payload) == record["pdf_bytes"],
    )

print("\nENCODING")
check("parts.csv carries the UTF-8 BOM", (OUT / "parts.csv").read_bytes().startswith(b"\xef\xbb\xbf"))
text_fields = ("district", "ac_name", "ps_address", "main_town_village", "gram_panchayat")
bad_nfc = [
    (r["ac_no_file"], f)
    for r in records
    for f in text_fields
    if isinstance(r.get(f), str) and r[f] != unicodedata.normalize("NFC", r[f])
]
check("every text field is NFC", not bad_nfc, f"{len(bad_nfc)} not NFC")
check("scripts written literally, not escaped", "\\u" not in (OUT / "parts.jsonl").read_text("utf-8"))

print("\nSCHEMA ACROSS LANGUAGES")
columns = {tuple(sorted(k for k in r if k != "sections")) for r in records}
check("every row has identical columns", len(columns) == 1, f"{len(columns)} distinct column sets")
for field, lang in EDITION_ONLY.items():
    printed = sum(1 for r in records if r["lang"] == lang and r.get(field))
    elsewhere_null = [r for r in records if r["lang"] != lang and r.get(field) is None]
    check(f"{field} populated for {lang}", printed > 0, f"{printed:,} rows")
    check(
        f"{field} blank (not null) for other languages",
        not elsewhere_null,
        f"{len(elsewhere_null)} nulls",
    )

print("\nNULL CONVENTION")
numeric = ("part_no", "start_serial", "end_serial", "electors_male", "electors_female",
           "electors_third_gender", "electors_total", "pin_code", "ac_no", "pc_no")
nulls = {k: sum(1 for r in records if r.get(k) is None) for k in numeric}
check("no unread numeric fields", not any(nulls.values()), str({k: v for k, v in nulls.items() if v}))
blank_ward = sum(1 for r in records if r.get("ward_no") == "")
check("rural parts read blank, not null", blank_ward > 0, f"{blank_ward:,} blank wards")

print("\nARITHMETIC (independent of OCR quality claims)")
sums = sum(
    1
    for r in records
    if None not in (r["electors_male"], r["electors_female"], r["electors_third_gender"], r["electors_total"])
    and r["electors_male"] + r["electors_female"] + r["electors_third_gender"] == r["electors_total"]
)
check("male+female+third == total", sums / len(records) > 0.995, f"{sums/len(records):.3%}")
matched = sum(1 for r in records if r["ac_no"] == r["ac_no_file"])
check("ac_no matches the filename", matched / len(records) > 0.999, f"{matched/len(records):.3%}")

print("\nSECTIONS")
nested = sum(len(r["sections"]) for r in records)
with (OUT / "part_sections.csv").open(encoding="utf-8-sig", newline="") as handle:
    flat = list(csv.DictReader(handle))
check("nested and flat section counts agree", nested == len(flat), f"{nested:,} vs {len(flat):,}")

report = json.loads((OUT / "report.json").read_text(encoding="utf-8"))
check("report.json carries the per-language breakdown", "by_language" in report)

print(f"\n{'ALL CHECKS PASSED' if ok else 'FAILURES ABOVE'}\n")
raise SystemExit(0 if ok else 1)
