"""Extraction of the elector records themselves, one row per voter.

``assam_rolls`` parses the two info pages that describe each part. This package parses
everything after them: the ruled grid of elector boxes that makes up the body of the roll.

The two join for free on ``(ac_no, part_no)`` -- the roll PDF and its info pages differ only
by an ``_INFO`` suffix -- and that join is also the quality check. Each part's info page
publishes how many electors it contains, verified arithmetically across all 31,486 parts, so
every part carries its own answer key for how many rows this package should produce.
"""

__all__ = ["fields", "grid", "pages"]
