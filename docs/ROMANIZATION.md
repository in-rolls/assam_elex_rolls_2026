# Transliteration

`dataset/transliteration.csv.gz` maps every native-script value in **all seven** name
fields to a romanization. It is a **lookup table, not a model**: under a megabyte, plain
CSV, editable in a spreadsheet, joined onto the dataset in one merge.

| tier | fields | distinct values |
|---|---|--:|
| 1 | `district`, `revenue_circle`, `police_station`, `block`, `post_office` | 2,817 |
| 2 | `main_town_village`, `ps_name` | 43,827 |

**Transliteration, never translation.** `গোসাইগাও ৰাজহ চক্ৰ` is *Gossaigaon Rajah Chakra*,
not *Gossaigaon Revenue Circle*. A check over the whole table enforces this.

```python
import csv, gzip, json
lut = {(r["field"], r["native"]): r["roman"]
       for r in csv.DictReader(gzip.open("dataset/transliteration.csv.gz", "rt", encoding="utf-8"))}
lut[("district", "কোকৰাব্মাৰ")]        # -> 'Kokrajhar'
```

## Why a table and not a model

No free offline tool produces conventional English spellings reliably. Measured on twelve
well-known Assam districts, top-1 exact:

| tool | exact |
|---|--:|
| **IndicXlit** (AI4Bharat, neural) | **5/12** |
| Aksharamukha `RomanColloquial` | 2/12 |
| Aksharamukha `RomanReadable` / IAST / ISO | 0–2/12 |

IndicXlit wins, but its misses are *systematic*: it renders Assamese **phonetics** —
`xunitpur` because শ is /x/, `zurhat` because যো is /z/, `dixpur` for Dispur — where
conventional spellings are older anglicisations. It optimises a different target, so its
output seeds a reviewable table rather than shipping directly.

`indicate` was tried and is **not broken** — it has no Assamese weights. Fed Bengali script
its Hindi model errors per character and returns `""`. On the script it *was* trained for it
gives `kokarajhar` and `jorhat`, better anglicisation than IndicXlit manages on Assamese, so
training `asm` weights on Aksharantar looks worth doing. `fetch_aksharantar.py` already maps
`asm`.

LLMs and hosted APIs (Bhashini/ULCA) were out of scope: free and offline only.

## What a table can do that no transliterator can

`district` holds 41 native spellings for 35 real districts, because `ঝ` is misread
systematically:

| native | rows | IndicXlit | table |
|---|--:|---|---|
| কোকৰাব্মাৰ | 862 | `kukorabmar` | **Kokrajhar** |
| কোকৰাব্াৰ | 33 | `kukorabrar` | **Kokrajhar** |
| কোকৰাব্বাৰ | 29 | `kukorabbar` | **Kokrajhar** |
| কোকৰাবাৰ | 20 | `kukorabar` | **Kokrajhar** |

**944 rows** now name their district correctly. No transliterator could do
this — the `ঝ` is gone from the source. Only a lookup can.

The verbatim column is **not** patched. Every row carries `pdf_sha256` so a reader can check
it against the source page; editing the text would make that provenance a lie. The table is
the same repair as a third column — derived, reversible, checkable.

## Review status

The table is complete. It is **not** fully reviewed, and the `source` column says so per row:

| provenance | meaning | entries | row-weight |
|---|---|--:|--:|
| `lexicon+indicxlit` | some tokens hand-checked, rest from the model | 33,630 | 22.0% |
| `indicxlit` | model output, unreviewed | 7,644 | 17.6% |
| `lexicon` | every token hand-checked | 4,805 | 45.4% |
| `already-latin` | source already Latin, passed through | 524 | 0.8% |
| `manual` | written by hand | 41 | 14.3% |

**60.4% of row-weight has every token hand-checked or was already Latin**, and
**77.2% of token-occurrences are hand-checked** — the second number is the higher one
because a value can be nine parts reviewed and one part not, and the first counts it as
unreviewed. Both are in the file: `source` per row, and the lexicon itself.

Capitalisation happens to make this legible. Reviewed tokens are title-cased, model output
is not, so `puthimari ombikagiri High School (Baon Ansh)` shows at a glance which half
someone looked at.

Review works at the **token** level, which is why it goes this fast. The 46,644 values
decompose into 20,600 tokens, and they repeat hard: `অংশ` appears in 362 values, `স্কুল` in
19,600 rows. Correcting `ৰাজহ` once fixes every revenue circle in the corpus.

The leverage is steep and measurable. Four review passes over
[`romanize/tokens.py`](../romanize/tokens.py) — 750 tokens in total — each took the
tokens ranked by how many rows they touch rather than by where they happened to appear:

| pass | tokens | tier 1 row-weight checked |
|---|--:|--:|
| 1 | ~120 | 42.5% |
| 2 | +232 | 71.8% |
| 3 | +101 | 78.0% |
| 4 | +286 | 78.5% (and tier 2 from nothing to 70.9% of tokens) |

Pass three bought 6.2 points for 101 tokens and pass four bought 0.5 on tier 1 — the tier-1
curve is flat and what remains there is a genuine long tail. Tier 2 is earlier on its own
curve.

## Tier 2 was cheaper than its size suggested

`ps_name` and `main_town_village` are 43,827 distinct values against tier 1's 2,817 — 15×
the volume. They cost roughly one more review pass, because a station name is a **place name
plus a school**, and the school half is a closed vocabulary:

| token | rows |
|---|--:|
| `স্কুল` (school) | 19,600 |
| `এল` / `পি` (the L and P of "L.P. School") | 11,928 / 790 |
| `নং` (no.) | 9,931 |
| `বিদ্যালয়` (vidyalaya) | 7,489 |
| `শাখা` (branch) | 3,201 |

200 tokens covered 47.8% of tier-2 token-occurrences before any of them were looked at. The
place-name half is the real long tail — 19,216 distinct tokens — and that is where the
unreviewed remainder sits.

Two distinctions the vocabulary forced:

**`বিদ্যালয়` is *Vidyalaya*, not "School".** The source uses both words, sometimes in the
same name, so collapsing them would destroy information the source took care to record.
Only words the source itself borrowed come back as English — `স্কুল`, `ৰুম`, `কলেজ`.

**`১০ম` is *10ma*, not "10th".** Bengali ordinal suffixes attach to Western numerals here.
IndicXlit reads them as syllables and returns `10maha`, `2yoy`, `4ortho`, `6stho`; four
lexicon entries fix every one.

## Known limits

- Transliteration inherits OCR error. Only hand-checked entries repair it, and only where
  someone looked.
- Conventional spellings are not stable: Karimganj is now officially Sribhumi, and both
  appear here. This column is a convenience key, never an identifier.
- The twelve-district comparison above is judged against general knowledge, not a sourced
  gazetteer. A published list would settle it.
