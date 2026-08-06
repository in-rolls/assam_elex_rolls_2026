"""Aksharamukha: rule-based script conversion, free and instant.

Treats **Assamese as its own script**, so ``ৰ`` and ``ৱ`` are handled as Assamese letters
rather than mapped through Bengali ``র``/``ব`` -- which matters here, since the corpus is
mostly Assamese and the two scripts differ in exactly those letters.

It is the floor, not the answer. Measured on twelve well-known districts:

    RomanColloquial   2/12
    RomanReadable     0/12
    ISO / IAST        0/12
    (IndicXlit        5/12)

The reason is structural: rules keep the inherent vowel, giving ``kokarajhara`` and
``yorahata`` where conventional English drops it (*Kokrajhar*, *Jorhat*). Aksharamukha's
schwa-deletion options do not fix it -- ``RemoveFinalSchwa`` is a no-op on these strings and
``RemoveSchwa`` raises. So this backend is kept for comparison and as a deterministic
fallback, and is not expected to win.
"""

from __future__ import annotations

import warnings
from typing import Dict, Iterable, List

#: Aksharamukha's source-script names, by the corpus's language code.
SOURCE_SCRIPT = {"ASM": "Assamese", "BEN": "Bengali", "ENG": None}

#: Scheme aimed at readability rather than scholarly notation -- the best of a weak set.
DEFAULT_SCHEME = "RomanColloquial"

NAME = "aksharamukha"


class AksharamukhaBackend:
    """Romanize with Aksharamukha, one string at a time."""

    def __init__(self, scheme: str = DEFAULT_SCHEME) -> None:
        self.scheme = scheme
        self.name = f"{NAME}:{scheme}"

    def _process(self, source: str, text: str) -> str:
        # The package emits a wall of SyntaxWarnings from its own regexes on import and
        # use; they are its problem, not the caller's, and would drown real output.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from aksharamukha import transliterate

            return transliterate.process(source, self.scheme, text)

    def romanize(self, text: str, lang: str = "ASM") -> str:
        source = SOURCE_SCRIPT.get(lang)
        if source is None:
            return text  # already Latin
        try:
            return self._process(source, text).strip()
        except Exception:
            return ""

    def romanize_many(self, items: Iterable[tuple]) -> Dict[str, str]:
        """``items`` is an iterable of ``(text, lang)``. Returns ``{text: roman}``."""
        out: Dict[str, str] = {}
        for text, lang in items:
            out[text] = self.romanize(text, lang)
        return out


def available() -> bool:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            import aksharamukha  # noqa: F401
        return True
    except ImportError:
        return False


def schemes() -> List[str]:
    """The schemes worth comparing in the audit, best first."""
    return ["RomanColloquial", "RomanReadable", "IAST", "ISO"]
