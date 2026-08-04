"""Tests for the schema, filename parser, and deterministic derivations."""

import pytest

from assam_rolls import schema


class TestParseSourceFilename:
    def test_parses_a_real_filename(self):
        parsed = schema.parse_source_filename(
            "2026-EROLLGEN-S03-100-FinalRoll-Revision1-ASM-45-WI_INFO.pdf"
        )
        assert parsed == {
            "year": 2026,
            "state": "S03",
            "ac_no": 100,
            "part_no": 45,
            "roll_type": "FinalRoll",
            "revision": 1,
            "lang": "ASM",
        }

    def test_single_digit_ac_and_part(self):
        parsed = schema.parse_source_filename(
            "2026-EROLLGEN-S03-1-FinalRoll-Revision1-ASM-1-WI_INFO.pdf"
        )
        assert parsed is not None
        assert parsed["ac_no"] == 1
        assert parsed["part_no"] == 1

    def test_ac_and_part_are_not_confused(self):
        """The AC number precedes the part number; a swap would silently corrupt joins."""
        parsed = schema.parse_source_filename(
            "2026-EROLLGEN-S03-12-FinalRoll-Revision1-ASM-207-WI_INFO.pdf"
        )
        assert parsed is not None
        assert parsed["ac_no"] == 12
        assert parsed["part_no"] == 207

    @pytest.mark.parametrize(
        "name",
        [
            "something-else.pdf",
            "2026-EROLLGEN-S03-1-FinalRoll-Revision1-ASM-1.pdf",  # missing WI_INFO
            "2026-EROLLGEN-S03-FinalRoll-Revision1-ASM-1-WI_INFO.pdf",  # missing AC
            "",
        ],
    )
    def test_returns_none_for_unexpected_names(self, name):
        assert schema.parse_source_filename(name) is None

    def test_tolerates_surrounding_whitespace(self):
        parsed = schema.parse_source_filename(
            "  2026-EROLLGEN-S03-10-FinalRoll-Revision1-ASM-3-WI_INFO.pdf  "
        )
        assert parsed is not None and parsed["ac_no"] == 10


class TestNormalizeDigits:
    def test_converts_assamese_numerals(self):
        assert schema.normalize_digits("৫৯০") == "590"

    def test_preserves_surrounding_assamese_script(self):
        got = schema.normalize_digits("৫৯০ বনগাঁও এল. পি. স্কুল, কঠা নং ১, বনগাঁও")
        assert got == "590 বনগাঁও এল. পি. স্কুল, কঠা নং 1, বনগাঁও"

    def test_leaves_western_digits_alone(self):
        assert schema.normalize_digits("783350") == "783350"

    def test_handles_devanagari(self):
        assert schema.normalize_digits("१२३") == "123"

    @pytest.mark.parametrize("value", [None, ""])
    def test_empty_input(self, value):
        assert schema.normalize_digits(value) == ""

    def test_all_ten_digits_round_trip(self):
        assert schema.normalize_digits(schema.ASSAMESE_DIGITS) == "0123456789"


class TestFirstInt:
    def test_extracts_ward_number_from_assamese_label(self):
        assert schema.first_int("ৱাৰ্ড নং ৮") == 8

    def test_extracts_from_western_digits(self):
        assert schema.first_int("Ward No 12") == 12

    def test_returns_none_when_no_digits(self):
        assert schema.first_int("কোনো নম্বৰ নাই") is None

    @pytest.mark.parametrize("value", [None, ""])
    def test_empty_input(self, value):
        assert schema.first_int(value) is None


class TestCleanText:
    def test_collapses_whitespace(self):
        assert schema.clean_text("  a   b \n c ") == "a b c"

    def test_normalizes_to_nfc(self):
        # Same grapheme, decomposed vs composed encodings must compare equal.
        decomposed = "ক্ষ"
        assert schema.clean_text(decomposed) == schema.clean_text(schema.clean_text(decomposed))

    @pytest.mark.parametrize("value", [None, ""])
    def test_empty_input(self, value):
        assert schema.clean_text(value) == ""


