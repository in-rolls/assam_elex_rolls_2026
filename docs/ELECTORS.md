# Extracting the electoral rolls

The `electors` stage turns each part PDF in `data/ac_rolls/` into one row per elector. It skips
the two info pages the `assam_rolls` stage already reads and starts at the roll proper.

```
python -m electors parse data/ac_rolls/AC1_ASM.zip      # extract one constituency
python -m electors report out/electors/AC001.parquet    # reconcile against the info pages
```

Output is one Parquet shard per constituency, zstd-compressed and gitignored; the committed
manifest carries each shard's row count and SHA-256. Rows join to the info-page tables on
`(ac_no, part_no)`, which is parsed from the source filename — the same key on both sides.

## What a page looks like

An elector page is a ruled grid, three columns by ten boxes. Each box holds six fields in two
places: a **header strip** across the full width with the serial number at the left and the
EPIC at the right, and a **text column** below it with four labelled lines.

```
নাম : খাদৰাম ৰাভা              name
পিতাৰ নাম : গংগাৰাম ৰাভা       relation, and its type from the label
ঘৰ নং : 21                     house number
বয়স : 46 লিঙ্গ : পুৰুষ          age and sex
```

Four measured facts shape how it is read.

**The EPIC needs the English model.** `asm` renders `HHK0001471` as `1414140001471` — it is
trained on a script with no Latin letters and maps them onto lookalike digits. Read with `eng`
the same crop comes back exactly right. The EPIC is the only globally unique field here, so
this one substitution is worth more than any other tuning.

**Scale, not page-segmentation mode, decides a marginal read.** Native resolution was tried:
counting how many crops still contained the *label* said scale 1 was as good as scale 2 for
half the cost. That measured the wrong thing. Extracting the *values* cost age 77% → 63% and
sex 83% → 73% — the printed label survives a coarse read, the digits beside it do not.

**Adjacent boxes share their borders.** Three columns produce seven rule clusters, not nine, so
column geometry is derived from cluster positions rather than assumed. Partial last pages draw
no photo dividers and produce four; both forms are handled, and the divider is derived from a
measured fraction of box width when it was never printed.

**Row height comes from page area explained, not from frequency.** Taking the modal gap between
horizontal rules returned six rows for a thirty-elector page, because internal rules outnumber
real ones.

## Ground truth, and what stands in for it

The roll prints its own totals on a closing page — `মূল তালিকা ... 453 420 873` — and they are
self-validating, since `male + female + third == total`. That page is the only real ground
truth in the source, and finding it mattered more than any single fix: the pipeline had been
reconciling against the info page's *net* figure instead, and residuals collapsed from ±27 per
part to ±1 the moment the right pair was compared.

Everything else is bracketed rather than known.

**Floor — provably wrong.** Values that cannot be right whatever the source says: Latin letters
or digits in an Assamese name, a name identical to the relation, a field's own label leaking
into its value, a malformed EPIC, an age outside 18–120, and a supposedly unique EPIC repeating.

**Ceiling — disagreement.** The name band is read at two scales and compared. Where they agree
the value is very likely right; where they differ the longer is kept and the row is flagged.

**A fill rate is never reported as accuracy.** `epic = 83%` means 83% non-empty, which is
equally consistent with 83% correct and 40% correct.

## The improvement loop

Fixes here were once justified by the page that revealed them, which is how the sex bias was
declared fixed twice before its real cause was found. Four modules now carry the loop.

### `capture` / `replay` — separate the expensive half from the cheap half

Most fixes change how a line is *interpreted*, not how it is read. Scoring one used to mean
re-rasterising the PDF and re-running tesseract over every crop — half an hour per variant, for
a change that cannot alter a single character tesseract returns.

```
python -m electors capture data/ac_rolls/AC1_ASM.zip --parts 12 13 14
python -m electors replay --diagnose
```

`capture` writes down every line of text with the geometry that produced it. `replay` re-parses
it with whatever the code says today. Two variants then score against **identical** input,
which also removes a confound: where OCR is re-run per variant, some of the difference is just
tesseract. Replay calls the pipeline's own `fields.assemble`, so it cannot drift from what
production does, and a test asserts the two agree.

It is exact for anything downstream of the text and **blind to crop geometry, scale and
engine**, which change the text itself. Captures are stamped, and a stale one is refused rather
than replayed — replaying text the pipeline would not produce answers the wrong question.

### `diagnose` — compute the co-occurrence, do not eyeball it

Every root cause found here started as a co-occurrence noticed by eye. Noticing by eye does not
scale and does not repeat, so this computes the failure rate of each class within each value of
each feature and reports the slices well above base rate.

Both missing *and* wrong values are failure classes. For a while only the first kind was, and
the largest error class in the corpus was invisible to the scan while the detectors that find
it sat in another module.

The feature set includes derived positional features, because **"diffuse" is a claim about the
feature set, not about the data** — and it is the claim that routes a class to the expensive
second pass. Adding page position moved `no_house` (3,550 rows) from diffuse to concentrated
and surfaced that names fail at 24.0% on the first elector page of a part against an 11.7%
base. `no_age` stayed diffuse under the richer features, which makes escalating it a measured
verdict rather than a gap in what was measured.

### `bench` — four gates, all required

| gate | means |
|---|---|
| in-sample | the target metric improves on the parts it was diagnosed against |
| out-of-sample | it improves by a comparable margin on parts never inspected |
| no degradation | every other field, and both ground-truth measures, stay within tolerance |
| cost | seconds per part does not rise materially |

Splits are written down rather than drawn fresh, because an out-of-sample set that gets looked
at stops being one. DIAGNOSE (parts 12–16) may be opened and stared at; VALIDATE is seeded and
used only to score; REGRESSION (parts 1–11) has measured roll totals.

Two details that took a mistake each to get right:

- **Guarded on soundness, not fill.** A fill rate *falls* when a provably wrong value is
  correctly cleared, so guarding it rejects the one move that unambiguously improves the data.
  `*_sound` counts a field only when present and not provably wrong.
- **A metric absent from either side is a failure, not a skip.** Otherwise the two checks with
  real ground truth pass by not being measured.

### `escalate` — know which rows are wrong rather than perfecting the pass

A cheap pass will always leave errors; what makes that acceptable is knowing which rows they
are, so an expensive engine runs on those and only those. Four families feed the router:
provably wrong, two-scale disagreement, a relation whose label was never recognised, and two or
more missing core fields. On 10,245 rows it flags **21.6%**.

Its precision is deliberately not reported. The floor detectors are half the router, so scoring
it against them is a check that cannot fail. Volume and per-family contribution are reported
instead, and precision becomes measurable once a second engine has read the flagged rows.

Measuring that composition immediately found a dead signal: `name_disagreement` had been raised
**zero times in 10,245 rows**, because the one-line second-scale read was passed through the
band assigner, where a single line lands in `house`. A scale-3 pass over every name crop was
being computed and discarded.

savitr's distilled model was checked as the expensive tier and rejected on evidence: it emits
this schema at 17.5 s/page with 96–98% reported fidelity, but 400 of 400 sampled training
records are Latin-script English rolls. It says nothing about Bengali-Assamese. General Surya
remains the candidate.

## What is not measured

None of this measures name accuracy against truth. The bracket — provably wrong at the floor,
two-scale disagreement at the ceiling — is what exists without labelled data, and cross-engine
agreement will narrow it without closing it. Agreement is reported as agreement, never as
accuracy: two engines can be wrong together.

The full 154-part run for AC1 has not completed, so no constituency-wide figure is published
here. The numbers above are from sampled parts and say so.
