"""Canonicalise repeated values by clustering them within a constituency.

Most locality fields repeat heavily: across the 890 parts on disk there are only 7
distinct districts, 9 revenue circles, 13 police stations, 20 blocks. Every part of an
assembly constituency shares one district, so the several spellings OCR produces for it
are noise around a single truth, and the majority reading can stand in for the rest.

**What this can and cannot fix.** Clustering removes *random* per-page error -- a
variant that appears 8 times in 154 pages loses to the one that appears 142 times. It
cannot touch a *systematic* error, because that misreads every page identically and so
wins its own vote: Tesseract renders কোকৰাঝাৰ as কোকৰাব্মাৰ on all 154 AC1 pages, and no
amount of voting recovers the ঝ. Systematic error is what cross-engine comparison is
for; the two techniques are complementary and neither substitutes for the other.

Canonicalisation is therefore applied to a **copy** of each value, with the raw reading
preserved, so the dictionary can never silently destroy evidence.
"""

from __future__ import annotations

import difflib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .bench import nfc
from .schema import normalize_digits

#: Fields that should hold one value for an entire constituency.
CONSTANT_PER_AC = ("district", "ac_name", "pc_name", "ac_reservation", "pc_reservation")

#: Fields that vary within a constituency but repeat enough to cluster.
REPEATED_PER_AC = ("revenue_circle", "block", "police_station", "post_office")

#: Fields that are essentially unique per part; clustering them would merge real
#: distinctions, so they are deliberately left alone.
UNIQUE_FIELDS = ("ps_name", "ps_address", "main_town_village")

#: Similarity above which two readings *may* be treated as the same underlying value.
#:
#: Similarity alone cannot decide this. Measured on real pairs from this corpus, genuine
#: OCR variants score 0.89-0.96 and genuinely different places score 0.85-0.96 -- the
#: ranges overlap, because these names share long suffixes ("উন্নয়ন খণ্ড", "যোৰহাট") and
#: differ in a short prefix. At 0.82 the dictionary merged three distinct blocks:
#: Gossaigaon into Kachugaon, Jorhat into Madhya Jorhat, and part 1 into part 2.
#:
#: 0.89 excludes the two low-scoring bad merges; the digit guard below catches the third,
#: which scored 0.96.
SIMILARITY_THRESHOLD = 0.89

#: A cluster must beat its rivals by this margin to be treated as settled. Below it the
#: field is reported as contested rather than silently resolved.
DOMINANCE_MARGIN = 2.0


@dataclass
class Cluster:
    """A group of readings taken to be the same underlying value."""

    canonical: str
    variants: Counter = field(default_factory=Counter)

    @property
    def total(self) -> int:
        return sum(self.variants.values())

    @property
    def support(self) -> int:
        return self.variants[self.canonical]


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, nfc(a), nfc(b)).ratio()


def digit_signature(text: str) -> str:
    """The digits in ``text``, script-normalised, in order.

    Assamese and Latin numerals are folded together first, so ``(১-1)`` and ``(2-1)``
    are compared as ``11`` and ``21`` rather than as different scripts.
    """
    return "".join(ch for ch in normalize_digits(nfc(text)) if ch.isdigit())


def mergeable(a: str, b: str, threshold: float = SIMILARITY_THRESHOLD) -> bool:
    """Whether two readings should be treated as the same underlying value.

    Requires both high similarity **and** identical digit sequences. The digit guard is
    what stops ``উত্তৰ পশ্চিম যোৰহাট (অংশ-১)`` merging into ``(অংশ-২)`` -- two real,
    different blocks that score 0.96 on similarity alone. It also refuses some genuine
    merges (a letter misread *as* a digit, such as ``(P-1)`` read as ``(২-1)`` and
    ``(2-1)``), which is the safe direction to err: those stay as separate variants of a
    value that is wrong either way, rather than being silently fused.
    """
    return similarity(a, b) >= threshold and digit_signature(a) == digit_signature(b)


def cluster_values(values: Iterable[str], threshold: float = SIMILARITY_THRESHOLD) -> List[Cluster]:
    """Group readings into clusters, each canonicalised to its most frequent member.

    Values are considered most-frequent-first so a cluster forms around the dominant
    reading rather than around whichever variant happened to be seen first.
    """
    counts = Counter(nfc(v) for v in values if nfc(v))
    clusters: List[Cluster] = []
    for value, count in counts.most_common():
        for cluster in clusters:
            if mergeable(value, cluster.canonical, threshold):
                cluster.variants[value] += count
                break
        else:
            clusters.append(Cluster(canonical=value, variants=Counter({value: count})))
    return clusters


