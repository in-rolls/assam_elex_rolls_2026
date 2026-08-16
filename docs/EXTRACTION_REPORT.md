# Assam 2026 Electoral Rolls — Extraction Report

All 126 assembly constituencies of the Assam 2026 final electoral roll, extracted from the
published scanned PDFs into one Parquet dataset: **26,043,942 elector rows**. This report says
what the dataset is, how complete it is, how wrong it is known to be, and what it cost. Every
figure here was computed from the shipped files by `scripts/final_measure` logic on 2026-08-15;
nothing is quoted from memory of intermediate runs.

## Where the data is

| Copy | Location | Contents |
|---|---|---|
| Authoritative | `gs://sawasdee-assam-rolls/shards/` | `AC001.parquet` … `AC126.parquet` (611 MB) plus per-AC `*.entry.json` (counts, reconciliation, cost) and `*.verify.json` |
| Local | `data/electors/` in this repo | The same 126 files, byte-verified against the bucket |

Load it:

```python
import pandas as pd, glob
df = pd.concat(pd.read_parquet(f) for f in sorted(glob.glob("data/electors/AC*.parquet")))
```

One row is one printed elector box. The columns that matter first: `name`, `relation_name`,
`relation_type`, `age`, `sex`, `house_no`, `epic_no` (the voter-ID the roll prints),
`roll_section` (`main` / `addition` / `deletion`), `deleted` (the roll's own struck-off marking),
and provenance down to the page: `ac_no`, `part_no`, `page_no`, `box_row`, `box_col`,
`source_pdf`, `pdf_sha256`. `flags` records repairs the pipeline made; `serial_no_ocr` is an
independently read copy of the serial for checking row order.

## Completeness

```
rows extracted            26,043,942
  Assamese  (114 ACs)     23,440,172
  Bengali    (11 ACs)      2,442,020
  English     (1 AC)         161,750

by section                main 25,253,806   addition 775,025   deletion 15,111
marked struck-off          1,008,591

state's published net     24,958,139
```

The extraction exceeds the published net figure because it keeps what the net figure removes:
supplement entries and electors the roll itself marks deleted. Filter `roll_section == "main"`
and `deleted == False` to approximate the net roll.

**Reconciliation against the roll's own arithmetic.** Every part prints its closing total, a
number this pipeline did not produce. Of 20,092 extracted parts, 19,259 could be measured against
it and **18,688 (97.0%) match exactly**. 46 constituencies match on every single measured part.
Where a part disagrees, the shortfall is recorded per constituency in
`entry.json:rows_short_of_printed`; the recorded total is **1,450 rows across 76 constituencies —
0.0056%** of the dataset, with the worst constituency (AC110) at 0.100%.

## Accuracy — what is proven, what is measured, what is not

Two different claims, deliberately separated.

**A lower bound on error, from the data alone** (`electors/floor.py`): rows wrong in ways that
need no ground truth — an identifier that repeats though unique by definition, a name in the
wrong script, a printed label leaked into a value. Over the final dataset:

```
                          Assamese      Bengali      English
malformed EPIC             3.009%       2.292%       1.312%
repeated EPIC              1.480%       1.299%       0.790%
label inside a value       0.678%       0.729%       0.000%
name equals relation       0.011%       0.008%       0.000%
latin in name              0.002%       0.000%       0.000%
impossible age             0.000%       0.000%       0.000%
```

The EPIC rates are the dataset's weakest point and are concentrated by history: a tesseract
charset defect fixed mid-run left ~46 early constituencies with the old 4.8% malformed rate,
while constituencies processed after the fix run at ~0%. Re-reading the affected constituencies
was measured at ~$150 of compute and deliberately deferred.

**Accuracy against ground truth is not yet measured.** A row no detector condemns is unmeasured,
not correct — only human marking can catch a plausible wrong name. The instrument exists
(`out/review_sheet.html`, 150 stratified crops, scored by `scripts/score_review.py`) and is
waiting to be marked. Until then this report makes no claim about name or age accuracy beyond the
floor above.

## Known defects, each with its bound

- **Sparse final pages** — a part whose last main-list page holds a few electors sometimes loses
  it to page classification. Bounded at ≤0.1% per constituency, recorded per entry; ~1,450 rows
  statewide. Recovery needs a re-render; not done.
- **EPIC quality split by era** — above.
- **Label leak** (~0.68%) — a printed field label at the edge of a value; detectable per row by
  `floor.LEAKED`.
- **Lost cost metadata** — a re-parse bug destroyed `vision_usd`/`stage1_seconds` for 63 early
  entries; recorded Vision spend ($103.48) therefore undercounts. Bucket versioning was off;
  unrecoverable.

## What it cost, and what broke

Compute: 4× `n2d-standard-32` on demand, roughly 60+ machine-hours of real work, ~$450–500 all
in, of which roughly $200 was idle time before utilisation was fixed. Vision: ~$130–150 true
(with the repack design reducing billed images ~9×; $103 of it still on record).

The run's failures are documented in commit messages; the ones that changed the design:

1. Resume treated a part with cached OCR words but no image as unprepared — every reboot
   re-rendered the fleet's whole backlog. (Cached words now suffice.)
2. An exact-match publish gate discarded whole constituencies over marginal shortfalls; measured
   distributions replaced it with structural-loss rules, and the measured cases are pinned as
   tests.
3. One OCR'd serial (`3801114767`, from a "photo available" placeholder box) overflowed int32 and
   discarded 179k rows. Bounds and cache-scrubbing added.
4. Vision's batch API reports per-image failure inside HTTP 200; treated as "blank image", one
   night of it parked 22 constituencies. Empty answers are now failed reads: retried, never
   cached.
5. A 67-megapixel composite — accepted by the API — deterministically exceeded the OCR backend's
   deadline. The pixel budget now targets what the backend can process, not what the API accepts.
6. The Barak-valley Bengali rolls title their supplements in the genitive (সংযোজনের তালিকা),
   which the whole-word section matcher rejected; three constituencies filed every supplement
   elector into the main roll before the inflected forms became markers. All three republished
   exact.

## Operations

The fleet is **shut down**: all four VMs `TERMINATED` (kept, not deleted, by decision — their
disks cost ~$32/month and hold nothing unique; every input and output lives in the bucket). A
worker is recreated by starting an instance with `scripts/gcp_worker.sh` as its startup metadata;
it claims work from `gs://sawasdee-assam-rolls/source/`, resumes from `stage1/`, and publishes to
`shards/`. Stage-1 OCR words are cached beside each part, so a parser improvement re-reaches all
26M rows for CPU cost only.
