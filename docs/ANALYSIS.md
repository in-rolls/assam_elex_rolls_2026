# Analysis notes

Findings from building the extractor, with the measurements that justify each decision.
Recorded here because they are the reusable part of this work: most are properties of
Assamese OCR and of ECI's roll PDFs, not of this repository.

Sections 1-9 were measured on the 890 parts of AC1, AC10, AC12 and AC100, all Assamese.
Section 10 covers the full 126-constituency corpus, which turned out not to be
monolingual.

---

## 1. The page is a fixed template, so the grid can be found without OCR

Page 1 is a four-section form printed from the same generator for every part. Rendered
at native resolution the rules land on **identical pixel rows across all four ACs**:

```python
STABLE_H_RULES = (124, 157, 191, 225, 399, 433, 483, 816, 850, 896)
```

That makes layout a signal-processing problem rather than a recognition problem. Summing
dark pixels along each row and column gives ink profiles whose peaks *are* the rules; the
cells fall out of their intersections. Eighteen named cells are located this way.

Three consequences, all of which cut error and cost:

- **Labels never need OCR.** `জিলা`, `ডাকঘৰ` and the rest are at known positions. Only
  values are read. Anything printed rather than filled in is free and exact.
- **Layout failure is detectable, not silent.** `build_grid` raises `LayoutError` when
  the stable rules are absent, so an off-template page becomes a flagged row rather than
  a plausible-looking wrong one. Over 890 pages this never fired.
- **It is language-independent.** Ink profiles do not care that the script is Assamese,
  so the same detector would work on any state's roll from the same generator.

The detector is deliberately intolerant. An earlier version cropped with `inset=0` and
the page border registered as a text row, shifting every field down one. Cropping inside
the rule fixed it, and the test that documents this exists to stop it recurring.

---

## 2. The digit-script trap: Assamese OCR reads Western `8` as an Assamese `4`

This one silently corrupted data before it was caught, and is the single most important
finding here.

Tesseract's Assamese model (`-l asm`) transcribes the Western digit **`8`** as the
Assamese digit **`৪`** (U+09EA, which means *4*). That alone would be a visible error.
What makes it dangerous is the Python side:

```python
>>> re.search(r"\d+", "৪").group()   # \d matches Unicode decimal digits
'৪'
>>> int("৪")                          # int() accepts them too
4
```

So the misread converts **cleanly and silently** to the wrong number. There is no
exception, no empty string, no obvious garbage — just a `4` where an `8` belonged. It was
only visible at all because the filename independently says which part the page is.

It is not deterministic: on the current run, part 8 reads correctly in AC10 and AC100 and
fails in AC1 and AC12. That intermittency is what makes it dangerous — it would never
show up as an obviously broken field, only as a scattering of parts numbered 4 that
should be 8.

Two rules follow, and both are enforced in code:

**Digits are read by a separate call.** `read_digits` runs `-l eng` with a digit
whitelist and only ever on crops known to contain nothing but a number.

**Digits are parsed ASCII-only.** `ocr.int_or_none` searches `[0-9]+`, not `\d+`, so a
script confusion yields `None` (a flagged missing value) instead of a wrong integer.

The converse also holds and is equally load-bearing: running the digit whitelist over
Assamese text forces every glyph to a digit and returns pure noise — it read AC 1's
header as `41`. Neither call is safe on the other's input, which is why the API has two
methods rather than one with a flag.

> The `100%` on `ac_no` deserves an asterisk: none of these four ACs is numbered with an
> `8` in a position where the trap fires. It is a real result, partly lucky.

---

## 3. Upscaling is per-region, not per-engine

The obvious tuning knob is how much to upscale a crop before handing it to Tesseract.
There is no single best value — the optimum differs by *region shape*:

| region | scale 2 | scale 3 | scale 4 |
|---|---|---|---|
| digits (sum check) | **100.0%** | 99.0% | 86.9% |
| single-line text | correct | loses long values outright | — |
| multi-line text | **drops an entire line** | reads both lines | — |

Two failures worth stating precisely, because both are silent:

- At scale 3, `যোৰহাট ইঞ্জিনিয়াৰিং কলেজ` returned **`""`** on 6 pages. Not garbled —
  empty.
- At scale 2, a two-line address lost one line completely under **every** `--psm` value
  tried. The output was a well-formed, plausible, incomplete address.

