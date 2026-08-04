"""Claude extraction: one call per page, structured output, Batch API by default.

Two paths, same prompt and schema:

* ``extract_page`` -- a single synchronous call, for pilots and the gold set.
* ``submit_batches`` / ``collect_batch`` -- the Batch API, for production runs. Half
  price, and the right shape for tens of thousands of pages.

Requests are keyed by ``PartRef.key`` (``{ac:03d}-{part:04d}``) so results rejoin
their source unambiguously and a run can resume without redoing work.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import anthropic

from .prompt import USER_INSTRUCTION, build_system_prompt
from .render import PartRef, sha256_bytes
from .schema import (
    MODEL_FIELD_NAMES,
    PAGE1_JSON_SCHEMA,
    clean_text,
    derive_columns,
    empty_part_row,
    normalize_digits,
)

DEFAULT_MODEL = "claude-opus-5"

# Opus 5 thinks by default, and max_tokens caps thinking *plus* response text. Assamese
# tokenizes expensively and a full page yields ~1-1.5k output tokens, so leave headroom
# rather than risk truncation mid-record.
DEFAULT_MAX_TOKENS = 8192

# Effort trades accuracy against cost/latency. Transcribing a fixed form is not a deep
# reasoning task, so "medium" is the starting point -- but this is exactly what the gold
# set is for: sweep low/medium/high and pick on measured accuracy, don't guess.
DEFAULT_EFFORT = "medium"

# The Batches API caps a request set at 100k requests / 256 MB. A base64 page image is
# ~250 KB, so size binds long before count; chunk well under the limit.
MAX_BATCH_REQUESTS = 500
MAX_BATCH_BYTES = 180 * 1024 * 1024


class ExtractionError(RuntimeError):
    """Raised when a response cannot be turned into a part row."""


@dataclass
class PageRequest:
    """One page queued for extraction."""

    ref: PartRef
    image_bytes: bytes

    @property
    def key(self) -> str:
        return self.ref.key


def _image_block(image_bytes: bytes) -> Dict[str, Any]:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.standard_b64encode(image_bytes).decode("ascii"),
        },
    }


def build_params(
    image_bytes: bytes,
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> Dict[str, Any]:
    """Message parameters for one page.

    ``cache_control`` on the system prompt makes the layout description a cached prefix
    shared by every page in the run. ``output_config.format`` pins the response to the
    schema, so parsing cannot fail on malformed JSON.
    """
    return {
        "model": model,
        "max_tokens": max_tokens,
        "system": [
            {
                "type": "text",
                "text": build_system_prompt(),
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            }
        ],
        "output_config": {
            "effort": effort,
            "format": {"type": "json_schema", "schema": PAGE1_JSON_SCHEMA},
        },
        "messages": [
            {
                "role": "user",
                "content": [_image_block(image_bytes), {"type": "text", "text": USER_INSTRUCTION}],
            }
        ],
    }


def _parse_response_text(content: Sequence[Any]) -> Dict[str, Any]:
    for block in content:
        block_type = getattr(block, "type", None) or (
            block.get("type") if isinstance(block, dict) else None
        )
        if block_type != "text":
            continue
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        if not isinstance(text, str):
            raise ExtractionError("text block carried no string payload")
        try:
            return json.loads(text)
        except ValueError as exc:
            raise ExtractionError(f"response was not valid JSON: {exc}") from exc
    raise ExtractionError("response contained no text block")


def extract_page(
    client: anthropic.Anthropic,
    image_bytes: bytes,
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
) -> Dict[str, Any]:
    """Extract one page synchronously. Used for pilots and the gold set."""
    response = client.messages.create(**build_params(image_bytes, model=model, effort=effort))
    if response.stop_reason == "refusal":
        raise ExtractionError("request was declined by safety classifiers")
    if response.stop_reason == "max_tokens":
        raise ExtractionError("response hit max_tokens; raise the budget and retry")
    return _parse_response_text(response.content)


# ------------------------------------------------------------------------------ batch


def chunk_requests(requests: List[PageRequest]) -> List[List[PageRequest]]:
    """Split pages into batches that respect the Batches API size and count caps."""
    chunks: List[List[PageRequest]] = []
    current: List[PageRequest] = []
    current_bytes = 0

    for request in requests:
        # base64 inflates by 4/3; the rest of the envelope is negligible beside the image.
        encoded_size = (len(request.image_bytes) * 4) // 3
        too_many = len(current) >= MAX_BATCH_REQUESTS
        too_big = current and current_bytes + encoded_size > MAX_BATCH_BYTES
        if too_many or too_big:
            chunks.append(current)
            current, current_bytes = [], 0
        current.append(request)
        current_bytes += encoded_size

    if current:
        chunks.append(current)
    return chunks


def submit_batches(
    client: anthropic.Anthropic,
    requests: List[PageRequest],
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
) -> List[str]:
    """Submit pages as one or more batches; returns the batch ids."""
    batch_ids: List[str] = []
    for chunk in chunk_requests(requests):
        batch = client.messages.batches.create(
            requests=[
                {
                    "custom_id": request.key,
                    "params": build_params(request.image_bytes, model=model, effort=effort),
                }
                for request in chunk
            ]
        )
        batch_ids.append(batch.id)
    return batch_ids


def batch_is_done(client: anthropic.Anthropic, batch_id: str) -> bool:
    return client.messages.batches.retrieve(batch_id).processing_status == "ended"


def collect_batch(client: anthropic.Anthropic, batch_id: str) -> Dict[str, Dict[str, Any]]:
    """Collect a finished batch as ``{custom_id: parsed_or_error}``.

    Results arrive in arbitrary order, so they are keyed by ``custom_id`` and never by
    position. Failures are recorded rather than raised, so one bad page cannot lose the
    rest of the batch.
    """
    collected: Dict[str, Dict[str, Any]] = {}
    for result in client.messages.batches.results(batch_id):
        key = result.custom_id
        outcome = result.result
        if outcome.type != "succeeded":
            collected[key] = {"_error": f"batch result {outcome.type}"}
            continue
        message = outcome.message
        if message.stop_reason == "refusal":
            collected[key] = {"_error": "refusal"}
            continue
        try:
            collected[key] = _parse_response_text(message.content)
        except ExtractionError as exc:
            collected[key] = {"_error": str(exc)}
    return collected


# ------------------------------------------------------------------------------- rows


def to_part_row(
    ref: PartRef,
    parsed: Dict[str, Any],
    image_bytes: Optional[bytes] = None,
    model: str = DEFAULT_MODEL,
    extracted_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Turn one parsed response into a ``parts.csv`` row with provenance and derivations."""
    row = empty_part_row()
    row.update(
        {
            "source_zip": ref.zip_name,
            "source_pdf": ref.pdf_name,
            "sha256": sha256_bytes(image_bytes) if image_bytes else "",
            "ac_no_file": ref.ac_no,
            "part_no_file": ref.part_no,
            "model": model,
            "extracted_at": extracted_at
            or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    )

    for name in MODEL_FIELD_NAMES:
        value = parsed.get(name)
        row[name] = clean_text(value) if isinstance(value, str) else value

    row.update(derive_columns(row))
    if "_error" in parsed:
        row["flags"] = "extraction_error"
        row["needs_review"] = True
    return row


def to_section_rows(ref: PartRef, parsed: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Explode the ``sections`` array into ``part_sections.csv`` rows."""
    rows: List[Dict[str, Any]] = []
    for index, section in enumerate(parsed.get("sections") or [], start=1):
        if not isinstance(section, dict):
            continue
        name = clean_text(section.get("section_name"))
        rows.append(
            {
                "ac_no": ref.ac_no,
                "part_no": ref.part_no,
                # Fall back to page order when the model reports no leading number.
                "section_no": section.get("section_no") if section.get("section_no") else index,
                "section_name": name,
                "section_name_roman": clean_text(section.get("section_name_roman")),
                "section_name_digits": normalize_digits(name),
            }
        )
    return rows


def load_pages(pages_dir: Path, refs: Iterable[PartRef]) -> List[PageRequest]:
    """Pair rendered PNGs with their part references, skipping any not yet rendered."""
    requests: List[PageRequest] = []
    for ref in refs:
        image_path = pages_dir / f"{ref.key}.png"
        if image_path.exists():
            requests.append(PageRequest(ref=ref, image_bytes=image_path.read_bytes()))
    return requests
