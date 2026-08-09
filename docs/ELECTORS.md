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

## Running one, and what it costs

```
python -m electors parse data/ac_rolls/AC1_ASM.zip --workers 5 --capture
```

Progress is logged with timestamps to the console and to a dated file under `out/logs/`, one
line per part as it **finishes** rather than in submission order — a pool that yields in order
lets one slow part at the front hold back everything behind it, and a run that looks silent for
twenty minutes cannot be told from a wedged one.

Each part is timed inside its own worker, because the wall-clock gap between two results is
whatever the slowest earlier part was still doing. The run's wall clock, mean and median per
part, and worker count are written into the manifest beside the row counts, so what a
constituency cost is recorded rather than remembered.

Two things the accounting deliberately refuses to do. Cached parts are kept out of the per-part
mean, since mixing them in makes a resumed run look faster than the pipeline is. And no
estimate is offered until each worker has actually read a part — a resumed run serves cached
parts in about a second each, and counting those as throughput put the first estimate for a
two-hour job at 57 seconds.

### Measured cost, and three optimisations that did not survive measurement

On a 10-core machine shared with other work (load ~25), 5 workers: **a part takes about 47
minutes** (822 electors in 46m58s; 873 in 47m34s) and a constituency projects to **16-17
hours**. One page of thirty electors costs about 53 seconds, and it divides:

| | share of a page |
|---|--:|
| text bands, 123 crops at scale 2 | 56% |
| name band, 30 crops at scale 3 | 17% |
| serial strips | 11% |
| rasterising the page | 11% |
| EPIC strips | 2% |

Four attempts to cut it, three of which failed and are recorded because a failed
optimisation tried twice is worse than one written down:

- **`OMP_THREAD_LIMIT=1`.** Tesseract multithreads, and five workers spawn far more threads
  than there are cores. Interleaved A/B: **0.97x, faster in 5 rounds of 8** — noise. An earlier
  version of the same test said 15%, but it ran the default arm first every round while the
  machine was quietening, so "second in the round" was worth the entire effect.
- **Whole text column at `psm 6` instead of four band crops at `psm 7`.** Five times faster,
  4.1s against 20.0s — and it recovered 34 of 120 fields against 104. Rejected.
- **Rasterising in grey rather than colour.** Every consumer converts to grey anyway, so the
  colour channels looked like pure waste, and a first A/B said 13% faster with byte-identical
  text on 123 of 123 crops. Isolating the stages that actually change killed it: the PNG is the
  **same size either way** (2.88 MB), because the source PDFs are already grey. Render 0.99x,
  load 0.97x, 0.04s saved per page. The 13% was the machine.
- **The serial zone.** This one worked: see above.

#### Establish the resolution before believing any of it

Three of the four failed and all three looked like wins first, which should have prompted the
obvious question sooner: **what does a null result look like here?** Running the same
configuration in both arms answers it.

    arm A median 23.3s   arm B median 21.0s   (identical configuration)
    apparent effect 0.90x, arm B "faster" in 5 of 8 rounds
    single measurements span 1.87x with nothing changed
    median-of-8 resolves to about 10%

An A/A pair produced a 10% "improvement" and won five rounds of eight -- the same score
`OMP_THREAD_LIMIT=1` got. That result was not evidence of anything, and neither was the
grayscale 13%.

The rule is not that large effects are unreachable; the serial zone's 2.1x sat far outside this
band and was readable directly. It is that **the resolution has to be measured rather than
assumed**, because it tracks whatever else the machine is doing -- load swung between 6 and 62
in one evening here. Below the resolution, more rounds help slowly and isolating the stage that
differs helps immediately: grayscale was settled in one step by noticing the output file was
the same size either way.

*Applied honestly to the change that shipped:* the serial zone's timing gain (0.78x on the
second part) is near this noise floor and should not be leaned on. Its justification is the
read rate -- 1.15-1.34x more serials recovered on both parts -- which is a count and not
subject to timing noise at all.

**Cost is dominated by OCR and by whatever else the machine is doing.** A part is roughly 30
pages, and each page costs about four tesseract invocations, so a constituency is a multi-hour
job. Any per-constituency figure quoted from a loaded machine describes the load. The manifest
records the worker count alongside the timing for that reason.

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
the value is very likely right; where they differ by more than scanner noise the row is
flagged. The finer scale is not assumed better, so the primary reading is kept and the
disagreement is recorded rather than resolved silently.

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

Three details that took a mistake each to get right:

- **Guarded on soundness, not fill.** A fill rate *falls* when a provably wrong value is
  correctly cleared, so guarding it rejects the one move that unambiguously improves the data.
  `*_sound` counts a field only when present and not provably wrong.
- **A metric absent from either side is a failure, not a skip.** Otherwise the two checks with
  real ground truth pass by not being measured.
