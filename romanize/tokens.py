"""Hand-checked romanizations for the tokens that recur across fields.

The 2,817 values decompose into 2,036 distinct native tokens, and they are heavily reused:
``অংশ`` appears in 362 values, ``খণ্ড`` in 165, ``ডিব্ৰুগড়`` in 13. So the efficient unit of
review is the **token**, not the value -- correcting ``ৰাজহ`` once fixes every revenue
circle at a stroke, across all four fields.

Three review passes are recorded here, each taking the highest-row-weight tokens still
unchecked. Together they lift hand-checked coverage from 42.5% to 78.0% of row-weight; the
third pass added 101 tokens for 6.2 points, so the curve is flattening and what remains is
a genuine long tail.

Three kinds of entry, all transliterations:

**Generic suffixes.** ``থানা`` is *thana*, never "Police Station"; ``অংশ`` is *ansh*, never
"Part". These are where translation would creep in, and ``guards`` enforces it.

**Borrowed English**, written in Bengali script -- ``টাউন``, ``ৰোড``, ``মিউনিসিপাল``. Writing
these back as *town*, *road*, *municipal* is transliteration: the source did the borrowing.

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
    "গাও": "Gaon",
    "গাওঁ": "Gaon",
    "নগৰ": "Nagar",
    "মহানগৰ": "Mahanagar",
    "চহৰ": "Chahar",
    "জিলা": "Jila",
    "বাজাৰ": "Bazar",
    "বাজার": "Bazar",
    "বজাৰ": "Bazar",
    "মহকুমা": "Mahakuma",
    "নগর": "Nagar",
    "সভা": "Sabha",
    "পৌর": "Paur",
    "মুখ্য": "Mukhya",
    "কেন্দ্ৰীয়": "Kendriya",
    "কেন্দ্ৰায়": "Kendriya",  # OCR variant
    "উজনি": "Ujani",
    "উজনা": "Ujani",  # OCR variant
    "জনজাতি": "Janajati",
    "জনজাত": "Janajati",
    "জন্নয়ন": "Unnayan",  # OCR variant
    "উডন্নয়ন": "Unnayan",  # OCR variant
    "ভডন্নয়ন": "Unnayan",  # OCR variant
    # Borrowed English, written in Bengali script. Rendering these as the English word
    # is transliteration, not translation -- the source already borrowed it.
    "ৰোড": "Road",
    "রোড": "Road",
    "ৰেলৱে": "Railway",
    "টাউন": "Town",
    "মিউনিসিপাল": "Municipal",
    "বোর্ড": "Board",
    "পুলিশ": "Police",
    "পুলিচ": "Police",
}

#: Single Latin letters borrowed into the script, used as part-numbering prefixes:
#: ``পি-1`` is *P-1*, ``(প-২)`` is *(P-2)*. A word transliterator has no way to know these
#: are letters rather than syllables, so it returns ``pee`` and ``poo``.
ABBREVIATIONS: Dict[str, str] = {
    "পি": "P",
    "প": "P",
    "চি": "C",
    "এম": "M",
    "ভি": "V",
    "অং": "Ang",  # clipped অংশ
    "জংচন": "Junction",
}

#: Direction and qualifier words.
QUALIFIERS: Dict[str, str] = {
    "পশ্চিম": "Paschim",
    "পূৱ": "Purba",
    "পুৱ": "Purba",
    "দক্ষিন": "Dakshin",
    "পূৰ্ব": "Purba",
    "দক্ষিণ": "Dakshin",
    "উত্তৰ": "Uttar",
    "উত্তর": "Uttar",
    "ডত্তৰ": "Uttar",  # OCR variant
    "পাশ্চম": "Paschim",  # OCR variant
    "পশ্চম": "Paschim",
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
    "গোসাহগাও": "Gossaigaon",  # OCR variant
    # --- second review pass: the next ~130 tokens by row-weight ---
    # The same ঝ -> ব্ misreading that gave four Kokrajhars also hits Sipajhar.
    "ছিপাঝাৰ": "Sipajhar",
    "ছিপাব্বাৰ": "Sipajhar",  # OCR variant
    "ছিপাব্মাৰ": "Sipajhar",  # OCR variant
    "মোৰাঝাৰ": "Morajhar",
    "মোৰাব্মাৰ": "Morajhar",  # OCR variant
    "লালা": "Lala",
    "লক্ষীপুৰ": "Lakhipur",
    "লক্ষীপুর": "Lakhipur",
    "লক্ষাপুৰ": "Lakhipur",  # OCR variant
    "লক্ষামপুৰ": "Lakhimpur",  # OCR variant
    "গোৰেশ্বৰ": "Goreswar",
    "সৰুপথাৰ": "Sarupathar",
    "বৰপথাৰ": "Barpathar",
    "কাকপথাৰ": "Kakopathar",
    "নলবাৰা": "Nalbari",  # OCR variant
    "গুৱাহাটা": "Guwahati",  # OCR variant
    "চাপৰ": "Chapar",
    "চাপৰি": "Chapori",
    "বোকাখাত": "Bokakhat",
    "ডিফু": "Diphu",
    "রামকৃষ্ণ": "Ramkrishna",
    "জুৰিয়া": "Juria",
    "ঢকুৱাখনা": "Dhakuakhana",
    "গোলাঘাট": "Golaghat",
    "গোলাঘাচ": "Golaghat",  # OCR variant
    "গোলাঘাঢ": "Golaghat",  # OCR variant
    "নিলামবাজার": "Nilambazar",
    "বদরপুর": "Badarpur",
    "পাথারকান্দি": "Patharkandi",
    "চাৰিদুৱাৰ": "Chariduar",
    "ছয়দুৱাৰ": "Chayduar",
    "বৰদুৱাৰ": "Barduar",
    "যোৰহাচ": "Jorhat",  # OCR variant
    "যোৰহাঢ": "Jorhat",  # OCR variant
    "কামপুৰ": "Kampur",
    "কলগাছিয়া": "Kalgachia",
    "চিচিবৰগাও": "Sissiborgaon",
    "গোলকগঞ্জ": "Golakganj",
    "গোলগঞ্জ": "Golakganj",  # OCR variant
    "ফকিৰগঞ্জ": "Fakirganj",
    "চাৰিআলি": "Chariali",
    "জালাহ": "Jalah",
    "বোকাজান": "Bokajan",
    "দেৰগাও": "Dergaon",
    "দেৰগাওঁ": "Dergaon",
    "ৰাঙাপাৰা": "Rangapara",
    "দোতমা": "Dotma",
    "কালাইন": "Kalain",
    "ধুবুৰা": "Dhubri",  # OCR variant
    "চিদলা": "Sidli",
    "চৰাং": "Chirang",
    "মাকুম": "Makum",
    "বাঘবৰ": "Baghbor",
    "মাজুলা": "Majuli",  # OCR variant
    "চতিয়া": "Chatia",
    "ঢেকীয়াজুলী": "Dhekiajuli",
    "লালুক": "Laluk",
    "বৰবৰুৱা": "Barbaruah",
    "খোৱাং": "Khowang",
    "কালয়াবৰ": "Kaliabor",
    "বজালা": "Bajali",  # OCR variant
    "সোনাপুৰ": "Sonapur",
    "কাটলিছড়া": "Katlicherra",
    "বাজারীছড়া": "Bazaricherra",
    "ঘগ্ৰাপাৰ": "Ghograpar",
    "লাহৰিঘাট": "Laharighat",
    "লাহাৰঘাচ": "Laharighat",  # OCR variant
    "উধারবন্দ": "Udharbond",
    "উদারবন্দ": "Udharbond",
    "ডিমৌ": "Demow",
    "ডিমো": "Demow",  # OCR variant
    "লাহোৱাল": "Lahowal",
    "চিলাপথাৰ": "Silapathar",
    "নদুৱাৰ": "Naduar",
    "বিজনা": "Bijni",  # OCR variant
    "বজনা": "Bijni",  # OCR variant
    "টিংখং": "Tingkhong",
    "ঢিংখং": "Tingkhong",  # OCR variant
    "সাপেখাতা": "Sapekhati",
    "জোনাই": "Jonai",
    "মিকিৰভেটা": "Mikirbheta",
    "বচদ্ৰৱা": "Batadrava",  # OCR variant
    "চেঙা": "Chenga",
    "মন্দিয়া": "Mandia",
    "বড়োবজাৰ": "Bodobazar",
    "কলাইগাও": "Kalaigaon",
    "কলাইগাঁও": "Kalaigaon",
    "কলাহগাও": "Kalaigaon",  # OCR variant
    "টেঙাখাত": "Tengakhat",
    "ঢেঙাখাত": "Tengakhat",  # OCR variant
    "মৰান": "Moran",
    "আলগাপুর": "Algapur",
    "বালিজানা": "Balijana",
    "হেলেম": "Helem",
    "ছয়গাও": "Chhaygaon",
    "ছয়গাওঁ": "Chhaygaon",
    "নাৰায়ণপুৰ": "Narayanpur",
    "সোণাৰি": "Sonari",
    "সোণাৰ": "Sonari",  # OCR variant
    "মানিকপুৰ": "Manikpur",
    "আগমনি": "Agomani",
    "আগমান": "Agomani",  # OCR variant
    "মুকালমুৱা": "Mukalmua",
    "হাওৰাঘাচ": "Howraghat",  # OCR variant
    "পলাশবাৰা": "Palasbari",  # OCR variant
    "বালিপৰা": "Balipara",
    "বড়খলা": "Borkhola",
    "গোগামুখ": "Gogamukh",
    "মাহমৰা": "Mahmara",
    "ৰংজুলি": "Rongjuli",
    "চামগুৰি": "Samaguri",
    "গৌৰীপুৰ": "Gauripur",
    "নগৰবেৰা": "Nagarbera",
    "সৃজনগ্ৰাম": "Srijangram",
    "সৃজনপ্ৰাম": "Srijangram",  # OCR variant
    "সৃজন্্ৰাম": "Srijangram",  # OCR variant
    "বঙাহগাও": "Bongaigaon",  # OCR variant
    "সৰ্থেবাৰী": "Sarthebari",
    "সথেবাৰা": "Sarthebari",  # OCR variant
    "অভয়াপুৰা": "Abhayapuri",  # OCR variant
    "সৰভোগ": "Sarbhog",
    "মাজবাট": "Mazbat",
    "বিন্নাকান্দ": "Binnakandi",
    "ডকমকা": "Dokmoka",
    "কাঠয়াতলা": "Kathiatoli",
    "ধলাই": "Dholai",
    "জলেশ্বৰ": "Jaleswar",
    "ঘিলামৰা": "Ghilamara",
    "জয়পুৰ": "Joypur",
    "ভৱানাপুৰ": "Bhawanipur",  # OCR variant
    "ধুলা": "Dhula",
    "ৰূপহী": "Rupahi",
    "ৰূপহা": "Rupahi",  # OCR variant
    "ৰূপহীহাট": "Rupohihat",
    "ৰূপহাহাচ": "Rupohihat",  # OCR variant
    "বৰনগৰ": "Barnagar",
    "আজাৰা": "Azara",
    "নাহৰকটায়া": "Naharkatia",  # OCR variant
    "বৈঠালাংছু": "Baithalangso",
    "ৰংখাং": "Rongkhang",
    "হোজাহ": "Hojai",  # OCR variant
    "বেলশৰ": "Belsor",
    "টীয়ক": "Teok",
    "টায়ক": "Teok",  # OCR variant
    "ঠেলামৰা": "Thelamara",
    "বেংতল": "Bengtol",
    "ডাংতল": "Dangtol",
    "ডিগবে": "Digboi",  # OCR variant
    "নাওবৈচা": "Naoboicha",
    "নাওবেচা": "Naoboicha",  # OCR variant
    "রাতাবাড়ী": "Ratabari",
    "ৰ্হা": "Raha",  # OCR variant
    "ৰামপুৰ": "Rampur",
    "বিহপুৰায়া": "Bihpuria",  # OCR variant
    "বৰক্ষেত্ৰা": "Barkhetri",  # OCR variant
    "পাটাছাৰকুছি": "Patacharkuchi",
    "গোৱৰ্ধনা": "Gobardhana",
    "গোৱধনা": "Gobardhana",  # OCR variant
    # --- third review pass ---
    "ছিপাব্সাৰ": "Sipajhar",  # OCR variant
    "বৰক্ষেত্ৰী": "Barkhetri",
    "বৰহক্ষেএ": "Barkhetri",  # OCR variant
    "মুৰকংচেলেক": "Murkongselek",
    "মুকংচেলেক": "Murkongselek",  # OCR variant
    "পানীতোলা": "Panitola",
    "পানাতোলা": "Panitola",  # OCR variant
    "ধলপুখুৰা": "Dholpukhuri",
    "রাজাবাজার": "Rajabazar",
    "ৰঙয়া": "Rangia",
    "ৰাঙয়া": "Rangia",  # OCR variant
    "তিনিচুকায়া": "Tinsukia",  # OCR variant
    "তিনচুকায়া": "Tinsukia",  # OCR variant
    "তিন্চুকায়া": "Tinsukia",  # OCR variant
    "যোগীঘোপা": "Jogighopa",
    "মেৰাপানী": "Merapani",
    "মেৰাপানা": "Merapani",  # OCR variant
    "চৈতন্যনগর": "Chaitanyanagar",
    "বৰচলা": "Borsola",
    "কচুৱা": "Kachua",
    "বৰপেটাৰোড": "Barpeta Road",
    "বৰপেচাৰোড": "Barpeta Road",  # OCR variant
    "বাইহাটা": "Baihata",
    "হাওৰাঘাট": "Howraghat",
    "ৰুপহা": "Rupahi",  # OCR variant
    "পথাৰঘাট": "Patharghat",
    "পাথাৰঘাচ": "Patharghat",  # OCR variant
    "হয়বৰগাও": "Hoiborgaon",
    "ভৈরবনগর": "Bhairabnagar",
    "পাকা": "Paka",
    "নাৰায়ানপুৰ": "Narayanpur",  # OCR variant
    "ৰংজুল": "Rongjuli",  # OCR variant
    "মাজবাচ": "Mazbat",  # OCR variant
    "মালেগড়": "Malegarh",
    "মৰিয়নী": "Mariani",
    "ভূৰাগাও": "Bhuragaon",
    "ভূৰাগাওঁ": "Bhuragaon",
    "শালকোচা": "Salkocha",
    "বড়জালেঙ্গা": "Barjalenga",
    "ওডালগুৰি": "Udalguri",  # OCR variant
    "বনভাগ": "Bonbhag",
    "বনগাও": "Bongaon",
    "বনগাওঁ": "Bongaon",
    "নামৰূপ": "Namrup",
    "কঠালগুৰি": "Kathalguri",
    "কণ্ঠালগুৰ": "Kathalguri",  # OCR variant
    "কঠালগুৰ": "Kathalguri",  # OCR variant
    "কঠ্ঠালগুৰ": "Kathalguri",  # OCR variant
    "কঠালণুৰ": "Kathalguri",  # OCR variant
    "কদম": "Kadam",
    "বেঙেনাখোৱা": "Bengenakhowa",
    "জয়পুর": "Joypur",
    "খুমটাই": "Khumtai",
    "বাঘমাৰা": "Baghmara",
    "ওৰাং": "Orang",
    "যমুনামুখ": "Jamunamukh",
    "জখলাবন্ধা": "Jakhalabandha",
    "বালজানা": "Balijana",  # OCR variant
    "পুলিবৰ": "Pulibor",
    "নাওবেছা": "Naoboicha",  # OCR variant
    "শুৱালকুছি": "Sualkuchi",
    "লাওখোৱা": "Laokhowa",
    "চন্দ্ৰপুৰ": "Chandrapur",
    "খেৰণী": "Kheroni",
    "চাৰআল": "Chariali",  # OCR variant
    "মহামায়া": "Mahamaya",
    "যোৰহাঢচ": "Jorhat",  # OCR variant
    "বগীনদী": "Boginodi",
    "ধোয়ারবন্দ": "Dwarband",
    "ভাঙ্গা": "Bhanga",
    "হামৰেণ": "Hamren",
    "গাভৰু": "Gabharu",
    "পানিগাও": "Panigaon",
    "পানিগাওঁ": "Panigaon",
    "চকচকা": "Chakchaka",
    "ভৰলুমুখ": "Bharalumukh",
    "টংলা": "Tangla",
    "বাসুগাও": "Basugaon",
    "বিহালী": "Behali",
    "বিহালা": "Behali",  # OCR variant
    "কমাৰগাওঁ": "Kamargaon",
    "তাৰাবাৰী": "Tarabari",
    "ৰৌতা": "Rowta",
    "ৰোতা": "Rowta",  # OCR variant
    "হাৰাশিঙা": "Harisinga",  # OCR variant
    "দুলায়াজান": "Duliajan",  # OCR variant
    "গোলাঘাচঢ": "Golaghat",  # OCR variant
    "দাক্ষণ": "Dakshin",  # OCR variant
    "পদুমণি": "Padumoni",
    "পদুমণ": "Padumoni",  # OCR variant
    "পদুমাণ": "Padumoni",  # OCR variant
    "কৃষ্ণাই": "Krishnai",
    "কৃষ্ণাহ": "Krishnai",  # OCR variant
    "বেজেৰা": "Bezera",
    "দুধনৈ": "Dudhnoi",
    "হাৰশিঙা": "Harisinga",
    "দুলীয়াজান": "Duliajan",
    "ভেৰগাও": "Bhergaon",
    "ভূৰবন্ধা": "Bhurbandha",
    "কৰুণাবাৰা": "Karunabari",
    "খৈৰাবাৰা": "Khairabari",
    "মৰিগাও": "Morigaon",
    "সুখচৰ": "Sukhchar",
    "বৰভাগ": "Barbhag",
    "নরসিংপুর": "Narsingpur",
    "জাগীৰোড": "Jagiroad",
    "ধমধমা": "Dhamdhama",
    "বইটামাৰা": "Boitamari",
    "জালুকবাৰী": "Jalukbari",
    "ধুপধাৰা": "Dhupdhara",
    "গড়মূৰ": "Garamur",
}

#: Every hand-checked token, merged. Longest first when applied, so multi-word entries win.
LEXICON: Dict[str, str] = {**GENERIC, **ABBREVIATIONS, **QUALIFIERS, **PLACES}


def lookup(token: str) -> str:
    """The hand-checked romanization of one native token, or ``""``."""
    return LEXICON.get(token, "")
