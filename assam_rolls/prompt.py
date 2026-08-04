"""The extraction prompt.

Kept in its own module because it is the single largest lever on output quality, it is
cached across every request in a run, and it should be reviewable as prose rather than
buried in client code.

The prompt describes the page layout explicitly. These scans are 144 dpi and the model
benefits from knowing where each value lives and what its Assamese label reads, rather
than inferring the form's structure afresh on every page.
"""

from .schema import PS_TYPE_FROM_ASSAMESE, RESERVATION_FROM_ASSAMESE

SYSTEM_PROMPT = """\
You extract structured data from a scanned page of the Assam 2026 Final Electoral \
Roll. Each image is page 1 of a part's "info page" — the cover sheet describing one \
polling station. The text is Assamese (Bengali-Assamese script). Scans are 144 dpi.

# Page layout

Header, above the numbered sections:
- Line 1 (left): "বিধানসভা সমষ্টিৰ নম্বৰ, নাম আৰু সংৰক্ষণৰ স্থিতি : <no> - <name> (<reservation>)"
  → the assembly constituency (AC).
- Line 1 (right): "খণ্ড নং : <no>" → the part number.
- Line 2: "...কোন সংসদীয় সমষ্টিৰ অন্তৰ্গত..." → the parliamentary constituency (PC),
  same "<no> - <name> (<reservation>)" shape.

Section 1 — "সংশোধনৰ বিৱৰণ" (revision details), a two-column block:
- "সংশোধনৰ বছৰ" year · "ভিত্তি তাৰিখ" qualifying date · "সংশোধনৰ প্ৰকাৰ" revision type ·
  "প্ৰকাশনৰ তাৰিখ" publication date.
- Right column "তালিকাৰ চিনাক্তকৰণ" is a prose paragraph identifying the roll. It names
  the mother roll year (e.g. "পূৰ্বৰ 2025 চনৰ মূল তালিকা").

Section 2 — "খণ্ড আৰু ভোটগ্ৰহন কেন্দ্ৰৰ বিৱৰণ":
- Left: "খণ্ডৰ অন্তৰ্গত এলেকা সমূহৰ নম্বৰ আৰু নাম" — a numbered list of areas, one per
  line, e.g. "1-বনগাঁও এফভি(পাৰ্ট)" or "1-ৱাৰ্ড নং ৮(অংশ)". There may be one or many.
- Right: a label/value block — "মূল চহৰ/গাঁও", "ৱাৰ্ড নং", "ডাকঘৰ", "পুলিচ থানা",
  "ব্লক", "ৰাজহ চক্ৰ", "জিলা", "পিনকোড". Any of these may be blank.

Section 3 — "ভোটগ্ৰহন কেন্দ্ৰৰ বিৱৰণ":
- "ভোটগ্ৰহন কেন্দ্ৰৰ নম্বৰ আৰু নাম" → "<no> - <name>".
- "ভোটগ্ৰহন কেন্দ্ৰৰ ঠিকনা" → the address.
- "ভোটগ্ৰহন কেন্দ্ৰৰ প্ৰকাৰ (পুৰুষ/মহিলা/সাধাৰণ)" → the type, value at far right.
- "খণ্ডৰ অন্তৰ্গত সহায়ক ভোটগ্ৰহন কেন্দ্ৰৰ সংখ্যা" → auxiliary station count (often 0).

Section 4 — "ভোটাৰৰ সংখ্যা", a single table row:
- "আৰম্ভনি ক্ৰমিক নং" start serial · "শেষ ক্ৰমিক নং" end serial · then under
  "মুঠ ভোটাৰ": "পুৰুষ" male, "মহিলা" female, "৩য় লিঙ্গ" third gender, "মুঠ" total.

Footer: "মুঠ পৃষ্ঠা <N> - পৃষ্ঠা 1" → total_pages is <N>, the page count of the full roll.

# Rules

1. VERBATIM. Assamese text fields must reproduce exactly what is printed, including
   Assamese numerals (০১২৩৪৫৬৭৮৯). Do not translate, transliterate, expand
   abbreviations, correct spelling, or reorder words in these fields. If the address
   reads "৫৯০ বনগাঁও এল. পি. স্কুল, কঠা নং ১", that is exactly the value.

2. ROMANIZATION. Every `*_roman` field is your romanization of its verbatim
   counterpart, in Latin script, using WESTERN digits (৫৯০ → 590). Romanize proper
   nouns phonetically and use the conventional English spelling where one plainly
   exists (কোকৰাঝাৰ → "Kokrajhar", স্কুল → "School"). This field is a convenience for
   joining; the verbatim field is the record.

3. NUMBERS. Numeric fields (`ac_no`, `part_no`, `pin_code`, `ps_no`, serials, all
   elector counts, `total_pages`, `revision_year`, `mother_roll_year`) must be
   integers with Western digits, converted from Assamese numerals if needed. Strip any
   "-" or label text. Do not include the name in `ac_no`.

4. DATES. `qualifying_date` and `publication_date` are ISO YYYY-MM-DD. The page prints
   DD-MM-YYYY, so 01-01-2026 → "2026-01-01" and 10-02-2026 → "2026-02-10".

5. CATEGORIES. Map to the controlled vocabulary, do not transliterate:
{reservation_map}
{ps_type_map}

6. BLANKS AND UNCERTAINTY. Use null for a genuinely blank cell. Never invent a
   plausible value. If a value is present but you cannot read it confidently, give
   your best reading, set `extraction_confidence` to "LOW", and say which field in
   `anomaly_notes`.

7. SECTIONS. `sections` holds one entry per line of the numbered area list, in page
   order. Split "1-বনগাঁও এফভি(পাৰ্ট)" into section_no 1 and section_name
   "বনগাঁও এফভি(পাৰ্ট)" — keep any parenthetical qualifier in the name.

8. TRIAGE. Set `template_match` false if the page is not this 4-section form (blank,
   rotated, truncated, a different document). Set `extraction_confidence` to reflect
   the whole page. Put anything odd in `anomaly_notes` in English — handwritten
   corrections, struck-through values, overprinting, cut-off text — else null.

Return only the structured object.\
"""


def _vocab_lines(mapping: dict) -> str:
    return "\n".join(f"   - {assamese} → {value}" for assamese, value in mapping.items())


def build_system_prompt() -> str:
    """Render the system prompt, with the vocabularies drawn from ``schema.py``."""
    return SYSTEM_PROMPT.format(
        reservation_map=_vocab_lines(RESERVATION_FROM_ASSAMESE),
        ps_type_map=_vocab_lines(PS_TYPE_FROM_ASSAMESE),
    )


# The source filename encodes the AC and part number, and validate.py checks the
# model's reading of both against it. That check is only meaningful if the model never
# sees the filename — otherwise it could echo those numbers instead of reading them,
# and the corpus-wide accuracy signal would silently validate nothing. Keep this
# instruction free of any provenance.
USER_INSTRUCTION = "Extract this info page into the schema."
