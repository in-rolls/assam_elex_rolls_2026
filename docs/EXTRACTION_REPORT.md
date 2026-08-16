# Assam 2026 electoral rolls: extraction report

Release `v2026.2` contains 26,043,350 elector rows from all 31,486 parts and all 126
constituencies in Assam's 2026 final electoral roll. Every row in the release corresponds to one
stage-one elector box, and every part corresponds to one filename-derived record in the
independently extracted info-page dataset. The release gate checks both equalities before it
builds the statewide Parquet file.

All figures below were recomputed from the release candidate on 16 August 2026. Accuracy against
human transcription has not been measured, so field fill and structural checks must not be read
as field accuracy.

## Data and identity

The verified release candidate is `assam_electoral_rolls_2026.parquet`. Its permanent download
location will be recorded here when `v2026.2` is tagged. It is 613,565,603 bytes and has this
SHA-256:

```text
f4e7072c11c9838339a3cef073d90ad544213405e80c827aa96b23599f4b68a2
```

The local release workspace keeps 126 constituency shards under `data/electors/`. Each shard has an
`entry.json` with its checksum and reconciliation metrics and a `verify.json` with part-level box
counts and readable closing totals. The committed manifest records the corresponding checksums and
reconciliation metrics without placing the large shards in Git.

One row is one printed elector box. The main extracted fields are `name`, `relation_name`,
`relation_type`, `house_no`, `age`, `sex`, and `epic_no`. `roll_section` distinguishes the main
list, additions, and deletion sheets. `status_code` and `deleted` record the roll's struck-off
marking when the header reader detected it. The source fields identify the exact PDF, page, row,
and column. `engine` is `google-cloud-vision+tesseract`: Google Cloud Vision reads the elector
body fields, while Tesseract reads the EPIC, serial, status, section header, and closing summary.

Join elector rows to the info-page dataset with:

```text
electors.(ac_no, part_no) = parts.(ac_no_file, part_no_file)
```

The filename-derived fields on the right are the keys. The OCR-read `ac_no` and `part_no` on the
info pages are validation fields.

## Coverage and reconciliation

| Edition | Constituencies | Rows | Name filled | Relation filled | House filled | Age filled | Sex filled | EPIC filled |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Assamese | 112 | 22,913,977 | 99.52% | 97.76% | 96.45% | 98.49% | 97.79% | 99.48% |
| Bengali | 13 | 2,968,343 | 99.27% | 97.42% | 92.83% | 98.45% | 95.92% | 99.44% |
| English | 1 | 161,030 | 99.97% | 97.66% | 98.31% | 98.38% | 98.34% | 99.97% |

The dataset has 25,266,031 main-list rows, 777,310 addition rows, and 9 rows from genuine
deletion sheets. All 26,043,350 source-box keys are unique. The 31,486 parts have 31,486 distinct
source PDF hashes.

The release gate checks field fill within each part, not only across a constituency. The lowest
part-level rates are 85.95% for name, 69.46% for relation, 55.09% for house number, 72.62% for
age, 72.98% for sex, and 66.85% for EPIC. No part falls below the 50% release floor.

Every part has exact agreement between its stage-one boxes and final rows. This is the primary
row-completeness check because it does not depend on OCR text.

Closing totals provide a second, less complete check. The pipeline obtained a usable printed
main-list total for 27,836 parts, or 88.4% of all parts. Of those, 27,315 parts, or 98.1%, match
exactly. The other 521 usable totals differ by one or two rows and have a combined absolute
discrepancy of 796 rows, 0.0031% of the release. The remaining 3,650 closing totals were unreadable
or contradicted the independently counted box geometry, so the report labels those parts
unmeasured instead of treating a suspect OCR total as truth. All 261 parts in the rebuilt English
constituency have usable totals and match exactly.

The info pages publish a statewide net count of 24,958,139. Removing the 9 deletion-sheet rows
and the 1,008,607 rows with a detected status code leaves 25,034,734 rows, which is 76,595 above
the published net count. The roll's arithmetic implies 1,085,202 struck-off entries, so the status
reader detected 92.9% of the implied total. Users must treat `deleted == False` as an approximation
of live status, not proof that an elector remains on the roll.

## Values known to be wrong

The checks in `electors/floor.py` identify values that cannot be right without consulting a page.
They give lower bounds for specific errors, not an overall accuracy estimate. For a valid EPIC
seen `n` times within one constituency, the repeated-EPIC count uses `n - 1`, the minimum number
of rows that must be wrong.

