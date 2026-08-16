"""Extraction of the elector records themselves, one row per voter.

``assam_rolls`` parses the two info pages that describe each part. This package parses
everything after them: the ruled grid of elector boxes that makes up the body of the roll.

The two join on the filename-derived keys: elector ``(ac_no, part_no)`` to info-page
``(ac_no_file, part_no_file)``. The OCR-read info-page keys are validation fields, not identifiers.
Each part's info page publishes how many electors it contains, verified arithmetically across all
31,486 parts, so every part carries its own answer key for how many rows this package should
produce.
"""

__all__ = ["fields", "grid", "pages"]