@dataclass
class FieldDictionary:
    """The mapping from raw reading to canonical value, for one field in one scope."""

    field_name: str
    scope: Any
    mapping: Dict[str, str] = field(default_factory=dict)
    clusters: List[Cluster] = field(default_factory=list)
    contested: bool = False

    def apply(self, value: str) -> str:
        return self.mapping.get(nfc(value), nfc(value))


def build_field_dictionary(
    values: Sequence[str],
    field_name: str,
    scope: Any,
    collapse_to_one: bool,
    threshold: float = SIMILARITY_THRESHOLD,
) -> FieldDictionary:
    """Build the raw-to-canonical mapping for one field within one scope.

    ``collapse_to_one`` is for fields that must hold a single value across the scope
    (a constituency's district). Everything then maps to the dominant cluster. When the
    top two clusters are too close to separate the dictionary is marked ``contested``
    and left as a no-op, because forcing a winner there would fabricate agreement.
    """
    clusters = cluster_values(values, threshold)
    dictionary = FieldDictionary(field_name=field_name, scope=scope, clusters=clusters)
    if not clusters:
        return dictionary

    if collapse_to_one:
        ordered = sorted(clusters, key=lambda c: c.total, reverse=True)
        if len(ordered) > 1 and ordered[0].total < ordered[1].total * DOMINANCE_MARGIN:
            dictionary.contested = True
            return dictionary
        winner = ordered[0].canonical
        for cluster in clusters:
            for variant in cluster.variants:
                dictionary.mapping[variant] = winner
        return dictionary

    for cluster in clusters:
        for variant in cluster.variants:
            dictionary.mapping[variant] = cluster.canonical
    return dictionary


@dataclass
class CorpusDictionary:
    """Every field dictionary for a corpus, keyed by (field, scope)."""

    entries: Dict[Tuple[str, Any], FieldDictionary] = field(default_factory=dict)

    def apply_row(self, row: Dict[str, Any], scope_key: str = "ac_no_file") -> Dict[str, Any]:
        """Return a copy of ``row`` with canonical values written alongside the raw ones."""
        out = dict(row)
        scope = row.get(scope_key)
        for (field_name, entry_scope), dictionary in self.entries.items():
            if entry_scope != scope:
                continue
            raw = row.get(field_name, "")
            out[f"{field_name}_canonical"] = dictionary.apply(raw)
        return out

    def changes(self) -> List[Tuple[str, Any, str, str, int]]:
        """Every substitution the dictionary makes, for audit."""
        rows = []
        for (field_name, scope), dictionary in self.entries.items():
            for cluster in dictionary.clusters:
                for variant, count in cluster.variants.items():
                    target = dictionary.apply(variant)
                    if target != variant:
                        rows.append((field_name, scope, variant, target, count))
        return sorted(rows, key=lambda r: -r[4])

    def contested(self) -> List[FieldDictionary]:
        return [d for d in self.entries.values() if d.contested]


def build(
    rows: Sequence[Dict[str, Any]],
    scope_key: str = "ac_no_file",
    constant_fields: Sequence[str] = CONSTANT_PER_AC,
    repeated_fields: Sequence[str] = REPEATED_PER_AC,
    threshold: float = SIMILARITY_THRESHOLD,
) -> CorpusDictionary:
    """Build dictionaries for every (field, scope) pair present in ``rows``."""
    by_scope: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_scope[row.get(scope_key)].append(row)

    corpus = CorpusDictionary()
    for scope, scope_rows in by_scope.items():
        for field_name in constant_fields:
            values = [r.get(field_name, "") for r in scope_rows]
            corpus.entries[(field_name, scope)] = build_field_dictionary(
                values, field_name, scope, collapse_to_one=True, threshold=threshold
            )
        for field_name in repeated_fields:
            values = [r.get(field_name, "") for r in scope_rows]
            corpus.entries[(field_name, scope)] = build_field_dictionary(
                values, field_name, scope, collapse_to_one=False, threshold=threshold
            )
    return corpus


def canonical_field(name: str) -> str:
    return f"{name}_canonical"


def apply_to_rows(
    rows: Sequence[Dict[str, Any]],
    corpus: Optional[CorpusDictionary] = None,
    scope_key: str = "ac_no_file",
) -> List[Dict[str, Any]]:
    """Build (if needed) and apply a dictionary, returning canonicalised copies."""
    corpus = corpus or build(rows, scope_key=scope_key)
    return [corpus.apply_row(row, scope_key=scope_key) for row in rows]