- **Targets have a direction.** An error rate improves by falling. Without saying so, the only
  way to gate a fix aimed at reducing wrong values is to gate it on some other metric that
  happens to rise — judging a change on something it never set out to do.

### What the loop produced

The band-assignment work is the first change taken through all four gates end to end, scored by
replaying identical cached OCR text through the old code and the new. 5,664 rows, eight parts,
splits disjoint.

| | in-sample (3,567) | out-of-sample (2,097) |
|---|--:|--:|
| name present and not provably wrong | 72.0% → **86.9%** | 73.4% → **89.2%** |
| relation present and not provably wrong | 77.5% → **93.2%** | 76.1% → **94.3%** |
| provably wrong | 19.2% → **6.3%** | 19.2% → **4.0%** |
| name identical to relation | 486 → 2 | 325 → 3 |

Out-of-sample matched and slightly exceeded in-sample, which is what says the diagnosis set was
not fitted. Nothing degraded: age, EPIC and sex were flat, completeness stayed at every
measured part exact, and the sex ratio did not move. Parsing cost rose 1.05× — under 0.1% of
pipeline time, which is OCR.

### The second pass, and what it revealed about the first

Tesseract cannot read the house number on a third of boxes or the age on a fifth, and the line
cache showed the crop yields no digits at all -- so no parser change recovers them. savitr's
distilled Surya reads the same crops. Over a whole part (636 electors, 334 boxes re-read):

| | before | after |
|---|--:|--:|
| house no | 59.6% | **82.1%** |
| age | 80.3% | **92.3%** |
| sex | 95.8% | **98.6%** |

**And it exposed an error the floor could not see.** The engines both produced an age on 20
boxes of one page and disagreed on 7. The crops were read by eye: the second pass was right on
every one of the six checked -- 24 against 25, 59 against 92, 52 against 92, 25 against 85, 54
against 25, 45 against 55.

That means roughly **a third of the ages the first pass reports are wrong**, against an error
floor of 0.5% which never saw them, because an age of 92 is perfectly plausible on its own. It
is also the mechanism behind an anomaly detected independently weeks of measurement earlier:
the excess of nonagenarians in the age distribution. Age is now taken from the second pass
where the two disagree, and the first reading is kept beside it.

**The cost is the constraint, and batching does not relieve it.** One call per box is not an
oversight -- stacking crops makes the model ignore all but one box, and layout-preserving crops
(a grid column, a page) run away until the token cap stops them. At 2-6 seconds a box a full
second pass is tens of hours per constituency, so which rows to send is the only lever.

### `escalate` — know which rows are wrong rather than perfecting the pass

A cheap pass will always leave errors; what makes that acceptable is knowing which rows they
are, so an expensive engine runs on those and only those. Four families feed the router:
provably wrong, two-scale disagreement, a relation whose label was never recognised, and two or
more missing core fields. It flags **28.3%** of rows.

The disagreement threshold is measured rather than chosen. Over 1,733 boxes where both scales
produced a name they differ on 53%, but 83% of those differences are a character or two — a
stray matra, a speck of punctuation. Flagging all of them took the router to 56%, which is a
re-run rather than a triage. Below 0.90 similarity the readings differ by more than noise.

Its precision is deliberately not reported. The floor detectors are half the router, so scoring
it against them is a check that cannot fail. Volume and per-family contribution are reported
instead, and precision becomes measurable once a second engine has read the flagged rows.

**Cost is counted in bands, not rows.** A re-read settles one field, not a whole box, so
flagging 39.8% of rows costs 2,718 band re-reads against 22,656 for a full second pass — 12%
of one. Counting rows alone made a precise detector look like it had pushed the router past
being a triage.

**A detector can be falsified even where precision cannot be measured.** If the rows it picks
are no likelier to be wrong than the rows it passes over, it is flagging at random however
sensible its reason sounds. `separation` scores that against a *distributional* property, which
is the only handle on a value that is individually plausible but collectively impossible.

The age detector is the case in point. `age_ambiguous` fires where the age zone holds more
digits than an age can account for — `বয়স ' 9526`. Flagged rows put 15.2% of ages in the
nineties; unflagged rows put 2.2% there, and fall away from 28.6% in the twenties exactly as a
roll should. That is a **6.8× concentration** of demographically impossible ages, established
without a single labelled row.

Which end of a long run is the age was left undecided on purpose. Read from the left it puts
15.3% of those rows in the nineties, from the right 13.6%, against the 2.3% the unambiguous
two-digit runs show — so neither end is the age, and picking the marginally better rule would
be tuning on a signal that does not support it. An age of 95 that is really 26 sits inside
every range check, so no floor detector sees it and the soundness metric counts it as good.
The honest response to a value the parser cannot recover is to route it, not to invent a rule.

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
