"""Hand-checked romanizations for the tokens that recur across fields.

The 2,817 values decompose into 2,036 distinct native tokens, and they are heavily reused:
``অংশ`` appears in 362 values, ``খণ্ড`` in 165, ``ডিব্ৰুগড়`` in 13. So the efficient unit of
review is the **token**, not the value -- correcting ``ৰাজহ`` once fixes every revenue
circle at a stroke, across all four fields.

The top 200 tokens cover 70% of row-weighted occurrences; the ~120 here cover the bulk of
that, and are the ones a reader meets constantly.

Two kinds of entry, both transliterations:

**Generic suffixes.** ``থানা`` is *thana*, never "Police Station"; ``অংশ`` is *ansh*, never
"Part". These are where translation would creep in, and ``guards`` enforces it.

**Place names**, in their conventional anglicised spelling -- what atlases, censuses and
road signs use. IndicXlit renders Assamese phonetics instead (``xiboxagor`` for Sivasagar,
``zurhat`` for Jorhat, ``dixpur`` for Dispur), which is faithful to pronunciation and
useless for joining.

OCR variants are folded in where the correct reading is unambiguous: ``গুৱাহাচা`` is the
misread ``গুৱাহাটী``, ``ভন্নয়ন``/``ডন্নয়ন`` are ``উন্নয়ন``, ``ডপ`` is ``উপ``. Those are
noted individually below.
"""

from __future__ import annotations

from typing import Dict

#: Generic administrative vocabulary. Transliterated, never translated.
GENERIC: Dict[str, str] = {
    "অংশ": "Ansh",  # "part" -- transliterated deliberately
    "খণ্ড": "Khanda",
    "থানা": "Thana",
    "ৰাজহ": "Rajah",
    "ৰাজস্ব": "Rajah",
    "রাজস্ব": "Rajah",
    "চক্ৰ": "Chakra",
    "চক্র": "Chakra",
    "ডাকঘৰ": "Dakghar",
    "ডাকঘর": "Dakghar",
    "ঘৰ": "Ghar",
    "উপ": "Up",
    "ডপ": "Up",  # OCR variant of উপ
    "সদৰ": "Sadar",
    "সদর": "Sadar",
    "নিগম": "Nigam",
    "পোৰ": "Paur",
    "পৌৰ": "Paur",
    "পোৰসভা": "Paurasabha",
    "পৌৰসভা": "Paurasabha",
    "পৌরসভা": "Paurasabha",
    "উন্নয়ন": "Unnayan",
    "ভন্নয়ন": "Unnayan",  # OCR variant
    "ডন্নয়ন": "Unnayan",  # OCR variant
    "পঞ্চায়ত": "Panchayat",
    "পঞ্চায়েত": "Panchayat",
    "গাঁও": "Gaon",
    "গাওঁ": "Gaon",
    "নগৰ": "Nagar",
    "মহানগৰ": "Mahanagar",
    "চহৰ": "Chahar",
    "জিলা": "Jila",
    "বাজাৰ": "Bazar",
    "বাজার": "Bazar",
    "মহকুমা": "Mahakuma",
}

#: Direction and qualifier words.
QUALIFIERS: Dict[str, str] = {
    "পশ্চিম": "Paschim",
    "পূৱ": "Purba",
    "পূৰ্ব": "Purba",
    "দক্ষিণ": "Dakshin",
    "উত্তৰ": "Uttar",
    "উত্তর": "Uttar",
    "মধ্য": "Madhya",
    "নতুন": "Natun",
}

