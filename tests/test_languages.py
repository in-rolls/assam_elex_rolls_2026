"""Language profiles.

The corpus is 112 Assamese constituencies, 13 Bengali and one English. Everything here
guards the same property from different angles: a page must be read with the model and
labels its own language calls for, and any uncertainty about which language that is has
to stop the run rather than default to Assamese.
"""

from __future__ import annotations

import json

import pytest

from assam_rolls import languages

#: The Assamese values as they stood before profiles existed, copied from the pre-refactor
#: `parse.py` and `schema.py`. Pinning them here is what stops the move to JSON from
#: quietly changing how 112 constituencies are read.
HISTORICAL_ASM = {
    "locality_labels": (
        "মূল চহৰ/গাঁও",
        "ৱাৰ্ড নং",
        "ডাকঘৰ",
        "পুলিচ থানা",
        "ব্লক",
        "ৰাজহ চক্ৰ",
        "জিলা",
        "পিনকোড",
    ),
    "revision_labels": (
        "সংশোধনৰ বছৰ",
        "ভিত্তি তাৰিখ",
        "সংশোধনৰ প্ৰকাৰ",
        "প্ৰকাশনৰ তাৰিখ",
    ),
    "address_label": "ভোটগ্ৰহন কেন্দ্ৰৰ ঠিকনা",
    "locality_value_x": 320,
    "revision_value_x": 213,
    "reservation_map": {
        "সাধাৰণ": "GENERAL",
        "অনুসূচিত জাতি": "SC",
        "অনুসূচিত জনজাতি": "ST",
    },
    "ps_type_map": {
        "পুৰুষ": "MALE",
        "মহিলা": "FEMALE",
        "সাধাৰণ": "GENERAL",
        "সাধাৰন": "GENERAL",
    },
}


class TestAssameseProfileIsUnchanged:
    """The 890-part run's accuracy figures were measured with these exact values."""

    @pytest.mark.parametrize("field", sorted(HISTORICAL_ASM))
    def test_field_matches_the_pre_refactor_value(self, field):
        profile = languages.profile_for("ASM")
        assert getattr(profile, field) == HISTORICAL_ASM[field]

    def test_uses_the_assamese_model(self):
        assert languages.profile_for("ASM").tesseract_lang == "asm"

    def test_is_hand_written_not_derived(self):
        assert not languages.profile_for("ASM").is_derived


class TestLookup:
    def test_is_case_insensitive(self):
        assert languages.profile_for("asm").code == "ASM"

    def test_caches_repeat_lookups(self):
        assert languages.profile_for("ASM") is languages.profile_for("ASM")

    def test_unknown_language_raises_rather_than_defaulting(self):
        """The whole point: a Bengali page must never be read as Assamese by accident."""
        with pytest.raises(languages.UnknownLanguage):
            languages.profile_for("XXX")

    def test_missing_language_raises(self):
        with pytest.raises(languages.UnknownLanguage):
            languages.profile_for(None)

    def test_error_names_what_is_available_and_how_to_fix_it(self):
        with pytest.raises(languages.UnknownLanguage) as excinfo:
            languages.profile_for("TAM")
        message = str(excinfo.value)
        assert "ASM" in message
        assert "calibrate" in message


class TestRoundTrip:
    def make(self, **overrides):
        payload = {
            "code": "TST",
            "tesseract_lang": "ben",
            "stable_h_rules": [124, 157, 191, 225, 399, 433, 483],
            "locality_fields": [f"f{i}" for i in range(8)],
            "locality_labels": [f"L{i}" for i in range(8)],
            "revision_labels": [f"R{i}" for i in range(4)],
            "address_label": "A",
            "locality_value_x": 300,
            "revision_value_x": 200,
            "reservation_map": {"x": "GENERAL"},
            "ps_type_map": {"y": "MALE"},
        }
        payload.update(overrides)
        return payload

    def test_dict_round_trip_is_lossless(self):
        profile = languages.from_dict(self.make())
        assert languages.from_dict(profile.to_dict()) == profile

    def test_json_survives_non_ascii(self, tmp_path, monkeypatch):
        monkeypatch.setattr(languages, "PROFILE_DIR", tmp_path)
        languages.clear_cache()
        profile = languages.from_dict(self.make(code="BEN", locality_labels=["মূল"] * 8))
        path = languages.write_profile(profile)
        assert "মূল" in path.read_text(encoding="utf-8")
        assert languages.profile_for("BEN") == profile

    def test_written_profile_is_human_readable_json(self, tmp_path, monkeypatch):
        """It gets reviewed as a git diff, so it must not be one long line."""
        monkeypatch.setattr(languages, "PROFILE_DIR", tmp_path)
        languages.clear_cache()
        path = languages.write_profile(languages.from_dict(self.make(code="BEN")))
        assert len(path.read_text(encoding="utf-8").splitlines()) > 10
        json.loads(path.read_text(encoding="utf-8"))


class TestValidation:
    def test_label_and_field_counts_must_correspond(self):
        """Labels map to fields row for row; a mismatch would misalign the whole block."""
        with pytest.raises(ValueError, match="row for row"):
            languages.LanguageProfile(
                code="TST",
                tesseract_lang="ben",
                stable_h_rules=(124, 157, 191, 225),
                locality_fields=tuple("abcdefgh"),
                locality_labels=("only", "three", "labels"),
                revision_labels=("a", "b", "c", "d"),
                address_label="A",
                locality_value_x=300,
                revision_value_x=200,
            )

    def test_wrong_revision_label_count_is_rejected(self):
        with pytest.raises(ValueError, match="4 revision labels"):
            languages.LanguageProfile(
                code="TST",
                tesseract_lang="ben",
                stable_h_rules=(124, 157, 191, 225),
                locality_fields=tuple("abcdefgh"),
                locality_labels=tuple("abcdefgh"),
                revision_labels=("a", "b"),
                address_label="A",
                locality_value_x=300,
                revision_value_x=200,
            )


class TestCorpusCoverage:
    def test_every_language_in_the_corpus_maps_to_a_model(self):
        assert set(languages.TESSERACT_LANG) == set(languages.KNOWN_LANGUAGES)

    def test_bengali_and_assamese_use_different_models(self):
        """They share a script but not a letter inventory (ৰ/ৱ are Assamese-only)."""
        assert languages.TESSERACT_LANG["ASM"] != languages.TESSERACT_LANG["BEN"]
