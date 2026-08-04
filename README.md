# Assam 2026 Electoral Roll — Part-Level Info Pages

Extracts the per-part **info pages** of the Assam 2026 Final Electoral Roll into a
tidy, part-level dataset: polling station, locality, and elector counts for every part
in every assembly constituency.

These are **not** the elector lists. Each part's roll opens with a two-page cover sheet
describing the polling station; this repository parses that cover sheet.

## Why this needs OCR

The source PDFs carry **no text layer**. Each page is a single embedded 1187×1679 RGB
image at 144 dpi:

```console
$ pdftotext -layout part.pdf -   # 2 bytes
$ pdffonts part.pdf              # no fonts
$ pdfimages -list part.pdf       # 1 image per page, 1187x1679, 144 dpi
```

So extraction runs a vision model over the page image rather than parsing text. The
text is Assamese (Bengali-Assamese script) with mixed Western (`2026`, `783350`) and
Assamese (`৫৯০`, `১`) numerals.

## What gets extracted

Page 1 is a rigid four-section form, stable across rural and urban constituencies:

| Section | Fields |
|---|---|
| Header | AC no/name/reservation, PC no/name/reservation, part no |
| 1 — revision | year, qualifying date, revision type, publication date, roll description, mother roll year |
| 2 — locality | numbered area list, main town/village, ward, post office, police station, block, revenue circle, district, pincode |
| 3 — polling station | number, name, address, type, auxiliary station count |
| 4 — electors | start/end serial, male, female, third gender, total |
| Footer | total pages of the full roll |

Output is two tables:

- **`parts.csv`** — one row per part.
- **`part_sections.csv`** — one row per numbered area within a part (the list is
  one-to-many, so it does not belong in a wide row).

### Text conventions

Assamese is stored **verbatim**, including Assamese numerals — `ps_address` reads
`৫৯০ বনগাঁও এল. পি. স্কুল, কঠা নং ১`. Each such field has:

- a `*_roman` companion — a model-produced romanization, for joining. Explicitly
  model-generated, and scored separately from the verbatim fields because
  transliteration has no single correct answer.
- where numerals matter, a `*_digits` companion produced **in code**, not by the model:
  Assamese→Western digit mapping is a pure bijection, so it is done deterministically
  (`ward_no_num`, `ps_address_digits`, `section_name_digits`).

The three categorical fields are normalized rather than transliterated:
`ac_reservation`/`pc_reservation` → `GENERAL`/`SC`/`ST`, `ps_type` →
`MALE`/`FEMALE`/`GENERAL`.

## Quality: ground truth on every page, for free

Every source filename encodes the AC and part number:

```
2026-EROLLGEN-S03-100-FinalRoll-Revision1-ASM-45-WI_INFO.pdf
                  └── AC 100                     └── part 45
```

The model **never sees the filename** — it reads both numbers off the image. Comparing
the two gives a true accuracy measurement on *every page in the corpus*, not just on a
sampled gold set. Together with the elector arithmetic
(`male + female + third_gender == total`), that is four independent hard checks per
page.

Checks are **hard** (a failure means the row is wrong) or **soft** (suspicious but
legitimately possible). Only hard failures — plus an explicit low-confidence or
off-template self-report from the model — set `needs_review`.

`assam-rolls review` renders flagged rows as an HTML sheet with the page image beside
the extracted values.

## Install

Requires [poppler](https://poppler.freedesktop.org/) and Python 3.10+.

```bash
brew install poppler          # macOS; Debian: apt-get install poppler-utils
make install                  # uv venv + editable install with dev extras
export ANTHROPIC_API_KEY=...
```

## Run

```bash
assam-rolls render                      # zips        -> out/pages/{ac}-{part}.png
assam-rolls extract                     # pages       -> Batch API (50% cheaper)
assam-rolls collect --wait              # batches     -> out/raw/*.json
assam-rolls build                       # raw JSON    -> parts.csv + report.json
assam-rolls review                      # flagged rows-> out/review.html
```

Every stage is resumable and idempotent, keyed on `{ac:03d}-{part:04d}`. For a quick
pilot without the batch round trip:

```bash
assam-rolls render  --limit 5
assam-rolls extract --limit 5 --sync
assam-rolls build
```

## Development

```bash
make test        # pytest
make lint        # black --check, isort --check-only, flake8
make ci          # both
make ci-docker   # the above in a standard python:3.12 image
```

## Status

Phase 1 (page 1, the four constituencies currently on disk) is in progress. Page 2 —
polling-station imagery, GPS coordinates, and facilities — is deliberately sequenced
**after** page 1 ships; its CAD drawings are non-standard across districts and need a
coverage survey before a schema can be fixed.

## Known limitations

- Source is 144 dpi scanned imagery. There is no text layer to cross-check against, so
  accuracy is measured, not assumed.
- `*_roman` fields are model-generated and unverified; the verbatim column is the record.
- Page-2 CAD drawings vary by district: GPS coordinates are present in only some, and
  the facilities table has several incompatible schemas. Coverage will be reported
  rather than assumed.
- CAD headers carry **pre-delimitation** AC numbers (AC 1 Gossaigaon appears as "28").
  When page 2 is parsed these are captured as a separate `ac_no_predelimitation`
  column, never merged into `ac_no`.

## Related

- [`in-rolls/parse_unsearchable_rolls`](https://github.com/in-rolls/parse_unsearchable_rolls) — shared roll-parsing core
- [`in-rolls/electoral_rolls`](https://github.com/in-rolls/electoral_rolls) — scrapers
- [`in-rolls/savitr`](https://github.com/in-rolls/savitr) — Surya OCR on Apple Silicon