#: Place names in their conventional anglicised spelling. OCR variants noted.
PLACES: Dict[str, str] = {
    "ডিব্ৰুগড়": "Dibrugarh",
    "নগাঁও": "Nagaon",
    "নগাওঁ": "Nagaon",
    "নগাও": "Nagaon",
    "গোলাঘাট": "Golaghat",
    "বৰপেটা": "Barpeta",
    "কোকৰাঝাৰ": "Kokrajhar",
    "কোকৰাব্মাৰ": "Kokrajhar",  # OCR variant, jha misread
    "কোকৰাব্াৰ": "Kokrajhar",
    "কোকৰাব্বাৰ": "Kokrajhar",
    "কোকৰাবাৰ": "Kokrajhar",
    "ধুবুৰী": "Dhubri",
    "কাছাড়": "Cachar",
    "শিলচর": "Silchar",
    "শিলচৰ": "Silchar",
    "কামৰূপ": "Kamrup",
    "কামৰুপ": "Kamrup",
    "যোৰহাট": "Jorhat",
    "শিৱসাগৰ": "Sivasagar",
    "ওদালগুৰি": "Udalguri",
    "হোজাই": "Hojai",
    "গোৱালপাৰা": "Goalpara",
    "ধেমাজি": "Dhemaji",
    "মানকাচৰ": "Mankachar",
    "তিনচুকিয়া": "Tinsukia",
    "তিনিচুকীয়া": "Tinsukia",
    "লক্ষীমপুৰ": "Lakhimpur",
    "লখিমপুৰ": "Lakhimpur",
    "বিশ্বনাথ": "Biswanath",
    "শোনিতপুৰ": "Sonitpur",
    "শোণিতপুৰ": "Sonitpur",
    "হাইলাকান্দি": "Hailakandi",
    "তামুলপুৰ": "Tamulpur",
    "মৰিগাওঁ": "Morigaon",
    "শ্রীভূমি": "Sribhumi",
    "শ্রাভূমি": "Sribhumi",  # OCR variant
    "নলবাৰী": "Nalbari",
    "আংলং": "Anglong",
    "কাৰবি": "Karbi",
    "কাৰ্বি": "Karbi",
    "শালমাৰা": "Salmara",
    "বঙাইগাওঁ": "Bongaigaon",
    "বঙাইগাও": "Bongaigaon",
    "দৰং": "Darrang",
    "হাজো": "Hajo",
    "দলগাও": "Dalgaon",
    "দলগাঁও": "Dalgaon",
    "ডুমডুমা": "Doomdooma",
    "তেজপুৰ": "Tezpur",
    "মায়ং": "Mayong",
    "দিশপুৰ": "Dispur",
    "সোনাই": "Sonai",
    "লংকা": "Lanka",
    "গুৱাহাটী": "Guwahati",
    "গুৱাহাচা": "Guwahati",  # OCR variant
    "গুৱাহাটি": "Guwahati",
    "বাক্সা": "Baksa",
    "বাস্সা": "Baksa",  # OCR variant
    "চিৰাং": "Chirang",
    "বজালী": "Bajali",
    "চৰাইদেউ": "Charaideo",
    "মাজুলী": "Majuli",
    "করিমগঞ্জ": "Karimganj",
    "কৰিমগঞ্জ": "Karimganj",
    "গোসাইগাও": "Gossaigaon",
    "গোসাইগাঁও": "Gossaigaon",
    "কচুগাও": "Kachugaon",
    "কচুগাঁও": "Kachugaon",
    "ঢেকীয়াজুলি": "Dhekiajuli",
    "ঢ়েকীয়াজুলা": "Dhekiajuli",  # OCR variant
    "বিলাসীপাৰা": "Bilasipara",
    "বিলাসাপাৰা": "Bilasipara",  # OCR variant
    "হাফলং": "Haflong",
    "ৰঙিয়া": "Rangia",
    "মঙলদৈ": "Mangaldoi",
    "মঙলদে": "Mangaldoi",  # OCR variant
    "নাজিৰা": "Nazira",
    "শিৱসাগৰ": "Sivasagar",
    "ডিমা": "Dima",
    "হাছাও": "Hasao",
    "যোৰহাটৰ": "Jorhat",
    "তিতাবৰ": "Titabar",
    "মৰাণ": "Moran",
    "নাহৰকটীয়া": "Naharkatia",
    "চাবুৱা": "Chabua",
    "মাৰ্ঘেৰিটা": "Margherita",
    "ডিগবৈ": "Digboi",
    "শদিয়া": "Sadiya",
    "ধেমাজী": "Dhemaji",
    "গহপুৰ": "Gohpur",
    "বিহপুৰীয়া": "Bihpuria",
    "উত্তৰ লখিমপুৰ": "North Lakhimpur",
    "হাউলী": "Howly",
    "সৰুপেটা": "Sarupeta",
    "পাঠশালা": "Pathsala",
    "ৰহা": "Raha",
    "কলিয়াবৰ": "Kaliabor",
    "ধিং": "Dhing",
    "বটদ্ৰৱা": "Batadrava",
    "লামডিং": "Lumding",
    "হোজাইৰ": "Hojai",
    "ডবকা": "Doboka",
    "কাটিগড়া": "Katigorah",
    "লক্ষীপুৰ": "Lakhipur",
    "সোণাপুৰ": "Sonapur",
    "পলাশবাৰী": "Palasbari",
    "বকো": "Boko",
    "ছয়গাঁও": "Chhaygaon",
    "কমলপুৰ": "Kamalpur",
    "নলবাৰীৰ": "Nalbari",
    "টিহু": "Tihu",
    "বৰমা": "Barama",
    "তামুলপুৰৰ": "Tamulpur",
    "মুছলপুৰ": "Musalpur",
    "সালাকাটি": "Salakati",
    "বিজনী": "Bijni",
    "অভয়াপুৰী": "Abhayapuri",
    "ফকিৰাগ্ৰাম": "Fakiragram",
    "গোসাইগাঁৱ": "Gossaigaon",
}

#: Every hand-checked token, merged. Longest first when applied, so multi-word entries win.
LEXICON: Dict[str, str] = {**GENERIC, **QUALIFIERS, **PLACES}


def lookup(token: str) -> str:
    """The hand-checked romanization of one native token, or ``""``."""
    return LEXICON.get(token, "")
