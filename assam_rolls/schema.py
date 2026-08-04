"""Schema for Assam 2026 electoral roll info pages.

Single source of truth for three things that must never drift apart:

1. ``PART_COLUMNS`` / ``SECTION_COLUMNS`` -- the CSV output headers.
2. ``PAGE1_JSON_SCHEMA`` -- the JSON Schema handed to Claude's structured outputs,
   which guarantees the response parses without regex salvage.
3. ``parse_source_filename`` -- recovers the AC and part number from the PDF filename.
   The publisher encodes both in every filename, so they are independently-known
   ground truth on every page at zero labeling cost (see ``validate.py``).

Text conventions
----------------
Assamese text is stored **verbatim**, including Assamese numerals where the source
uses them (``ps_address`` reads ``৫৯০ বনগাঁও এল. পি. স্কুল, কঠা নং ১, বনগাঁও``).
Each such field has a ``*_roman`` companion holding a model-produced romanization.
The verbatim column is the archival record; ``*_roman`` is a convenience for joining
and is explicitly model-generated -- it is scored separately from the verbatim fields
because transliteration has no single correct answer.

The three categorical fields are normalized to a controlled English vocabulary rather
than transliterated, so they can be filtered on directly.
"""

import re
import unicodedata
from typing import Any, Dict, List, Optional

# --------------------------------------------------------------------------- filename

# 2026-EROLLGEN-S03-100-FinalRoll-Revision1-ASM-45-WI_INFO.pdf
#                   ^AC                          ^part
SOURCE_FILENAME_RE = re.compile(
    r"^(?P<year>\d{4})-EROLLGEN-(?P<state>S\d{2})-(?P<ac_no>\d+)-"
    r"(?P<roll_type>[A-Za-z]+)-Revision(?P<revision>\d+)-"
    r"(?P<lang>[A-Z]+)-(?P<part_no>\d+)-WI_INFO\.pdf$"
)

STATE_CODE = "S03"
STATE_NAME = "Assam"
N_ASSEMBLY_CONSTITUENCIES = 126


def parse_source_filename(filename: str) -> Optional[Dict[str, Any]]:
    """Recover AC number, part number and roll metadata from a source PDF filename.

    Returns ``None`` when the name does not match the publisher's pattern, which is
    itself worth flagging -- an unrecognized name means an unexpected file in the zip.
    """
    match = SOURCE_FILENAME_RE.match(filename.strip())
    if not match:
        return None
    fields = match.groupdict()
    return {
        "year": int(fields["year"]),
        "state": fields["state"],
        "ac_no": int(fields["ac_no"]),
        "part_no": int(fields["part_no"]),
        "roll_type": fields["roll_type"],
        "revision": int(fields["revision"]),
        "lang": fields["lang"],
    }


# -------------------------------------------------------------------- digit normalization

# Assamese uses the Bengali-Assamese numerals, U+09E6..U+09EF. The mapping to Western
# digits is a pure bijection, so it is done in code rather than asked of the model:
# exact, free, and testable. Devanagari (U+0966..U+096F) is included because roll
# addresses occasionally mix it in.
ASSAMESE_DIGITS = "০১২৩৪৫৬৭৮৯"
DEVANAGARI_DIGITS = "०१२३४५६७८९"
_DIGIT_TRANSLATION = {
    **{ord(c): str(i) for i, c in enumerate(ASSAMESE_DIGITS)},
    **{ord(c): str(i) for i, c in enumerate(DEVANAGARI_DIGITS)},
}


def normalize_digits(text: Optional[str]) -> str:
    """Rewrite Assamese/Devanagari numerals as Western digits, leaving all else intact.

    ``'৫৯০ বনগাঁও এল. পি. স্কুল, কঠা নং ১'`` -> ``'590 বনগাঁও এল. পি. স্কুল, কঠা নং 1'``
    """
    if not text:
        return ""
    return str(text).translate(_DIGIT_TRANSLATION)


def first_int(text: Optional[str]) -> Optional[int]:
    """First integer appearing in ``text`` after digit normalization, else ``None``.

    Used to derive ``ward_no_num`` from a verbatim ward label such as ``ৱাৰ্ড নং ৮``.
    """
    match = re.search(r"\d+", normalize_digits(text))
    return int(match.group()) if match else None


def clean_text(text: Optional[str]) -> str:
    """Normalize Unicode to NFC and collapse whitespace, preserving script.

    Assamese combining marks admit multiple encodings for the same grapheme; NFC makes
    verbatim fields comparable across extraction runs and against the gold set.
    """
    if not text:
        return ""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", str(text))).strip()


# ---------------------------------------------------------------- controlled vocabulary

RESERVATION_VALUES = ["GENERAL", "SC", "ST"]
PS_TYPE_VALUES = ["MALE", "FEMALE", "GENERAL"]
CONFIDENCE_VALUES = ["HIGH", "MEDIUM", "LOW"]

