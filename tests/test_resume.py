"""A part resumed from the bucket has its words but not its images.

Composites are never uploaded -- a terabyte for the state, and cheap to rebuild -- so this is the
normal state of every part on a machine that restarted. The two questions that decide what happens
to it, ``stage1.done`` and ``stage2.ready``, have each been wrong about it in a different and
expensive direction, so both are pinned here against all three states.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from electors import extract, stage1, stage2

#: One real word. An empty list now means "never usefully read", so a fixture that wants a
#: cached read has to contain something.
WORDS = '[["x", 0, 0, 10, 10]]'

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
            (part / f"{Path(name).stem}.words.json").write_text(WORDS, encoding="utf-8")
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


def test_a_cached_summary_page_is_not_counted_as_bought(tmp_path: Path) -> None:
    """``images_billed`` is what the run is charged for, so a free read must not appear in it.

    The count decides the reported Vision cost per constituency. Incrementing it on the cached
    path adds one phantom image per part -- invisible while every part was being read for the
    first time, and the whole state once the fleet resumes instead of re-rendering.
    """
    part = tmp_path / "part0001"
    part.mkdir()
    Image.new("L", (40, 20), 255).save(part / "summary_page.png")
    (part / "summary_page.words.json").write_text(WORDS, encoding="utf-8")

    def never(_images):
        raise AssertionError("Vision was called for a page whose words are already cached")

    _, paid = stage2.summary_from_vision(part, "", never)
    assert paid is False


def test_composites_are_named_from_the_record(tmp_path: Path) -> None:
    """Both images are named even with nothing on disk, or stage two reads half the part."""
    part = _part(tmp_path, images=False, words=True)
    assert [p.name for p in stage2.composites(part)] == sorted(PLACEMENTS)


def test_a_corrupt_rows_cache_is_treated_as_absent(tmp_path: Path) -> None:
    """AC20 died on one unreadable cache file out of 242 parts.

    The words the rows were parsed from are still there, so nothing is lost by parsing again --
    and a whole constituency is lost by refusing to.
    """
    from electors import run

    cache = tmp_path / "rows.jsonl"
    cache.write_text("\x00 not json at all\n", encoding="utf-8")
    assert run._cached_rows(cache) is None
    assert not cache.exists()


def test_a_good_rows_cache_is_read(tmp_path: Path) -> None:
    from electors import run

    cache = tmp_path / "rows.jsonl"
    cache.write_text('{"name": "a"}\n\n{"name": "b"}\n', encoding="utf-8")
    assert run._cached_rows(cache) == [{"name": "a"}, {"name": "b"}]
    assert cache.exists()


def _part_with_boxes(tmp_path: Path, boxes: int, cached_rows: int) -> Path:
    """A part stage one cut ``boxes`` from, whose cache holds ``cached_rows``."""
    from electors import pages as pages_mod
    from electors import stage1, summary

    part = tmp_path / "part0001"
    part.mkdir(exist_ok=True)
    (part / "manifest.jsonl").write_text("{}\n", encoding="utf-8")
    (part / "placements.json").write_text(json.dumps(PLACEMENTS), encoding="utf-8")
    for name in PLACEMENTS:
        (part / f"{Path(name).stem}.words.json").write_text(WORDS, encoding="utf-8")
    stage1.write_side(
        part / "side.json",
        {
            "pages": {
                3: {
                    "section": pages_mod.Section.MAIN,
                    "recognised": True,
                    "boxes": {(3, r, 0): "" for r in range(boxes)},
                }
            },
            "unknown": [],
            "summary": summary.RollSummary(
                male=boxes // 2, female=boxes - boxes // 2, third=0, total=boxes, scale=400
            ),
        },
    )
    (part / "rows.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "part_no": 1,
                    "roll_section": "main",
                    "pipeline_version": extract.PIPELINE_VERSION,
                }
            )
            + "\n"
            for _ in range(cached_rows)
        ),
        encoding="utf-8",
    )
    return part


def test_a_cache_covering_the_whole_part_is_kept(tmp_path: Path) -> None:
    from electors import run

    part = _part_with_boxes(tmp_path, boxes=100, cached_rows=100)
    run.read(tmp_path, "")
    assert (part / "rows.jsonl").exists()


def test_a_cache_covering_part_of_the_part_is_partial(tmp_path: Path) -> None:
    """AC33's 110 poisoned parts: 12 rows cached against 1,170 boxes cut.

    Stage two used to list the composites on disk, so a run that found some of a part's images
    read those and cached the answer as the whole part. Every later run then served it.
    """
    from electors import run

    part = _part_with_boxes(tmp_path, boxes=1170, cached_rows=12)
    assert run.cache_is_partial(part, 12)
    assert not run.cache_is_partial(part, 1170)
    # Three rows short is the sparse-final-page defect, not a partial read.
    assert not run.cache_is_partial(part, 1167)


def test_a_short_cache_is_kept_when_the_part_cannot_be_read_again(tmp_path: Path) -> None:
    """Better a part that answers to reconciliation than one that stops the run."""
    from electors import run

    part = _part_with_boxes(tmp_path, boxes=1170, cached_rows=12)
    for words in part.glob("*.words.json"):
        words.unlink()
    run.read(tmp_path, "")
    assert (part / "rows.jsonl").exists()


def test_an_empty_words_file_does_not_count_as_a_read(tmp_path: Path) -> None:
    """AC33 kept 214 of them: the paperwork of a finished read with none of the result.

    A composite is a stack of text lines cut from elector boxes, so one with no words on it is a
    failed read. Treated as done, the part is skipped forever and reports no error while doing it.
    """
    part = _part(tmp_path, images=False, words=True)
    (part / "composite000.words.json").write_text("[]", encoding="utf-8")
    assert not stage1.readable(part)
    assert not stage1.done(tmp_path, 1)
    assert not stage2.ready(part)


def test_an_empty_read_is_not_written_down(tmp_path: Path) -> None:
    """Caching a failure makes it permanent, and every retry then reproduces it exactly."""
    stage2.save_words(tmp_path, "composite000.png", [])
    assert not stage2.words_path(tmp_path, "composite000.png").exists()


def test_an_empty_words_file_already_written_reads_as_absent(tmp_path: Path) -> None:
    stage2.words_path(tmp_path, "composite000.png").write_text("[]", encoding="utf-8")
    assert stage2.cached_words(tmp_path, "composite000.png") is None


def test_a_words_file_that_will_not_parse_reads_as_absent(tmp_path: Path) -> None:
    """AC20 part 24: a zero-length words file, and the constituency died on json.loads.

    It died inside the branch whose only job is to decide whether the image needs reading again,
    which is the one place that must never raise.
    """
    path = stage2.words_path(tmp_path, "composite000.png")
    path.write_bytes(b"")
    assert stage2.cached_words(tmp_path, "composite000.png") is None
    assert not stage1._has_words(path)


def _one_pixel(tmp_path: Path) -> Path:
    path = tmp_path / "composite000.png"
    Image.new("L", (10, 10), 255).save(path)
    return path


def test_a_per_image_vision_error_is_not_a_blank_image(tmp_path: Path, monkeypatch) -> None:
    """The batch endpoint reports per-image failure inside an HTTP 200.

    One night of treating that as "no words" cost twenty-two constituencies: parts came up
    hundreds of rows short, were correctly refused at the gate, and were parked as failed. The
    rows were never wrong; the reads had failed and nothing said so.
    """
    monkeypatch.setattr(stage2.time, "sleep", lambda _s: None)
    path = _one_pixel(tmp_path)
    calls = []

    def erroring(_images):
        calls.append(1)
        return [{"error": {"code": 8, "message": "RESOURCE_EXHAUSTED"}}]

    try:
        stage2._buy_words(tmp_path, path, "", erroring)
        raise AssertionError("a per-image error must not read as an empty image")
    except RuntimeError as exc:
        assert "RESOURCE_EXHAUSTED" in str(exc)
    assert len(calls) == stage2.IMAGE_ATTEMPTS
    assert stage2.cached_words(tmp_path, path.name) is None


def test_a_transient_error_recovers_on_retry(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(stage2.time, "sleep", lambda _s: None)
    path = _one_pixel(tmp_path)
    answers = [
        [{"error": {"message": "internal error"}}],
        [
            {
                "fullTextAnnotation": {
                    "pages": [
                        {
                            "blocks": [
                                {
                                    "paragraphs": [
                                        {
                                            "words": [
                                                {
                                                    "symbols": [{"text": "ক"}],
                                                    "boundingBox": {
                                                        "vertices": [
                                                            {"x": 0, "y": 0},
                                                            {"x": 5, "y": 5},
                                                        ]
                                                    },
                                                }
                                            ]
                                        }
                                    ]
                                }
                            ]
                        }
                    ]
                }
            }
        ],
    ]

    found = stage2._buy_words(tmp_path, path, "", lambda _i: answers.pop(0))
    assert [w.text for w in found] == ["ক"]
    assert stage2.cached_words(tmp_path, path.name) is not None


def test_a_re_prepared_part_loses_its_stale_caches(tmp_path: Path) -> None:
    """Words are keyed by image name and a re-render owes the old layout nothing.

    Stale words attributed to a new layout shuffle fields between electors silently -- counts
    match, reconciliation balances, and every name is somebody else's. Only 17.2% of tiles keep
    identical geometry between runs, so this is the norm after a re-render, not the exception.
    """
    (tmp_path / "composite000.words.json").write_text('[["x",0,0,1,1]]', encoding="utf-8")
    (tmp_path / "composite001.words.json").write_text('[["y",0,0,1,1]]', encoding="utf-8")
    (tmp_path / "rows.jsonl").write_text('{"name": "old"}\n', encoding="utf-8")
    (tmp_path / "summary_page.words.json").write_text('[["z",0,0,1,1]]', encoding="utf-8")

    stage1.clear_stale(tmp_path)

    assert not list(tmp_path.glob("composite*.words.json"))
    assert not (tmp_path / "rows.jsonl").exists()
    # The summary page is rendered from the PDF, not repacked; its read stays valid.
    assert (tmp_path / "summary_page.words.json").exists()


def test_the_pixel_budget_stays_below_the_observed_deadline() -> None:
    """A 67.0 MP composite deadline-exceeded on every attempt; the budget must stay under it."""
    from electors import vision

    assert vision.MAX_PIXELS * vision.PIXEL_MARGIN <= 50_000_000