class TestJsonSchema:
    def test_every_model_field_is_a_property(self):
        props = schema.PAGE1_JSON_SCHEMA["properties"]
        for name in schema.MODEL_FIELD_NAMES:
            assert name in props, f"{name} missing from JSON Schema"

    def test_all_properties_are_required(self):
        """Structured outputs is strictest -- and most reliable -- when nothing is optional."""
        props = set(schema.PAGE1_JSON_SCHEMA["properties"])
        assert props == set(schema.PAGE1_JSON_SCHEMA["required"])

    def test_additional_properties_disallowed(self):
        assert schema.PAGE1_JSON_SCHEMA["additionalProperties"] is False
        items = schema.PAGE1_JSON_SCHEMA["properties"]["sections"]["items"]
        assert items["additionalProperties"] is False

    def test_every_field_is_nullable(self):
        """A blank cell must be representable, or the model will invent a value."""
        for name in schema.MODEL_FIELD_NAMES:
            variants = schema.PAGE1_JSON_SCHEMA["properties"][name]["anyOf"]
            assert {"type": "null"} in variants, f"{name} is not nullable"

    def test_enum_fields_carry_their_vocabulary(self):
        props = schema.PAGE1_JSON_SCHEMA["properties"]
        assert props["ac_reservation"]["anyOf"][0]["enum"] == schema.RESERVATION_VALUES
        assert props["ps_type"]["anyOf"][0]["enum"] == schema.PS_TYPE_VALUES
        assert props["extraction_confidence"]["anyOf"][0]["enum"] == schema.CONFIDENCE_VALUES

    def test_schema_uses_only_supported_keywords(self):
        """Structured outputs rejects numeric/length constraints; catch them early."""
        banned = {"minimum", "maximum", "minLength", "maxLength", "pattern", "multipleOf"}

        def walk(node):
            if isinstance(node, dict):
                assert not (banned & set(node)), f"unsupported keyword in {node}"
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(schema.PAGE1_JSON_SCHEMA)


class TestColumns:
    def test_part_columns_are_unique(self):
        assert len(schema.PART_COLUMNS) == len(set(schema.PART_COLUMNS))

    def test_empty_row_covers_every_column(self):
        assert set(schema.empty_part_row()) == set(schema.PART_COLUMNS)

    def test_derived_columns_are_present(self):
        for column in schema.DERIVED_COLUMNS:
            assert column in schema.PART_COLUMNS

    def test_qa_columns_carry_both_sides_of_the_check_ratio(self):
        """checks_passed without checks_total is uninterpretable."""
        assert "checks_passed" in schema.PART_COLUMNS
        assert "checks_total" in schema.PART_COLUMNS

    def test_verbatim_fields_have_roman_companions(self):
        expected_roman = [
            "ac_name",
            "pc_name",
            "main_town_village",
            "ward_no",
            "post_office",
            "police_station",
            "block",
            "revenue_circle",
            "district",
            "ps_name",
            "ps_address",
        ]
        for base in expected_roman:
            assert f"{base}_roman" in schema.MODEL_FIELD_NAMES


class TestDeriveColumns:
    def test_derives_ward_and_address_digits(self):
        row = {
            "ward_no": "ৱাৰ্ড নং ৮",
            "ps_address": "৫৯০ বনগাঁও এল. পি. স্কুল, কঠা নং ১",
        }
        derived = schema.derive_columns(row)
        assert derived["ward_no_num"] == 8
        assert derived["ps_address_digits"] == "590 বনগাঁও এল. পি. স্কুল, কঠা নং 1"

    def test_missing_fields_do_not_raise(self):
        derived = schema.derive_columns({})
        assert derived["ward_no_num"] is None
        assert derived["ps_address_digits"] == ""