# Assamese surface forms, for the prompt and for post-hoc normalization of stragglers.
RESERVATION_FROM_ASSAMESE = {
    "সাধাৰণ": "GENERAL",
    "অনুসূচিত জাতি": "SC",
    "অনুসূচিত জনজাতি": "ST",
}
PS_TYPE_FROM_ASSAMESE = {
    "পুৰুষ": "MALE",
    "মহিলা": "FEMALE",
    "সাধাৰণ": "GENERAL",
    "সাধাৰন": "GENERAL",  # unnormalized spelling seen in AC1
}

# ------------------------------------------------------------------- model-facing fields

#: Fields the model reads off page 1, in the order they appear on the form.
#: (name, json_type, description) -- ``json_type`` is "string", "integer" or an enum list.
MODEL_FIELDS: List[Dict[str, Any]] = [
    # header
    {"name": "ac_no", "type": "integer", "desc": "Assembly constituency number."},
    {"name": "ac_name", "type": "string", "desc": "AC name, verbatim Assamese."},
    {"name": "ac_name_roman", "type": "string", "desc": "Romanization of ac_name."},
    {"name": "ac_reservation", "type": RESERVATION_VALUES, "desc": "AC reservation status."},
    {"name": "pc_no", "type": "integer", "desc": "Parliamentary constituency number."},
    {"name": "pc_name", "type": "string", "desc": "PC name, verbatim Assamese."},
    {"name": "pc_name_roman", "type": "string", "desc": "Romanization of pc_name."},
    {"name": "pc_reservation", "type": RESERVATION_VALUES, "desc": "PC reservation status."},
    {"name": "part_no", "type": "integer", "desc": "Part number (খণ্ড নং), top right."},
    # section 1 -- revision
    {"name": "revision_year", "type": "integer", "desc": "Year of revision (সংশোধনৰ বছৰ)."},
    {
        "name": "qualifying_date",
        "type": "string",
        "desc": "Qualifying date (ভিত্তি তাৰিখ) as ISO YYYY-MM-DD.",
    },
    {
        "name": "revision_type",
        "type": "string",
        "desc": "Type of revision (সংশোধনৰ প্ৰকাৰ), verbatim.",
    },
    {"name": "revision_type_roman", "type": "string", "desc": "English/romanized revision_type."},
    {
        "name": "publication_date",
        "type": "string",
        "desc": "Date of publication (প্ৰকাশনৰ তাৰিখ) as ISO YYYY-MM-DD.",
    },
    {
        "name": "roll_description",
        "type": "string",
        "desc": "Roll identification paragraph (তালিকাৰ চিনাক্তকৰণ), verbatim Assamese.",
    },
    # section 2 -- locality
    {"name": "main_town_village", "type": "string", "desc": "মূল চহৰ/গাঁও, verbatim."},
    {"name": "main_town_village_roman", "type": "string", "desc": "Romanization."},
    {"name": "ward_no", "type": "string", "desc": "ৱাৰ্ড নং, verbatim (may be empty)."},
    {"name": "ward_no_roman", "type": "string", "desc": "Romanization of ward_no."},
    {"name": "post_office", "type": "string", "desc": "ডাকঘৰ, verbatim."},
    {"name": "post_office_roman", "type": "string", "desc": "Romanization."},
    {"name": "police_station", "type": "string", "desc": "পুলিচ থানা, verbatim."},
    {"name": "police_station_roman", "type": "string", "desc": "Romanization."},
    {"name": "block", "type": "string", "desc": "ব্লক (development block / পৌৰসভা), verbatim."},
    {"name": "block_roman", "type": "string", "desc": "Romanization."},
    {"name": "revenue_circle", "type": "string", "desc": "ৰাজহ চক্ৰ (revenue circle), verbatim."},
    {"name": "revenue_circle_roman", "type": "string", "desc": "Romanization."},
    {"name": "district", "type": "string", "desc": "জিলা, verbatim."},
    {"name": "district_roman", "type": "string", "desc": "Romanization."},
    {"name": "pin_code", "type": "integer", "desc": "পিনকোড, 6 digits."},
    # section 3 -- polling station
    {"name": "ps_no", "type": "integer", "desc": "Polling station number."},
    {"name": "ps_name", "type": "string", "desc": "Polling station name, verbatim Assamese."},
    {"name": "ps_name_roman", "type": "string", "desc": "Romanization of ps_name."},
    {"name": "ps_address", "type": "string", "desc": "Polling station address, verbatim."},
    {"name": "ps_address_roman", "type": "string", "desc": "Romanization of ps_address."},
    {"name": "ps_type", "type": PS_TYPE_VALUES, "desc": "Polling station type."},
    {
        "name": "auxiliary_ps_count",
        "type": "integer",
        "desc": "Number of auxiliary polling stations in the part.",
    },
    # section 4 -- electors
    {"name": "start_serial", "type": "integer", "desc": "আৰম্ভনি ক্ৰমিক নং."},
    {"name": "end_serial", "type": "integer", "desc": "শেষ ক্ৰমিক নং."},
    {"name": "electors_male", "type": "integer", "desc": "পুৰুষ."},
    {"name": "electors_female", "type": "integer", "desc": "মহিলা."},
    {"name": "electors_third_gender", "type": "integer", "desc": "৩য় লিঙ্গ."},
    {"name": "electors_total", "type": "integer", "desc": "মুঠ."},
    # footer
    {"name": "total_pages", "type": "integer", "desc": "Total pages of the full roll, footer."},
    {
        "name": "mother_roll_year",
        "type": "integer",
        "desc": (
            "Year of the mother roll named in the roll_description paragraph "
            "(e.g. 2025 in 'পূৰ্বৰ 2025 চনৰ মূল তালিকা'). Null if not stated."
        ),
    },
    # triage -- model self-report, used to route pages to human review
    {
        "name": "template_match",
        "type": "boolean",
        "desc": (
            "True if the page is the standard 4-section info page. False for blank, "
            "rotated, truncated or otherwise anomalous pages."
        ),
    },
    {
        "name": "extraction_confidence",
        "type": CONFIDENCE_VALUES,
        "desc": (
            "Overall legibility of this page. LOW if the scan is faint, skewed or "
            "partly cut off, or if several values had to be guessed."
        ),
    },
    {
        "name": "anomaly_notes",
        "type": "string",
        "desc": (
            "Short English note about anything unusual -- handwritten corrections, "
            "struck-through values, illegible cells, unexpected extra text. Null if none."
        ),
    },
]