A blanket 3× traded **8 recovered addresses for 6 lost post offices** — nearly a wash,
and a bad one, since the losses were in a field that had been perfect. So scale is set
per region: `DEFAULT_DIGIT_SCALE = 2`, `DEFAULT_TEXT_SCALE = 2`,
`MULTILINE_TEXT_SCALE = 3`.

`--psm 6` ("uniform block") beat 7, 8 and 13 on isolated numbers — notably it is the only
mode that reliably reads a lone `0`, which psm 7 returns as empty.

---

## 4. Reading values without reading labels

Values sit to the right of their labels, but *where* to the right varies. Two
approaches, and the right one differs by block:

**Locality block — fixed offset (`x = 320`).** A dynamic largest-gap detector was tried
and was worse: it lands *before* the colon and pulls `:` into the value.

**Revision block — dynamic offset.** A fixed offset clipped values outright:
`2026` → `26`, `বিশেষ` → `শেষ`. Here the largest-gap detector works because the labels
vary far more in width.

A tempting third approach was rejected: OCR the whole locality block in one call and map
lines to fields by position. It failed on rural parts, where `ward_no` is blank — 7 lines
came back for 8 fields and **every subsequent field shifted up by one**. Row-position
mapping is only safe when every row is guaranteed non-empty, which is exactly what this
form does not guarantee.

---

## 5. Null and blank are different, and the ink profile decides which

A rural part has no ward number. A page whose ward cell is smudged has one that could not
be read. Collapsing both to `""` throws away the distinction permanently, and it is the
distinction a user needs to know whether to trust the row.

The pipeline decides mechanically, from the same ink profile the grid detector already
computes:

| ink in the value region | text recognised | result |
|---|---|---|
| no | — | `""` — the form prints nothing there |
| yes | no | `null` — the pipeline failed to read it |
| yes | yes | the value |

In the shipped dataset: **729 parts have `ward_no == ""`** (rural, genuinely no ward) and
**0 have `ward_no == null`**. Four parts have `main_town_village == null` — ink present,
no text recovered. Those four are real extraction failures and are identifiable *as*
failures rather than hiding among 729 blanks.

Derived columns inherit the kind of emptiness rather than recomputing it: blank in, blank
out; unread in, unread out.

---

## 6. Dictionary canonicalisation: similarity alone is not enough

Every part of an AC shares a district, a revenue circle and a block. That redundancy
allows OCR noise to be cleaned by clustering variants within an AC and promoting the
dominant form.

The first threshold tried, 0.82, made things **worse** — it merged genuinely distinct
places:

| merged | times | reality |
|---|--:|---|
| Gossaigaon → Kachugaon | 22 | different blocks |
| Jorhat → Madhya Jorhat | 10 | different revenue circles |
| `(অংশ-২)` → `(অংশ-১)` | 7 | different parts of one place |

The distributions overlap and cannot be separated by similarity at any threshold:

- OCR variants of one name: **0.89 – 0.96**
- Genuinely different names: **0.85 – 0.96**

What separates them is not similarity but **digits**. `(অংশ-১)` and `(অংশ-২)` differ in
exactly one character and are 0.96 similar, but their digit content differs — and digits
in a place name are never OCR noise, because they come from the digit pass, which is
independently verified. So `mergeable(a, b)` requires similarity ≥ **0.89** *and* an
identical digit signature.

On the shipped run this makes **4 substitutions with 0 contested clusters** — deliberately
conservative.

**The honest limit:** clustering fixes *random* error, never *systematic* error. When
every page in AC1 misreads `ঝ` the same way, the wrong spelling is the mode and
canonicalisation promotes it. `*_canonical` therefore means "made internally consistent",
not "corrected", and the raw value is always kept beside it.

---

## 7. Engine bake-off: Tesseract vs Surya

