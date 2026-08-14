"""AC113 is printed in English, and the reader was built for one script.

Its 161,750 rows shipped with name, relation, age, sex and house number blank on every one --
EPIC alone survived, because that is read from its own cell by a Latin-alphabet engine. These
tests pin the English path and, just as importantly, pin the two Assamese-script paths beside it,
because the labels now live in shared patterns.
"""

import pytest

from electors import fields, vision

ENGLISH = [
    "Name : PROKAS RAI",
    "Fathers Name : RAJ BAHADUR RAI",
    "House No. 1",
    "Age 45 Gender : Male",
]
ASSAMESE = [
    "নাম : ৰমেন দাস",
    "পিতাৰ নাম : হৰেন দাস",
    "ঘৰ নং : 12",
    "বয়স : 45 লিঙ্গ : পুৰুষ",
]
BENGALI = [
    "নাম : বিটন ভূমিজ",
    "পিতার নাম : লাল বাবু ভুমিজ",
    "বাড়ী নং : 01",
    "বয়স : 34 লিঙ্গ : পুরুষ",
]


def test_an_english_box_yields_every_field():
    e = vision.elector_from(["FXW1756134"], ENGLISH)
    assert e.name == "PROKAS RAI"
    assert e.relation_name == "RAJ BAHADUR RAI"
    assert e.relation_type == "father"
    assert (e.age, e.sex, e.house_no) == (45, "M", "1")


@pytest.mark.parametrize(
    "lines,name,kind",
    [
        (ASSAMESE, "ৰমেন দাস", "father"),
        (BENGALI, "বিটন ভূমিজ", "father"),
    ],
)
def test_the_assamese_script_paths_are_untouched(lines, name, kind):
    """Adding English to the shared patterns must not disturb the editions that already worked."""
    e = vision.elector_from(["ABC1234567"], lines)
    assert e.name == name
    assert e.relation_type == kind
    assert e.age is not None and e.sex == "M" and e.house_no


@pytest.mark.parametrize(
    "printed,expected",
    [
        ("Male", "M"),
        ("Female", "F"),
        ("Other", "T"),
        ("Third", "T"),
    ],
)
def test_female_is_not_read_as_male(printed, expected):
    """``MALE`` is a substring of ``FEMALE``.

    Listed first it matched every woman on the roll as a man -- silently, since both values are
    valid. The Assamese pair has no such overlap, so the hazard arrived with English.
    """
    lines = ["Name : A B", "Fathers Name : C D", "House No. 3", f"Age 30 Gender : {printed}"]
    assert vision.elector_from(["ABC1234567"], lines).sex == expected


@pytest.mark.parametrize(
    "relation,kind",
    [
        ("Fathers", "father"),
        ("Mothers", "mother"),
        ("Husbands", "husband"),
    ],
)
def test_english_relations_are_typed(relation, kind):
    lines = ["Name : A B", f"{relation} Name : C D", "House No. 3", "Age 30 Gender : Male"]
    assert vision.elector_from(["ABC1234567"], lines).relation_type == kind


def test_a_latin_name_survives_the_foreign_stripper():
    """``strip_foreign`` removes Latin as debris, which empties an English name outright.

    A value with no Assamese in it is not an Assamese name carrying debris; it is Latin text.
    """
    assert fields.strip_foreign("PROKAS RAI")[0] == "PROKAS RAI"
    # and the original behaviour, where there is something for the Latin to be debris of
    assert fields.strip_foreign("ৰমেন দাস 12")[0] == "ৰমেন দাস"
