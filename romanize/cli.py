"""Command line for the transliteration stage.

python -m romanize vocab                 what needs transliterating, and how much
python -m romanize fill --backend ...    fill the lookup table
python -m romanize audit                 side-by-side comparison -> docs/ROMANIZATION.md
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
