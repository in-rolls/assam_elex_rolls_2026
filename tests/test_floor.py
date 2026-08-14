"""Each detector must be able to condemn a row and to clear one.

A check nobody can make fail is decoration. This project shipped one for weeks: ``serial_no`` is
assigned by a counter and is therefore 1..N by construction, so checking it for gaps reported zero
forever and felt like assurance. Every detector here is given an input that must trip it and an
input that must not.
"""

import pytest

from electors import floor


def rows(**overrides):
    """One well-formed row, with the fields under test overridable."""
    base = {
        "name": "ৰমেন দাস",
        "relation_name": "হৰেন দাস",
        "age": 45,
        "sex": "M",
        "house_no": "12",
        "epic_no": "ABC1234567",
    }
    base.update(overrides)
    return [base]


CONDEMNS = [
    (floor.latin_in_name, {"name": "PROKAS RAI"}),
    (floor.latin_in_name, {"name": "ৰমেন Das"}),
    (floor.name_is_relation, {"name": "ৰমেন দাস", "relation_name": "ৰমেন দাস"}),
    (floor.label_in_value, {"name": "নাম ৰমেন দাস"}),
    (floor.label_in_value, {"relation_name": "হৰেন দাস বয়স"}),
    (floor.malformed_epic, {"epic_no": "AB1234567"}),
    (floor.malformed_epic, {"epic_no": "ABC123456"}),
    (floor.malformed_epic, {"epic_no": "1414140001471"}),
    (floor.impossible_age, {"age": 7}),
    (floor.impossible_age, {"age": 250}),
]


@pytest.mark.parametrize("detector,bad", CONDEMNS)
def test_each_detector_can_fire(detector, bad):
    assert detector(rows(**bad)).condemned == 1, f"{detector.__name__} did not fire on {bad}"


@pytest.mark.parametrize("detector,_bad", CONDEMNS)
def test_each_detector_clears_a_good_row(detector, _bad):
    assert detector(rows()).condemned == 0, f"{detector.__name__} fired on a clean row"


def test_repeated_epic_needs_two_rows():
    """Uniqueness is the one detector a single row cannot express."""
    shared = dict(rows()[0])
    other = dict(shared, name="আনোৱাৰ আলী")
    assert floor.repeated_epic([shared, other]).condemned == 2
    assert floor.repeated_epic([shared, dict(other, epic_no="XYZ7654321")]).condemned == 0


def test_repeated_epic_ignores_malformed_ones():
    """A malformed EPIC repeating says nothing about uniqueness -- it is already condemned above.

    Counting it here too would report one defect as two and inflate the bound.
    """
    junk = [dict(rows()[0], epic_no="???"), dict(rows()[0], epic_no="???")]
    assert floor.repeated_epic(junk).condemned == 0


def test_an_empty_field_is_not_condemned():
    """Missing is not wrong. A blank name is visibly absent; the bound is about values that are
    present and provably incorrect, so that removing a bad value is never scored as damage."""
    blank = rows(name="", relation_name="", epic_no="", age=None)
    assert all(f.condemned == 0 for f in floor.scan(blank))


def test_render_names_the_failing_detector():
    text = floor.render(floor.scan(rows(name="PROKAS RAI")))
    assert "latin in name" in text


def test_a_name_containing_a_label_is_not_a_leak():
    """Assamese names contain the label strings. Substring matching condemned real people.

    ``বাইনাম`` contains ``নাম``; ``নংগা`` contains ``নং``; ``বাড়িয়ৰ মুৰ্ম`` contains ``বাড়ি``.
    Three quarters of what the loose test flagged were names. A floor is only a floor if
    everything beneath it is genuinely wrong.
    """
    for name in ("বাইনাম", "নংগা", "বাড়িয়ৰ মুৰ্ম", "লেফিটনংট মূসীদাস"):
        assert floor.label_in_value(rows(relation_name=name)).condemned == 0, name


def test_a_label_at_the_front_is_a_leak():
    """What a failed split actually leaves behind: the label, or the label and its separator."""
    for value in ("ঘৰ নং", "নাম লেকশী নাৰ্জাৰী", "বয়স : লিঙ্গ : মহিলা", "লিঙ্গ : কোচ : পুৰুষ"):
        assert floor.label_in_value(rows(name=value)).condemned == 1, value
