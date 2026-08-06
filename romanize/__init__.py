"""Transliteration of the Assam roll dataset's native-script fields.

A separate stage from extraction: it reads the published dataset and writes its own
artifacts, and imports nothing from ``assam_rolls``. Nothing here changes the extraction
pipeline or the shipped ``parts.*`` files.

The product is a **lookup table** (``dataset/transliteration.csv.gz``), not a model.
31,486 rows collapse to 2,817 distinct strings across the five administrative fields --
41 districts alone cover 100% of rows -- so the table is small enough to review by hand,
and a reviewed entry outlives whichever tool made the first guess.

Transliteration, never translation: ``ৰাজহ চক্ৰ`` is *rajah chakra*, not *Revenue Circle*.
``guards`` enforces that over the whole table.
"""

from . import guards, lookup, vocabulary

__all__ = ["guards", "lookup", "vocabulary"]
