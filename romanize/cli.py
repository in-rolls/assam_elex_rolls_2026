"""Command line for the transliteration stage.

python -m romanize vocab                 what needs transliterating, and how much
python -m romanize fill --backend ...    fill the lookup table
python -m romanize audit                 side-by-side comparison -> docs/ROMANIZATION.md
python -m romanize anchor                official gazetteer overrides guessed spellings
python -m romanize check                 transliteration-not-translation guard
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import guards, lookup, tokens, vocabulary


def cmd_vocab(args: argparse.Namespace) -> int:
    rows = vocabulary.load_rows(args.dataset)
    entries = vocabulary.build(rows, args.fields)
    summary = vocabulary.summarize(entries)
    total_rows = len(rows)

    print(f"{total_rows:,} parts\n")
    print(f"  {'field':20s} {'distinct':>9s} {'rows':>9s} {'coverage':>9s}")
    for field in args.fields:
        stat = summary.get(field)
        if not stat:
            continue
        print(
            f"  {field:20s} {stat['distinct']:>9,d} {stat['rows']:>9,d} "
            f"{stat['rows'] / total_rows:>9.1%}"
        )
    print(f"\n  {'TOTAL':20s} {len(entries):>9,d} distinct strings to transliterate")
    return 0


def _provenance(entry, backend_name: str, roman: str) -> str:
    """Which parts of this value a human has checked.

    Recorded per row rather than per run, because the table is a mixture: districts are
    written by hand, most values are the model's, and many are part one and part the other.
    A reader deciding whether to trust ``Guwahati Paur Nigam (Ansh-1)`` needs to know that
    every token in it was reviewed, while ``lokhyapur`` was not.
    """
    from .backends.indicxlit import DIGITS, SCRIPT_RUN

    runs = SCRIPT_RUN.findall(entry.native.translate(DIGITS))
    if not runs:
        return "already-latin"
    checked = sum(1 for run in runs if run in tokens.LEXICON)
    if checked == len(runs):
        return "lexicon"
    if checked:
        return f"lexicon+{backend_name}"
    return backend_name


def cmd_fill(args: argparse.Namespace) -> int:
    rows = vocabulary.load_rows(args.dataset)
    entries = vocabulary.build(rows, args.fields)
    existing = lookup.read(args.table)

    manual = sum(1 for r in existing.values() if r.is_manual)
    todo = [e for e in entries if not (existing.get(e.key) and existing[e.key].is_filled)]
    print(
        f"{len(entries):,} distinct strings | {manual:,} hand-reviewed (kept) | "
        f"{len(todo):,} to fill"
    )
    if not todo:
        print("nothing to do")
        return 0

    filled = {}
    if args.backend == "indicxlit":
        from .backends.indicxlit import IndicXlitBackend

        backend = IndicXlitBackend()
        print(f"running {backend.name} over {len(todo):,} strings...")
        words = backend.romanize_many(((e.native, e.lang) for e in todo), progress=print)
        # Hand-checked tokens outrank the model everywhere they appear. Reviewing ৰাজহ
        # once fixes every revenue circle; reviewing each value separately would not.
        for native, roman in tokens.LEXICON.items():
            words[native] = [roman]
        for entry in todo:
            roman = backend.join(entry.native, words)
            if not roman:
                continue
            # An unchanged result is only a failure when there was native script to
            # convert. The English constituency's values -- "HAFLONG", "HARANGAJAO ITDP
            # BLOCK" -- are already Latin, so unchanged is the correct answer, and
            # treating it as a miss left 40 of them permanently empty.
            if roman == entry.native and guards.NATIVE_SCRIPT.search(entry.native):
                continue
            filled[entry.key] = (roman, _provenance(entry, backend.name, roman))
    else:
        from .backends.aksharamukha import AksharamukhaBackend

        backend = AksharamukhaBackend(args.scheme)
        print(f"running {backend.name} over {len(todo):,} strings...")
        for entry in todo:
            roman = backend.romanize(entry.native, entry.lang)
            if roman:
                filled[entry.key] = (roman, backend.name)

    merged = lookup.merge(existing, entries, filled)
    problems = guards.check((r.field, r.native, r.roman) for r in merged)
    if problems:
        print(f"\nREFUSING TO WRITE: {len(problems)} translated entries", file=sys.stderr)
        for problem in problems[:10]:
            print(f"  {problem}", file=sys.stderr)
        return 1

    written = lookup.write(merged, args.table)
    still = len(lookup.pending(merged))
    print(
        f"wrote {written:,} rows to {args.table} | {len(filled):,} filled now | "
        f"{still:,} still empty"
    )
    return 0


def _hand_checked(row) -> bool:
    """Whether this row's romanization owes anything to a human.

    Decides if a displaced spelling is worth keeping as a ``variant``. A value whose every
    token came from the model is a guess, and a guess an authority overruled is not an
    alternative spelling -- it is just wrong. One where a reviewer chose even part of the
    wording is a real competing form and worth recording.
    """
    from .backends.indicxlit import DIGITS, SCRIPT_RUN

    if row.is_manual:
        return True
    runs = SCRIPT_RUN.findall(row.native.translate(DIGITS))
    return any(run in tokens.LEXICON for run in runs)


def _scopes(rows, gaz):
    """The candidate list each value may be matched against, and why it is small.

    Every scope comes from a key the corpus already carries. Nothing is ever compared
    against the whole gazetteer, because at that size a best match is meaningless.
    """
    from collections import defaultdict

    pins = defaultdict(set)  # post office value -> the pincodes it is printed with
    districts = defaultdict(set)  # any value    -> the official districts it occurs in
    for row in rows:
        pin = str(row.get("pin_code") or "").strip()
        official = {o.district for o in gaz.offices(pin) if o.district}
        for field in ("post_office", "district", "block", "main_town_village"):
            value = row.get(field)
            if not value:
                continue
            districts[(field, value)] |= official
            if pin:
                pins[value].add(pin)

    def candidates(field: str, native: str):
        if field == "post_office":
            return sorted({o.name for pin in pins.get(native, ()) for o in gaz.offices(pin)})
        if field == "district":
            return gaz.districts
        if field == "block":
            seen = districts.get((field, native), set())
            return sorted({b for d in seen for b in gaz.blocks_by_district.get(d, ())})
        if field == "main_town_village":
            # A village and the post office serving it usually share a name, so the offices
            # under the village's own pincodes are a fair candidate pool. Weaker evidence
            # than a post-office match, which is why the acceptance bar does the work.
            return sorted({o.name for pin in pins.get(native, ()) for o in gaz.offices(pin)})
        return []

    return candidates


def cmd_anchor(args: argparse.Namespace) -> int:
    """Replace guessed spellings with official ones, wherever an official one can be found."""
    import csv as _csv
    import gzip as _gzip
    import json as _json

    from . import anchor, gazetteer, normalize
    from .backends.indicxlit import IndicXlitBackend

    table = lookup.read(args.table)
    if not table:
        print(f"no table at {args.table}", file=sys.stderr)
        return 1
    # Repair model artifacts *first*. Anchoring stores whatever it displaces, and doing this
    # afterwards left 113 variants reading ``xingori`` and ``aamtola`` -- spellings this
    # pipeline declares wrong, published as if they were alternatives.
    table = {
        key: (row if row.is_manual else lookup.replace(row, roman=normalize.repair(row.roman)))
        for key, row in table.items()
    }

    with _gzip.open(args.dataset, "rt", encoding="utf-8") as handle:
        rows = [_json.loads(line) for line in handle if line.strip()]
    pincodes = {str(r.get("pin_code") or "").strip() for r in rows}

    print(f"loading gazetteer for {len(pincodes - {''}):,} pincodes...")
    gaz = gazetteer.load(pincodes, progress=print)
    print(f"  {len(gaz):,} official post offices, {len(gaz.districts)} districts")

    words = IndicXlitBackend().romanize_many([])  # cache only; never starts the model
    candidates_for = _scopes(rows, gaz)

    # Calibrate before applying: on values already hand-checked, the right answer is known.
    known = [
        (r.native, r.roman, candidates_for(r.field, r.native))
        for r in table.values()
        if (r.is_manual or r.source == "lexicon") and candidates_for(r.field, r.native)
    ]
    report = anchor.calibrate(known, words)
    print(
        f"\ncalibration on {len(known):,} already-reviewed values: "
        f"{report['accepted']:,} matched at >={anchor.ACCEPT}, "
        f"agreeing with hand review {report['precision']:.1%} of the time"
    )
    for native, hand, official, score in list(report["differ"])[:12]:
        print(f"    {native[:20]:<22} hand {hand[:20]:<22} official {official} ({score:.2f})")

    applied = confirmed = reviewed = 0
    out = []
    review_rows = []
    official_rows = []
    for row in table.values():
        cands = candidates_for(row.field, row.native)
        found = anchor.match(row.native, cands, words, scope=row.field) if cands else None
        if found and found.accepted:
            official = anchor.apply(row.native, found.official, words)
            if official and official != row.roman:
                applied += 1
                new = lookup.adopt(row, official, args.authority, keep_variant=_hand_checked(row))
            else:
                confirmed += 1
                new = lookup.confirm(row, args.authority)
            out.append(new)
            official_rows.append((new, found.score))
            continue
        if found and found.needs_review:
            reviewed += 1
            review_rows.append(
                (row.field, row.native, row.roman, found.official, f"{found.score:.2f}")
            )
        # Nothing official to say: keep what we had, but repair model artifacts.
        if row.source == "indicxlit" or row.source == "lexicon+indicxlit":
            repaired = normalize.repair(row.roman)
            out.append(row if repaired == row.roman else lookup.replace(row, roman=repaired))
        else:
            out.append(row)

    # An authority settles the *name*, not the column it was found in. Only some fields have
    # a scope to match within -- a post office has its pincode, a police station has nothing
    # -- so without this ডুমডুমা came out "Doom Dooma" as a post office and "Doomdooma" as a
    # police station, breaking the one property this table has always had: the same native
    # string romanizes the same way everywhere.
    #
    # Official rows are included rather than skipped. Skipping them meant two fields that
    # anchored the same name differently could never be reconciled: the consistency check
    # below would fire and the run would exit having written nothing, with no way out. The
    # winner is chosen by scope strength, then by match score -- never by iteration order.
    best_official: dict = {}
    for row, score in official_rows:
        rank = (anchor.scope_rank(row.field), -score, row.roman)
        if row.native not in best_official or rank < best_official[row.native][0]:
            best_official[row.native] = (rank, row.roman, row.authority)
    propagated = 0
    for index, row in enumerate(out):
        found = best_official.get(row.native)
        if not found or row.roman == found[1]:
            continue
        out[index] = lookup.adopt(row, found[1], found[2], keep_variant=_hand_checked(row))
        propagated += 1

    problems = guards.check((r.field, r.native, r.roman) for r in out)
    if problems:
        print(f"\nREFUSING TO WRITE: {len(problems)} problems", file=sys.stderr)
        for problem in problems[:10]:
            print(f"  {problem}", file=sys.stderr)
        return 1

    lookup.write(out, args.table)
    args.review.parent.mkdir(parents=True, exist_ok=True)
    with args.review.open("w", encoding="utf-8", newline="") as handle:
        writer = _csv.writer(handle)
        writer.writerow(("field", "native", "current", "official_candidate", "score"))
        writer.writerows(sorted(review_rows))
    print(
        f"\napplied {applied:,} official spellings | {confirmed:,} confirmed unchanged | "
        f"{reviewed:,} in the review band -> {args.review}"
    )
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    rows = lookup.read(args.table)
    if not rows:
        print(f"no table at {args.table}", file=sys.stderr)
        return 1
    problems = guards.check((r.field, r.native, r.roman) for r in rows.values())
    filled = sum(1 for r in rows.values() if r.is_filled)
    print(f"{len(rows):,} entries | {filled:,} filled | {len(rows) - filled:,} empty")
    if problems:
        print(f"\n{len(problems)} TRANSLATED (should be transliterated):")
        for problem in problems[:20]:
            print(f"  {problem}")
        return 1
    print("no translated entries: every value is a transliteration")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    from . import audit

    rows = vocabulary.load_rows(args.dataset)
    entries = vocabulary.build(rows, args.fields)
    text = audit.render(entries, len(rows), table=lookup.read(args.table))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="romanize", description=__doc__)
    parser.add_argument("--dataset", type=Path, default=vocabulary.DATASET)
    parser.add_argument("--table", type=Path, default=lookup.TABLE)
    parser.add_argument(
        "--fields",
        nargs="*",
        default=list(vocabulary.ALL_FIELDS),
        help="fields to transliterate (default: all seven)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("vocab", help="what needs transliterating").set_defaults(func=cmd_vocab)

    p_fill = sub.add_parser("fill", help="fill the lookup table")
    p_fill.add_argument("--backend", choices=["indicxlit", "aksharamukha"], default="indicxlit")
    p_fill.add_argument("--scheme", default="RomanColloquial")
    p_fill.set_defaults(func=cmd_fill)

    p_anchor = sub.add_parser("anchor", help="replace guessed spellings with official ones")
    p_anchor.add_argument("--authority", default="indiapost", choices=sorted(lookup.AUTHORITIES))
    p_anchor.add_argument("--review", type=Path, default=Path("out/anchor_review.csv"))
    p_anchor.set_defaults(func=cmd_anchor)

    sub.add_parser("check", help="transliteration-not-translation guard").set_defaults(
        func=cmd_check
    )

    p_audit = sub.add_parser("audit", help="side-by-side comparison")
    p_audit.add_argument("--out", type=Path, default=Path("docs/ROMANIZATION.md"))
    p_audit.set_defaults(func=cmd_audit)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
