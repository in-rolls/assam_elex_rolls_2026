# Transliteration

`dataset/transliteration.csv.gz` maps every native-script value in the five administrative
fields to a romanization. It is a **lookup table, not a model**: 40 KB, plain CSV, editable
in a spreadsheet, joined onto the dataset in one merge.

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
| `indicxlit` | model output, unreviewed | 1,787 | 43.0% |
| `lexicon+indicxlit` | some tokens hand-checked, rest from the model | 677 | 14.5% |
| `lexicon` | every token hand-checked | 272 | 21.8% |
| `manual` | written by hand | 41 | 20.0% |
| `already-latin` | source already Latin, passed through | 40 | 0.8% |

**42.5% of row-weight is fully hand-checked or already Latin.** The rest is IndicXlit
output at the quality above — usable as a starting point, not as an authority.

Review works at the **token** level, which is why it goes this fast: the 2,817 values
decompose into 2,036 tokens, and they repeat heavily — `অংশ` appears in 362 values, `খণ্ড` in
165. Correcting `ৰাজহ` once fixes every revenue circle across all four fields.

## Known limits

- Transliteration inherits OCR error. Only hand-checked entries repair it, and only where
  someone looked.
- Conventional spellings are not stable: Karimganj is now officially Sribhumi, and both
  appear here. This column is a convenience key, never an identifier.
- `main_town_village` (12,459 distinct) and `ps_name` (31,368) are not covered. Same
  machinery, more volume.
- The twelve-district comparison above is judged against general knowledge, not a sourced
  gazetteer. A published list would settle it.
