"""IndicXlit (AI4Bharat) -- the best free option measured, at 5/12 on known districts.

Runs in an **isolated Python 3.10 environment**, because ``ai4bharat-transliteration``
needs ``fairseq`` and an old TensorFlow/torch stack that will not coexist with this
project's Python 3.12. The recipe is lifted from ``indicate/training/baseline_indicxlit.py``
rather than reinvented.

The whole vocabulary goes through **one** subprocess invocation. Model load is ~10 seconds
and per-word inference is milliseconds, so a process per word would spend all its time
loading and none transliterating.

Quality, measured on twelve well-known Assam districts (top-1 exact):

    guwahati, udalguri, hailakandi, dhemaji, bongaon            correct
    dibrugorh (dibrugarh at rank 2)                             near
    kukorajhar, zurhat, kasar, xunitpur, nogaon, tinisukiya     wrong

The misses are systematic, not noise: IndicXlit renders Assamese **phonetics** -- ``xunitpur``
because Assamese শ is /x/, ``zurhat`` because যো is /z/ -- where conventional English
spellings are older anglicisations. It is not undertrained; it is optimising a different
target. That is why its output seeds a reviewable table instead of shipping directly.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

NAME = "indicxlit"

#: IndicXlit language codes, by the corpus's language code.
XLIT_LANG = {"ASM": "as", "BEN": "bn", "ENG": None}

#: The dependency set that actually resolves. Pinned because the loose versions pull a
#: torch/TF combination that fails to import.
UV_ARGS = [
    "uv",
    "run",
    "--python",
    "3.10",
    "--no-project",
    "--with",
    "ai4bharat-transliteration",
    "--with",
    "gevent==24.11.1",
    "--with",
    "tensorflow==2.15.1",
    "--with",
    "keras==2.15.0",
    "--with",
    "tensorflow-addons==0.23.0",
    "--with",
    "torch==2.2.2",
    "python",
]

#: Runs inside the isolated interpreter: read {lang: [words]} as JSON, write {word: [top-k]}.
WORKER = """
import json, sys
from ai4bharat.transliteration import XlitEngine

payload = json.load(open(sys.argv[1], encoding="utf-8"))
topk = int(sys.argv[3])
out = {}
for lang, words in payload.items():
    engine = XlitEngine(lang, beam_width=4, src_script_type="indic")
    for word in words:
        try:
            out[word] = engine.translit_word(word, lang, topk=topk)
        except Exception as exc:
            out[word] = {"_error": f"{type(exc).__name__}: {exc}"}
json.dump(out, open(sys.argv[2], "w", encoding="utf-8"), ensure_ascii=False)
"""


class IndicXlitBackend:
    """Batch-romanize through the isolated environment."""

    name = NAME

    def __init__(self, topk: int = 3, timeout: int = 3600) -> None:
        self.topk = topk
        self.timeout = timeout

    def romanize_many(self, items: Iterable[Tuple[str, str]]) -> Dict[str, List[str]]:
        """``items`` is ``(text, lang)``. Returns ``{text: [candidates]}``.

        Multi-word values are transliterated word by word and rejoined, which is how
        IndicXlit is meant to be used -- it is a *word* transliterator, and a phrase fed
        in whole comes back mangled.
        """
        by_lang: Dict[str, set] = {}
        for text, lang in items:
            code = XLIT_LANG.get(lang)
            if code is None:
                continue
            for token in text.split():
                if token.strip():
                    by_lang.setdefault(code, set()).add(token)
        if not by_lang:
            return {}

        payload = {lang: sorted(words) for lang, words in by_lang.items()}
        with tempfile.TemporaryDirectory() as tmp:
            src, dst, script = (Path(tmp) / n for n in ("in.json", "out.json", "worker.py"))
            src.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            script.write_text(WORKER, encoding="utf-8")
            result = subprocess.run(
                [*UV_ARGS, str(script), str(src), str(dst), str(self.topk)],
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            if not dst.exists():
                raise RuntimeError(
                    f"IndicXlit worker produced nothing (rc={result.returncode}): "
                    f"{result.stderr.strip()[-400:]}"
                )
            return json.loads(dst.read_text(encoding="utf-8"))

    @staticmethod
    def join(text: str, words: Dict[str, List[str]], rank: int = 0) -> str:
        """Rebuild a phrase from its per-word candidates, keeping punctuation-only tokens."""
        parts = []
        for token in text.split():
            candidates = words.get(token)
            if isinstance(candidates, dict) or not candidates:
                parts.append(token)  # error or unknown: keep the original
            else:
                parts.append(candidates[min(rank, len(candidates) - 1)])
        return " ".join(parts)


def available() -> bool:
    """Whether ``uv`` is present; the environment itself is built on first use."""
    try:
        subprocess.run(["uv", "--version"], capture_output=True, timeout=30)
        return True
    except (OSError, subprocess.SubprocessError):
        return False