Surya (a 650M VLM, run via `savitr`'s MLX runtime) was benchmarked against Tesseract on
the Assamese text cells over a 16-page sample.

**The scores measure agreement, not accuracy.** There is no human-labelled gold set, so
one engine was declared the reference and the other scored against it. Where both engines
share a failure they agree and are both wrong. This is stated wherever the numbers appear
because it is easy to misread as an accuracy table.

Overall agreement: **86.7%** across the text fields — unchanged after dictionary
canonicalisation, which is itself informative: if the remaining disagreements were random
noise, clustering would have reduced them. They are systematic.

**Tesseract wins**, on two grounds neither of which is raw agreement:

**Error shape.** Tesseract averages **2.3 instances per distinct error**; Surya **1.1**.
Tesseract fails the *same way* repeatedly, so its errors are enumerable, correctable in
bulk, and detectable by within-AC consistency. Surya's errors are one-off, so each must
be found and fixed individually. For a 28,000-page corpus that difference dominates.

**Surya is disqualified as a reference.** It emitted **Devanagari characters into three
Assamese fields** — a script error a native reader would never make, and one that would
poison any gold set built from it. It also renders a Latin pincode as `78336০`, mixing in
a Bengali zero, which is why digits fall back to Tesseract even when Surya reads text.

Speed is not close: **2.1 s/page vs 18.9 s/page** — a 9× difference that becomes days of
wall-clock statewide.

### A methodological note on scoring

The first "normalised" metric did not work. `গাঁও` and `গাওঁ` — the same word with the
candra-bindu on a different base — encode as `গ া ঁ ও` and `গ া ও ঁ`. Stripping
whitespace and punctuation leaves them different, so a mere diacritic placement counted
as a wrong word.

It was replaced with `skeleton`, which strips **combining marks entirely**. Comparing
`exact` against `skeleton` then separates two genuinely different error classes:
misplaced diacritics versus wrong letters. On this corpus they are equal (79.4% both),
which says the disagreements are wrong *letters*, not misplaced marks.

---

## 8. Why the Claude API path was measured and then abandoned

The original design used Claude with structured outputs. It was costed precisely before
being written off — image tokens are exactly `width × height / 750`, so the input side is
knowable in advance rather than estimated.

The result: **81% of the projected spend was output and thinking tokens**, not the images.
The page is dense — 60-odd fields including a variable-length section list and a verbatim
Assamese paragraph — so every page pays for a long structured response. Batch API's 50%
discount does not change the shape of that.

Against $0 and 0.37 s/page for a local pipeline that satisfies its checks on 99.9% of
pages, the API path did not justify itself. The code remains in `extract.py` and is not
wired into the default flow; nothing in the shipped pipeline requires a key.

The one thing genuinely lost is the `*_roman` transliterations, which OCR cannot produce.
Those columns are dropped from the output rather than shipped empty.

---

## 9. What is still not known

Stated plainly, because the fill rates in `README.md` invite the opposite conclusion:

- **Numeric accuracy is measured. Text accuracy is not.** Four independent checks verify
  numbers on every page. No equivalent exists for the Assamese text, and cross-engine
  agreement is not accuracy — both engines can share a failure, and on `ঝ` they may.
- **The `asm` model is frozen.** `asm.traineddata` is `4.00.00alpha:asm:synth20170629` —
  trained in 2017 on synthetic data and unchanged since. Its known failures (`ঝ`
  systematically misread, `ৰ`/`র` confused inconsistently) will not improve on their own.
- **Within-AC consistency is not correctness.** AC10, AC12 and AC100 are 100% internally
  consistent on district; AC1 is 92.2% across 4 variants — and AC1's *modal* spelling is
  itself wrong. A perfectly consistent AC can be consistently wrong.

Closing these requires a human-labelled gold set. Nothing in the pipeline substitutes for
one, and no number reported here should be read as if one existed.

---

## 10. The roll is printed in three languages, and the form is not the same in each

The full download settled a question the four-AC sample could not raise: **the Assam roll
is not monolingual.** The publisher's own filenames say so.

| language | ACs | parts | which |
|---|--:|--:|---|
| Assamese | 112 | 27,683 | 1–112 |
| Bengali | 13 | 3,542 | 114–126 (Barak Valley) |
| English | 1 | 261 | 113 |

That the language is *stated in the filename* is the load-bearing fact. It is known before
a page is opened, so it never has to be detected, and a wrong guess is impossible. The
pipeline refuses to read a page whose language has no profile rather than defaulting to
Assamese — defaulting is precisely how 3,803 parts would have been read with the wrong
model and nobody would have seen a single error message.

### What transfers between languages, and what does not

The initial evidence was encouraging and turned out to be shallow. All three editions are
595×842 pt, one 1187×1679 image per page, two pages, zero font objects — the same
generator. It was tempting to conclude the grid detector would transfer unchanged.

It did not. Measured against rendered pages:

| | Assamese | Bengali | English |
|---|---|---|---|
| upper rules | 124, 157, 191, 225, 399, 433, 483 | **identical** | 133, 185, 243, 295, 488, 540, 596 |
| lower rules | 816, 850, 896 | 840, 874, 920 (+24) | varies: 953/969/985 … |
| locality rows | 8 | **9** | 9 |
| vertical rules | — | within 3px | within 3px |

Three separate lessons:

**Only the upper rules are language-specific.** English rows are simply taller. Bengali's
lower block sits 24px below Assamese's. But the rules *below* section 2 move with page
content in every language — English has three variants 16px apart depending on how far the
address wraps — so hardcoding them was always wrong and merely happened to work on
Assamese. They are now found from the end of the rule list, which is one rule for all
three languages and for every variant.

**Vertical rules need no table at all.** Measured across all three, they agree to within
3px, and the column snapper already tolerates that.

**The field set differs.** This is the one that would have corrupted data.

### The extra rows

The Bengali form prints a **ninth** locality row, `গ্রাম পঞ্চায়েত` (Gram Panchayat), between
the police station and the block. The English form prints **Subdivision** after the revenue
circle — which it labels *Tehsil*, the same slot between Block and District, so it is the
same field under a different translation.

Mapping Assamese's eight fields onto Bengali's nine rows would have shifted **block,
revenue circle, district and pincode each down by one** on all 3,542 Bengali parts. Every
value would have been a real value, in the wrong column, on every page — no exception, no
empty field, nothing to notice. The row-count check is what caught it.

Both extras are now columns of their own, blank on the editions that do not print them.
The core eight are identical across all three languages, so the dataset is one schema:
every row has the same columns whatever language its constituency was printed in.

### Wrapping breaks row counting, and the fix is to look at the labels

English values wrap. `NEW SANGBAR DEVELOPMENT BLOCK` runs onto a second line, and an ink
profile taken across the whole cell counts that continuation as a row of its own — 9, 10 or
11 rows for the same 9 fields, which makes positional mapping impossible.

The label column never wraps. Counting rows in a left-hand strip that stops short of the
printed colon gives exactly 9 on every English page. A generic fix, and it removed the need
for any continuation-folding logic.

The same class of assumption broke the station name. The left-hand station cell had been
split at a horizontal rule that only divides the *right-hand* column; on Assamese pages
that rule happens to land in whitespace, on English it cut the station name in half. The
cell now spans the whole block and the label rows are found by reading them.

### Recovering the labels instead of typing them

The row-alignment fallback matches against the printed labels, so Bengali and English
labels had to be known exactly. Typing them from memory would have been guessing.

They are recovered from the corpus instead. A label is *printed*, not filled in, so it is
identical on every page of a language: OCR the label column across a sample and take the
**modal reading per row**. Random per-page errors are outvoted, and the agreement fraction
says how much to trust each result. Both languages came out at 98–100% with no weak rows.

Three things had to be got right for that to work, each found by looking at the output:

- **Crop to the gap, not to the label.** A crop only as wide as the text is too small a
  strip for Tesseract — Bengali `ব্লক` (~45px) read as `a`.
- **Stop short of the colon.** Including it adds a glyph transcribed inconsistently as
  `H`, `1`, `(` or `]`, which differs across pages and destroys the consensus outright.
- **Find the value by measurement, not by offset.** A fixed clearance past the colon is
  either too small (the colon lands in the value as a stray `'` or `>`, and a blank field
  reads as unread rather than empty) or too large (`HAFLONG` became `4~AFLONG`). Walking
  past the colon's ink to the next whitespace is exact.

The strongest evidence that this works: the **Bengali value offset was derived as 320** —
the same number that had been hand-tuned for Assamese months earlier, arrived at
independently from pixels.

What consensus cannot do is catch a *systematic* misreading: if the model mangles a glyph
the same way on every page, the mode is mangled too. That is why per-language accuracy is
reported separately — see below.

### Why accuracy is reported per language

The corpus is 88% Assamese. A total collapse confined to the 13 Bengali constituencies
would move the corpus-wide figure by about one percent, and one confined to the single
English constituency by less than one part in a hundred. Both would look like noise.

Each language is read with its own Tesseract model, its own label table and its own rule
anchors, so each can fail independently — and the four hard checks are language-independent,
so each language has the same free ground truth on every page. `report.json` therefore
carries the full breakdown by language, and that is the number to look at first.

### A correction worth recording

While planning this I claimed the label-alignment fallback fires on 82% of Assamese pages,
reasoning that a blank ward number drops a row. That was inference, not measurement, and it
was wrong: the label row is printed even when its value is empty, so Assamese is 8 rows on
every page sampled. The fallback is rare on Assamese — and, because of wrapping, essential
on English. The direction of the error was the opposite of what I assumed.