MODEL_FIELD_NAMES = [f["name"] for f in MODEL_FIELDS]

#: Provenance columns computed by the pipeline, never by the model.
PROVENANCE_COLUMNS = [
    "source_zip",
    "source_pdf",
    "sha256",
    "ac_no_file",
    "part_no_file",
]

#: Columns derived in code from verbatim model output -- deterministic, no model call.
DERIVED_COLUMNS = [
    "ward_no_num",
    "ps_address_digits",
]

#: QA columns written by ``validate.py``.
QA_COLUMNS = [
    "model",
    "extracted_at",
    "checks_passed",
    "flags",
    "needs_review",
]

PART_COLUMNS = PROVENANCE_COLUMNS + MODEL_FIELD_NAMES + DERIVED_COLUMNS + QA_COLUMNS

SECTION_COLUMNS = [
    "ac_no",
    "part_no",
    "section_no",
    "section_name",
    "section_name_roman",
    "section_name_digits",
]


def derive_columns(row: Dict[str, Any]) -> Dict[str, Any]:
    """Compute ``DERIVED_COLUMNS`` from a part row's verbatim fields.

    Kept deterministic on purpose: digit transliteration is a bijection, so doing it in
    code avoids both the token cost and the failure mode of asking the model for it.
    """
    return {
        "ward_no_num": first_int(row.get("ward_no")),
        "ps_address_digits": normalize_digits(row.get("ps_address")),
    }


# ------------------------------------------------------------------------- JSON Schema


def _property_schema(field: Dict[str, Any]) -> Dict[str, Any]:
    """Build one JSON Schema property, nullable so the model can report an empty cell.

    Nullability uses ``anyOf`` rather than a type union because structured outputs
    validates against the documented subset, which supports ``anyOf`` and ``enum``
    but not numeric or length constraints.
    """
    spec = field["type"]
    if isinstance(spec, list):
        base: Dict[str, Any] = {"type": "string", "enum": spec}
    else:
        base = {"type": spec}
    return {
        "anyOf": [base, {"type": "null"}],
        "description": field["desc"],
    }


def build_page1_json_schema() -> Dict[str, Any]:
    """JSON Schema for one page-1 extraction, for ``output_config.format``."""
    properties = {f["name"]: _property_schema(f) for f in MODEL_FIELDS}
    properties["sections"] = {
        "type": "array",
        "description": (
            "The numbered area list under 'খণ্ডৰ অন্তৰ্গত এলেকা সমূহৰ নম্বৰ আৰু নাম'. "
            "One entry per numbered line, in page order."
        ),
        "items": {
            "type": "object",
            "properties": {
                "section_no": {
                    "anyOf": [{"type": "integer"}, {"type": "null"}],
                    "description": "Leading number of the area line.",
                },
                "section_name": {
                    "type": "string",
                    "description": "Area name, verbatim Assamese.",
                },
                "section_name_roman": {
                    "type": "string",
                    "description": "Romanization of section_name.",
                },
            },
            "required": ["section_no", "section_name", "section_name_roman"],
            "additionalProperties": False,
        },
    }
    return {
        "type": "object",
        "properties": properties,
        "required": MODEL_FIELD_NAMES + ["sections"],
        "additionalProperties": False,
    }


PAGE1_JSON_SCHEMA = build_page1_json_schema()


def empty_part_row() -> Dict[str, Any]:
    """A part row with every column present and empty, so CSV writing never KeyErrors."""
    return {column: "" for column in PART_COLUMNS}
