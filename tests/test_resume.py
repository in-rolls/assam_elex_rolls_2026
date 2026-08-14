"""A part resumed from the bucket has its words but not its images.

Composites are never uploaded -- a terabyte for the state, and cheap to rebuild -- so this is the
normal state of every part on a machine that restarted. The two questions that decide what happens
to it, ``stage1.done`` and ``stage2.ready``, have each been wrong about it in a different and
expensive direction, so both are pinned here against all three states.
"""

from __future__ import annotations

import json
from pathlib import Path

from electors import stage1, stage2

PLACEMENTS = {
    "composite000.png": [{"left": 0, "top": 0, "right": 10, "bottom": 10, "page": 3, "box": 0}],
    "composite001.png": [{"left": 0, "top": 0, "right": 10, "bottom": 10, "page": 3, "box": 1}],
}


def _part(tmp_path: Path, images: bool, words: bool) -> Path:
    part = tmp_path / "part0001"
    part.mkdir()
    (part / "manifest.jsonl").write_text("{}\n", encoding="utf-8")
    (part / "placements.json").write_text(json.dumps(PLACEMENTS), encoding="utf-8")
    (part / "side.json").write_text(
        json.dumps({"stage1_version": stage1.STAGE1_VERSION, "pages": {}}), encoding="utf-8"
    )
    for name in PLACEMENTS:
        if images:
            (part / name).write_bytes(b"not really a png")
        if words:
            (part / f"{Path(name).stem}.words.json").write_text("[]", encoding="utf-8")
    return part


def test_images_present_is_prepared(tmp_path: Path) -> None:
    _part(tmp_path, images=True, words=False)
    assert stage1.done(tmp_path, 1)
    assert stage2.ready(tmp_path / "part0001")


def test_words_without_images_is_prepared(tmp_path: Path) -> None:
    """The resumed-from-bucket case. Calling this unprepared re-renders the whole state."""
    _part(tmp_path, images=False, words=True)
    assert stage1.done(tmp_path, 1)
    assert stage2.ready(tmp_path / "part0001")


def test_neither_is_not_prepared(tmp_path: Path) -> None:
    """AC101's failure: metadata alone looked finished, and stage two found nothing to read."""
    _part(tmp_path, images=False, words=False)
    assert not stage1.done(tmp_path, 1)
    assert not stage2.ready(tmp_path / "part0001")


def test_one_image_missing_its_words_is_not_prepared(tmp_path: Path) -> None:
    """Every named composite must be readable, not just one of them."""
    part = _part(tmp_path, images=False, words=True)
    (part / "composite001.words.json").unlink()
    assert not stage1.done(tmp_path, 1)
    assert not stage2.ready(part)


def test_composites_are_named_from_the_record(tmp_path: Path) -> None:
    """Both images are named even with nothing on disk, or stage two reads half the part."""
    part = _part(tmp_path, images=False, words=True)
    assert [p.name for p in stage2.composites(part)] == sorted(PLACEMENTS)
