"""The cache's resumability guarantees.

A statewide run is ~28,000 pages over hours; the properties that make resumption safe
rather than merely convenient are that a write is atomic, that an entry is keyed to the
source bytes it came from, and that a damaged entry costs one re-extraction instead of
the run.
"""

from __future__ import annotations

import json
import os

from assam_rolls import cache
from assam_rolls.schema import PIPELINE_VERSION

ROW = {"ac_no": 100, "district": "যোৰহাট"}
SECTIONS = [{"section_no": 1, "section_name": "পকিমুৰী"}]
SHA = "a" * 64


class TestRoundTrip:
    def test_write_then_read(self, tmp_path):
        cache.write_entry(tmp_path, "100-0001", ROW, SECTIONS, SHA)
        entry = cache.read_entry(tmp_path, "100-0001")
        assert entry["row"] == ROW
        assert entry["sections"] == SECTIONS
        assert entry["pdf_sha256"] == SHA
        assert entry["pipeline_version"] == PIPELINE_VERSION

    def test_creates_the_directory(self, tmp_path):
        target = tmp_path / "nested" / "cache"
        cache.write_entry(target, "100-0001", ROW, SECTIONS, SHA)
        assert (target / "100-0001.json").exists()

    def test_assamese_survives(self, tmp_path):
        cache.write_entry(tmp_path, "100-0001", ROW, SECTIONS, SHA)
        raw = (tmp_path / "100-0001.json").read_text(encoding="utf-8")
        assert "যোৰহাট" in raw

    def test_missing_entry_is_none(self, tmp_path):
        assert cache.read_entry(tmp_path, "nope") is None


class TestFreshness:
    def test_matching_hash_is_fresh(self, tmp_path):
        cache.write_entry(tmp_path, "k", ROW, SECTIONS, SHA)
        assert cache.is_fresh(cache.read_entry(tmp_path, "k"), SHA)

    def test_changed_source_bytes_invalidate(self, tmp_path):
        """A re-issued PDF must be re-extracted, never served from cache."""
        cache.write_entry(tmp_path, "k", ROW, SECTIONS, SHA)
        assert not cache.is_fresh(cache.read_entry(tmp_path, "k"), "b" * 64)

    def test_pipeline_version_bump_invalidates(self, tmp_path):
        """Old results must not be mixed into output produced by newer parsing rules."""
        cache.write_entry(tmp_path, "k", ROW, SECTIONS, SHA)
        path = tmp_path / "k.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["pipeline_version"] = "0.0.1-old"
        path.write_text(json.dumps(payload), encoding="utf-8")
        assert not cache.is_fresh(cache.read_entry(tmp_path, "k"), SHA)

    def test_missing_entry_is_not_fresh(self):
        assert not cache.is_fresh(None, SHA)


class TestDurability:
    def test_corrupt_entry_is_treated_as_missing(self, tmp_path):
        """One truncated file costs a re-extraction, not the run."""
        (tmp_path / "k.json").write_text("{not json", encoding="utf-8")
        assert cache.read_entry(tmp_path, "k") is None

    def test_no_temp_files_survive_a_successful_write(self, tmp_path):
        cache.write_entry(tmp_path, "k", ROW, SECTIONS, SHA)
        assert [p.name for p in tmp_path.iterdir()] == ["k.json"]

    def test_a_failed_write_leaves_no_partial_entry(self, tmp_path, monkeypatch):
        """A kill mid-write must not leave a truncated file that later parses as valid."""
        real_replace = os.replace

        def explode(src, dst):
            raise OSError("disk full")

        monkeypatch.setattr(cache.os, "replace", explode)
        try:
            cache.write_entry(tmp_path, "k", ROW, SECTIONS, SHA)
        except OSError:
            pass
        monkeypatch.setattr(cache.os, "replace", real_replace)
        assert not (tmp_path / "k.json").exists()
        assert list(tmp_path.iterdir()) == []

    def test_rewrite_replaces_rather_than_appends(self, tmp_path):
        cache.write_entry(tmp_path, "k", ROW, SECTIONS, SHA)
        cache.write_entry(tmp_path, "k", {"ac_no": 101}, [], "c" * 64)
        entry = cache.read_entry(tmp_path, "k")
        assert entry["row"] == {"ac_no": 101}
        assert entry["pdf_sha256"] == "c" * 64


class TestClear:
    def test_removes_every_entry_and_counts_them(self, tmp_path):
        for key in ("a", "b", "c"):
            cache.write_entry(tmp_path, key, ROW, SECTIONS, SHA)
        assert cache.clear(tmp_path) == 3
        assert list(tmp_path.glob("*.json")) == []

    def test_absent_directory_is_not_an_error(self, tmp_path):
        assert cache.clear(tmp_path / "missing") == 0


class TestResumeSemantics:
    def test_a_second_pass_skips_everything_already_done(self, tmp_path):
        """The behaviour resumption rests on, stated as a property."""
        keys = [f"100-{n:04d}" for n in range(1, 6)]
        hashes = {key: f"{i:064d}" for i, key in enumerate(keys)}

        def pass_over(done):
            extracted = []
            for key in keys:
                if cache.is_fresh(cache.read_entry(tmp_path, key), hashes[key]):
                    continue
                cache.write_entry(tmp_path, key, ROW, SECTIONS, hashes[key])
                extracted.append(key)
            done.extend(extracted)
            return extracted

        done = []
        assert pass_over(done) == keys  # cold
        assert pass_over(done) == []  # warm: nothing repeats

    def test_an_interrupted_pass_resumes_at_the_break(self, tmp_path):
        keys = [f"100-{n:04d}" for n in range(1, 6)]
        for key in keys[:2]:
            cache.write_entry(tmp_path, key, ROW, SECTIONS, SHA)
        remaining = [k for k in keys if not cache.is_fresh(cache.read_entry(tmp_path, k), SHA)]
        assert remaining == keys[2:]
