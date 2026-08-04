"""Tests for request construction, batching, and response-to-row mapping.

Uses fake clients throughout: none of this needs an API key, and the parts worth
testing are the request shape and the row mapping, not the network.
"""

import base64
import json
import types

import pytest

from assam_rolls import extract, prompt, schema
from assam_rolls.render import PartRef

REF = PartRef("AC1_ASM Roll Info Pages.zip", "2026-...-ASM-3-WI_INFO.pdf", ac_no=1, part_no=3)

PARSED = {
    "ac_no": 1,
    "ac_name": "গোসাইগাঁও",
    "ac_name_roman": "Gossaigaon",
    "part_no": 3,
    "district": "কোকৰাঝাৰ",
    "ps_address": "৫৯০ বনগাঁও এল. পি. স্কুল, কঠা নং ১",
    "ward_no": "ৱাৰ্ড নং ৮",
    "electors_total": 850,
    "sections": [
        {"section_no": 1, "section_name": "বনগাঁও এফভি(পাৰ্ট)", "section_name_roman": "Bongaon FV"}
    ],
}


def text_block(payload):
    return types.SimpleNamespace(type="text", text=json.dumps(payload))


def fake_message(payload, stop_reason="end_turn"):
    return types.SimpleNamespace(content=[text_block(payload)], stop_reason=stop_reason)


class TestBuildParams:
    def test_includes_schema_and_effort(self):
        params = extract.build_params(b"png")
        assert params["output_config"]["format"]["schema"] is schema.PAGE1_JSON_SCHEMA
        assert params["output_config"]["effort"] == extract.DEFAULT_EFFORT

    def test_system_prompt_is_cached(self):
        """The layout description is identical per page; caching it is most of the saving."""
        system = extract.build_params(b"png")["system"][0]
        assert system["cache_control"]["type"] == "ephemeral"

    def test_image_is_base64_png(self):
        block = extract.build_params(b"rawbytes")["messages"][0]["content"][0]
        assert block["source"]["media_type"] == "image/png"
        assert base64.standard_b64decode(block["source"]["data"]) == b"rawbytes"

    def test_max_tokens_leaves_room_for_thinking(self):
        assert extract.build_params(b"png")["max_tokens"] >= 8192

    def test_model_and_effort_are_overridable(self):
        params = extract.build_params(b"png", model="claude-sonnet-5", effort="low")
        assert params["model"] == "claude-sonnet-5"
        assert params["output_config"]["effort"] == "low"

    def test_prompt_never_leaks_the_filename(self):
        """The filename holds the AC/part ground truth; leaking it would void the check."""
        params = extract.build_params(b"png")
        instruction = params["messages"][0]["content"][1]["text"]
        for token in ("EROLLGEN", "WI_INFO", ".pdf", "{filename}"):
            assert token not in instruction
            assert token not in params["system"][0]["text"]


class TestSystemPrompt:
    def test_renders_controlled_vocabulary(self):
        text = prompt.build_system_prompt()
        assert "GENERAL" in text and "SC" in text and "ST" in text
        assert "MALE" in text and "FEMALE" in text

    def test_has_no_unrendered_placeholders(self):
        assert "{" not in prompt.build_system_prompt().replace("{}", "")


class TestParseResponse:
    def test_extracts_json_from_text_block(self):
        assert extract._parse_response_text([text_block({"a": 1})]) == {"a": 1}

    def test_raises_without_a_text_block(self):
        with pytest.raises(extract.ExtractionError):
            extract._parse_response_text([types.SimpleNamespace(type="thinking", thinking="...")])

    def test_raises_on_malformed_json(self):
        block = types.SimpleNamespace(type="text", text="{not json")
        with pytest.raises(extract.ExtractionError):
            extract._parse_response_text([block])


class TestExtractPage:
    def test_returns_parsed_payload(self):
        client = types.SimpleNamespace(
            messages=types.SimpleNamespace(create=lambda **kw: fake_message(PARSED))
        )
        assert extract.extract_page(client, b"png")["ac_name"] == "গোসাইগাঁও"

    def test_refusal_raises(self):
        client = types.SimpleNamespace(
            messages=types.SimpleNamespace(
                create=lambda **kw: fake_message(PARSED, stop_reason="refusal")
            )
        )
        with pytest.raises(extract.ExtractionError, match="declined"):
            extract.extract_page(client, b"png")

    def test_truncation_raises_rather_than_returning_partial_data(self):
        client = types.SimpleNamespace(
            messages=types.SimpleNamespace(
                create=lambda **kw: fake_message(PARSED, stop_reason="max_tokens")
            )
        )
        with pytest.raises(extract.ExtractionError, match="max_tokens"):
            extract.extract_page(client, b"png")


