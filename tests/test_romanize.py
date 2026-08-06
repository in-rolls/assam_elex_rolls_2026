"""The transliteration stage: the guard, and the table's memory of hand review."""

from __future__ import annotations

from romanize import guards, lookup, vocabulary


class TestTransliterationNotTranslation:
    """The one rule that must not drift, and the loanword case that broke v1."""

    def test_a_translated_suffix_is_caught(self):
        assert guards.violations("গোসাইগাঁও থানা", "gossaigaon police station")

    def test_the_transliterated_suffix_is_fine(self):
        assert not guards.violations("গোসাইগাঁও থানা", "gossaigaon thana")

    def test_a_loanword_is_not_a_translation(self):
        """টাউন *is* English 'town' borrowed into Assamese; rendering it 'town' is right.

        The first version banned the English word outright and failed on this the first
        time it ran over real data.
        """
        assert not guards.violations("লামডিং ৰেলৱে টাউন", "lumding relway town")

    def test_the_native_word_for_town_is_still_caught(self):
        assert guards.violations("গুৱাহাটী চহৰ", "guwahati town")

    def test_a_banned_word_inside_a_name_does_not_trip(self):
        assert not guards.violations("পাৰ্টাবঘৰ", "partabghar")

    def test_check_reports_every_offender(self):
        problems = guards.check(
            [
                ("block", "কচুগাও উন্নয়ন খণ্ড", "kachugaon development block"),
                ("block", "কচুগাও উন্নয়ন খণ্ড", "kachugaon unnayan khanda"),
            ]
        )
        assert len(problems) == 1


class TestLookupKeepsHandReview:
    """Hand review is the expensive part; a re-run must never undo it."""

    def entry(self, native, freq=1, field="district"):
        return vocabulary.Entry(field=field, native=native, frequency=freq, lang="ASM")

    def test_manual_rows_survive_a_machine_refill(self):
        existing = {
            ("district", "কোকৰাব্মাৰ"): lookup.Row(
                "district", "কোকৰাব্মাৰ", 862, "Kokrajhar", lookup.MANUAL
            )
        }
        merged = lookup.merge(
            existing,
            [self.entry("কোকৰাব্মাৰ", 862)],
            filled={("district", "কোকৰাব্মাৰ"): ("kukorabmar", "indicxlit")},
        )
        assert merged[0].roman == "Kokrajhar"
        assert merged[0].source == lookup.MANUAL

    def test_manual_rows_still_track_corpus_frequency(self):
        """The romanization belongs to the review; the count belongs to the corpus."""
        existing = {("district", "ক"): lookup.Row("district", "ক", 10, "K", lookup.MANUAL)}
        merged = lookup.merge(existing, [self.entry("ক", 999)])
        assert merged[0].frequency == 999 and merged[0].roman == "K"

    def test_machine_rows_are_refreshed(self):
        existing = {("district", "ক"): lookup.Row("district", "ক", 1, "old", "indicxlit")}
        merged = lookup.merge(
            existing, [self.entry("ক")], filled={("district", "ক"): ("new", "indicxlit")}
        )
        assert merged[0].roman == "new"

    def test_round_trip_through_the_file(self, tmp_path):
        path = tmp_path / "t.csv.gz"
        rows = [lookup.Row("district", "কোকৰাঝাৰ", 5, "Kokrajhar", lookup.MANUAL, "note")]
        lookup.write(rows, path)
        back = lookup.read(path)
        assert back[("district", "কোকৰাঝাৰ")] == rows[0]

    def test_pending_is_the_review_queue_most_common_first(self):
        rows = [
            lookup.Row("district", "a", 1, "", ""),
            lookup.Row("district", "b", 99, "", ""),
            lookup.Row("district", "c", 50, "done", "indicxlit"),
        ]
        assert [r.native for r in lookup.pending(rows)] == ["b", "a"]