| Detector | Assamese | Bengali | English |
|---|---:|---:|---:|
| Malformed EPIC | 3.065% | 1.986% | 0.939% |
| Minimum wrong from repeated EPIC | 0.813% | 0.826% | 0.417% |
| Printed label at an edge of a value | 0.684% | 0.674% | 0.574% |
| Name equals relation name | 0.011% | 0.009% | 0.017% |
| Latin letters in a non-English name | 0.002% | 0.001% | not applicable |
| Age outside 18 to 120 | 0.000% | 0.000% | 0.000% |

These rates overlap. Adding them would double-count some rows. A row that passes every detector
is still unmeasured: a plausible but wrong name, age, or house number can pass all structural
checks. The repository has review-sheet and scoring tools, but no completed held-out human sample
supports a statewide field-accuracy claim.

## Corrections in v2026.2

The previous artifact had 26,043,942 rows. The new total is 592 lower because several corrections
changed what counted as a real elector row:

1. AC113 was rebuilt with English-aware page and field parsing. Its old 161,750 rows had blank
   substantive fields and included pages that did not belong in the elector grid. The rebuilt
   shard has 161,030 rows, exact box equality, exact main-list totals in all 261 parts, and 98.31%
   house-number fill.
2. Four malformed page grids in AC24 part 95, AC35 part 132, AC45 part 199, and AC49 part 287 had
   dropped 128 genuine boxes. The page classifier now validates repeated box and text-column
   widths and inherits the part's established geometry when local rules are impossible.
3. A section marker matched the suffix `-abad` inside place names and mislabeled 15,102 main-list
   rows as deletion-sheet rows. Section markers now require script boundaries on both sides. Nine
   genuine deletion-sheet rows remain.
4. Canonical serial numbers now continue through supplements. The release also sets 29,042 legacy
   OCR serials outside 1 to 9,999 to null instead of retaining impossible values.
5. Source identity and joins now use filename-derived AC and part numbers consistently. This
   recovered accurate part metadata for constituencies whose OCR-read info-page identifiers were
   incomplete.
6. The independent release review found that a stage-one cache could reuse serial, EPIC, and
   status reads after a box rectangle changed. The release re-read all 111 exposed headers from
   their exact source PDFs. It corrected 106 headers, including 70 OCR serial values, 47 EPIC
   values, and one status code. Cache reuse now requires identical source bytes and identical
   serial/status and EPIC crop rectangles.
7. The same review found that row provenance attributed all fields to Tesseract even though
   Google Cloud Vision reads the elector body. All shards now record both engines explicitly,
   and the release gate requires the corrected value.

The data audit also found a house-number limitation that remains. Some Assamese parts use
alphanumeric or address-like identifiers that the high-precision parser rejects. AC37 part 133
has the lowest house-number fill at 55.09%. Broadening the parser without a labeled benchmark
could replace missing values with wrong ones, so this release records the limitation rather than
making an unmeasured regex change.

## Release checks

Run the release gate with:

```bash
python -m electors release \
  --shards data/electors \
  --parts dataset/parts.jsonl.gz \
  --out data/assam_electoral_rolls_2026.parquet
```

The command refuses to build unless all of these conditions hold:

1. The directory contains exactly `AC001.parquet` through `AC126.parquet`.
2. The shards contain exactly the 31,486 filename-derived part keys in the info-page dataset.
3. Every shard checksum, byte count, row count, and reconciliation field matches its entry file.
4. Every part's stage-one box count equals its Parquet row count.
5. Source-box keys, canonical serials, source order, and field domains satisfy their uniqueness
   and range constraints. Source PDF hashes are unique statewide, including across constituencies.
6. Every source filename agrees with the row's AC, part, language, roll type, revision, and year.
7. Required fields meet the 50% floor in every part, and deletion, status, flag, and timestamp
   invariants hold on every row.
8. The assembled file has one row group per constituency and each row group equals its source
   shard as an Arrow table.

The build writes a temporary file, verifies it from disk, computes its SHA-256, and only then
replaces the destination. The test suite includes fixtures that make each release invariant fail.

## Remaining limits

The release establishes coverage and internal consistency. It does not establish transcription
accuracy. Three limits matter most:

- The status reader misses about 7.1% of struck-off entries implied by the roll's arithmetic.
  Recovering row identity requires re-reading the serial cells or measuring a watermark detector.
- Malformed and repeated EPICs remain concentrated in shards processed before the stricter EPIC
  header reader. Repair requires source-image reprocessing, not string substitution.
- A separate statewide comparison found 77,438 well-formed EPIC values in more than one
  constituency, involving 93,817 occurrences beyond one per value. This check cannot distinguish
  an OCR collision from a duplicate already printed in the source rolls, so the per-language
  table reports only within-constituency collisions and does not assign these rows to an edition.
- Cost totals are incomplete because early re-parses overwrote timing and OCR billing metadata in
  some entry files. The historical estimate remains roughly $450 to $500 for compute and $130 to
  $150 for Vision, but these are not release-grade measurements.