class TestChunkRequests:
    def test_splits_on_request_count(self):
        requests = [
            extract.PageRequest(PartRef("z", "p", 1, n), b"x")
            for n in range(extract.MAX_BATCH_REQUESTS + 5)
        ]
        chunks = extract.chunk_requests(requests)
        assert len(chunks) == 2
        assert len(chunks[0]) == extract.MAX_BATCH_REQUESTS

    def test_splits_on_payload_size(self):
        """Base64 page images bind the 256 MB batch cap long before the count cap."""
        big = b"x" * (100 * 1024 * 1024)
        requests = [extract.PageRequest(PartRef("z", "p", 1, n), big) for n in range(4)]
        chunks = extract.chunk_requests(requests)
        assert len(chunks) > 1
        assert all(chunk for chunk in chunks)

    def test_preserves_every_request(self):
        requests = [extract.PageRequest(PartRef("z", "p", 1, n), b"x") for n in range(1200)]
        chunks = extract.chunk_requests(requests)
        assert sum(len(chunk) for chunk in chunks) == 1200

    def test_empty_input(self):
        assert extract.chunk_requests([]) == []


class TestCollectBatch:
    def _client(self, results):
        return types.SimpleNamespace(
            messages=types.SimpleNamespace(
                batches=types.SimpleNamespace(results=lambda batch_id: iter(results))
            )
        )

    def test_keys_by_custom_id_not_position(self):
        """Batch results arrive in arbitrary order; positional joins would corrupt rows."""
        results = [
            types.SimpleNamespace(
                custom_id="001-0003",
                result=types.SimpleNamespace(type="succeeded", message=fake_message(PARSED)),
            ),
            types.SimpleNamespace(
                custom_id="001-0001",
                result=types.SimpleNamespace(type="succeeded", message=fake_message({"ac_no": 9})),
            ),
        ]
        collected = extract.collect_batch(self._client(results), "batch_1")
        assert collected["001-0003"]["ac_no"] == 1
        assert collected["001-0001"]["ac_no"] == 9

    def test_records_failures_without_losing_the_batch(self):
        results = [
            types.SimpleNamespace(custom_id="a", result=types.SimpleNamespace(type="errored")),
            types.SimpleNamespace(
                custom_id="b",
                result=types.SimpleNamespace(type="succeeded", message=fake_message(PARSED)),
            ),
        ]
        collected = extract.collect_batch(self._client(results), "batch_1")
        assert "_error" in collected["a"]
        assert collected["b"]["ac_no"] == 1


class TestToPartRow:
    def test_populates_provenance_from_the_filename(self):
        row = extract.to_part_row(REF, PARSED, image_bytes=b"png")
        assert row["ac_no_file"] == 1
        assert row["part_no_file"] == 3
        assert row["source_zip"] == REF.zip_name
        assert len(row["sha256"]) == 64

    def test_keeps_assamese_verbatim(self):
        row = extract.to_part_row(REF, PARSED)
        assert row["ps_address"] == "৫৯০ বনগাঁও এল. পি. স্কুল, কঠা নং ১"

    def test_adds_deterministic_derivations(self):
        row = extract.to_part_row(REF, PARSED)
        assert row["ward_no_num"] == 8
        assert row["ps_address_digits"] == "590 বনগাঁও এল. পি. স্কুল, কঠা নং 1"

    def test_has_every_column(self):
        assert set(extract.to_part_row(REF, PARSED)) >= set(schema.PART_COLUMNS)

    def test_extraction_error_is_flagged_for_review(self):
        row = extract.to_part_row(REF, {"_error": "refusal"})
        assert row["needs_review"] is True
        assert row["flags"] == "extraction_error"


class TestToSectionRows:
    def test_explodes_sections(self):
        rows = extract.to_section_rows(REF, PARSED)
        assert len(rows) == 1
        assert rows[0]["ac_no"] == 1 and rows[0]["part_no"] == 3
        assert rows[0]["section_name"] == "বনগাঁও এফভি(পাৰ্ট)"

    def test_normalizes_digits_in_section_names(self):
        parsed = {
            "sections": [
                {"section_no": 1, "section_name": "ৱাৰ্ড নং ৮(অংশ)", "section_name_roman": "Ward 8"}
            ]
        }
        assert extract.to_section_rows(REF, parsed)[0]["section_name_digits"] == "ৱাৰ্ড নং 8(অংশ)"

    def test_falls_back_to_page_order_when_number_missing(self):
        parsed = {"sections": [{"section_name": "ক", "section_name_roman": "Ka"}]}
        assert extract.to_section_rows(REF, parsed)[0]["section_no"] == 1

    def test_no_sections_yields_no_rows(self):
        assert extract.to_section_rows(REF, {"sections": None}) == []
        assert extract.to_section_rows(REF, {}) == []
