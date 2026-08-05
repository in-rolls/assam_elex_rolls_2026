"""Everything about the form that changes with the language it is printed in.

The Assam 2026 roll is not monolingual. Of the 126 constituencies, 112 are printed in
Assamese, 13 in Bengali (the Barak Valley, ACs 114-126) and one in English (AC 113). The
publisher states which in every filename, so the language of a page is known before it is
read -- it never has to be detected.

The form is the same design in all three -- identical page size, one 1187x1679 image per
page, same generator, same sections in the same order -- but it is **not** pixel-identical
across languages, which was the assumption this module exists to correct. Measured over
the corpus:

* Assamese and Bengali share the same upper rules exactly, but Bengali's lower block sits
  24px further down;
* English rows are taller throughout, putting its upper rules at 133/185/243/295/488/
  540/596 against Assamese's 124/157/191/225/399/433/483.

So each language carries its own **upper** rules here, while ``layout`` locates everything
below section 2 structurally -- those rules move with page content regardless of language.

What else differs by language:

* which Tesseract model to use;
* the printed labels, which the row-alignment fallback matches against;
* where values begin, since label widths differ by language;
* the words that map to the controlled vocabulary (``সাধাৰণ`` / ``GENERAL``).

Vertical rules, measured across all three, agree to within 3px and so need no table.

A ``LanguageProfile`` holds exactly those, and is passed to the parser rather than read
from module state. There is deliberately **no default profile**: a page must say what it
is, because silently falling back to Assamese is precisely how 3,803 Bengali and English
parts would be misread without anyone noticing.

Profiles live as JSON beside this module. The Assamese one was written by hand and is
covered by the parser's tests; the other two are derived by ``assam-rolls calibrate``,
which recovers the printed labels by consensus across sample pages. Keeping them as data
rather than literals means a regenerated profile shows up as a reviewable diff.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Mapping, Tuple

PROFILE_DIR = Path(__file__).parent / "profiles"

#: Language codes as the publisher spells them in filenames.
KNOWN_LANGUAGES = ("ASM", "BEN", "ENG")

#: Tesseract model per language. Bengali and Assamese share a script but not a model:
#: ``ben`` has no ``ৰ``/``ৱ`` prior, and using it on Assamese would systematically
#: substitute the Bengali letters.
TESSERACT_LANG = {"ASM": "asm", "BEN": "ben", "ENG": "eng"}


class UnknownLanguage(KeyError):
    """Raised when a page names a language no profile exists for."""


@dataclass(frozen=True)
class LanguageProfile:
    """How to read the form in one language."""

    code: str
    tesseract_lang: str

    #: The upper horizontal rules that verify a page is this language's template. Only
    #: the upper ones are language-specific: everything below section 2 moves with page
    #: content, so ``layout`` locates it structurally instead. Assamese and Bengali happen
    #: to share these; English does not, its rows being taller.
    stable_h_rules: Tuple[int, ...]

    #: The locality rows this edition prints, in order, as schema field names. The core
    #: eight are identical in every language; the Bengali edition inserts
    #: ``gram_panchayat`` after the police station and the English one adds
    #: ``subdivision`` after the revenue circle. Everything else lines up field for field.
    locality_fields: Tuple[str, ...]

    #: The locality and revision labels, in the same order the form prints them. Used by
    #: the row-alignment fallback and by calibration.
    locality_labels: Tuple[str, ...]
    revision_labels: Tuple[str, ...]
    address_label: str

    #: X offset where values begin, used when a row has no gap wide enough to locate.
    locality_value_x: int
    revision_value_x: int

    reservation_map: Mapping[str, str] = field(default_factory=dict)
    ps_type_map: Mapping[str, str] = field(default_factory=dict)

    #: Provenance for a derived profile: how many pages were sampled and the weakest
    #: per-row agreement among them. ``None`` for a hand-written profile.
    derived_from_pages: int = 0
    min_label_agreement: float = 1.0

    def __post_init__(self) -> None:
        if len(self.stable_h_rules) < 4:
            raise ValueError(f"{self.code}: too few stable rules to identify the template")
        if len(self.locality_labels) != len(self.locality_fields):
            raise ValueError(
                f"{self.code}: {len(self.locality_labels)} labels for "
                f"{len(self.locality_fields)} fields -- they must correspond row for row"
            )
        if len(self.revision_labels) != 4:
            raise ValueError(f"{self.code}: expected 4 revision labels")

    @property
    def is_derived(self) -> bool:
        return self.derived_from_pages > 0

    def to_dict(self) -> Dict:
        return {
            "code": self.code,
            "tesseract_lang": self.tesseract_lang,
            "stable_h_rules": list(self.stable_h_rules),
            "locality_fields": list(self.locality_fields),
            "locality_labels": list(self.locality_labels),
            "revision_labels": list(self.revision_labels),
            "address_label": self.address_label,
            "locality_value_x": self.locality_value_x,
            "revision_value_x": self.revision_value_x,
            "reservation_map": dict(self.reservation_map),
            "ps_type_map": dict(self.ps_type_map),
            "derived_from_pages": self.derived_from_pages,
            "min_label_agreement": self.min_label_agreement,
        }


def from_dict(payload: Mapping) -> LanguageProfile:
    return LanguageProfile(
        code=payload["code"],
        tesseract_lang=payload["tesseract_lang"],
        stable_h_rules=tuple(int(v) for v in payload["stable_h_rules"]),
        locality_fields=tuple(payload["locality_fields"]),
        locality_labels=tuple(payload["locality_labels"]),
        revision_labels=tuple(payload["revision_labels"]),
        address_label=payload["address_label"],
        locality_value_x=int(payload["locality_value_x"]),
        revision_value_x=int(payload["revision_value_x"]),
        reservation_map=dict(payload.get("reservation_map", {})),
        ps_type_map=dict(payload.get("ps_type_map", {})),
        derived_from_pages=int(payload.get("derived_from_pages", 0)),
        min_label_agreement=float(payload.get("min_label_agreement", 1.0)),
    )


def profile_path(code: str) -> Path:
    return PROFILE_DIR / f"{code.upper()}.json"


def write_profile(profile: LanguageProfile) -> Path:
    """Persist a profile as JSON. Used by ``calibrate``."""
    path = profile_path(profile.code)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(profile.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


_cache: Dict[str, LanguageProfile] = {}


def profile_for(code: str) -> LanguageProfile:
    """The profile for a language code, or raise.

    Raising rather than defaulting is the point: a missing Bengali profile must stop the
    run, not quietly hand Bengali pages to the Assamese model.
    """
    key = (code or "").upper()
    if key in _cache:
        return _cache[key]
    path = profile_path(key)
    if not path.exists():
        available = sorted(p.stem for p in PROFILE_DIR.glob("*.json"))
        raise UnknownLanguage(
            f"no profile for language {code!r} (have: {available or 'none'}); "
            f"run `assam-rolls calibrate --lang {key}` to derive one"
        )
    profile = from_dict(json.loads(path.read_text(encoding="utf-8")))
    _cache[key] = profile
    return profile


def available() -> Tuple[str, ...]:
    return tuple(sorted(p.stem for p in PROFILE_DIR.glob("*.json")))


def clear_cache() -> None:
    """Forget loaded profiles, so a freshly calibrated one is picked up."""
    _cache.clear()
