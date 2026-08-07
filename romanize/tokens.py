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

#: Polling-station and habitation vocabulary -- the tier-2 fields (``ps_name``,
#: ``main_town_village``). These 43,827 values look unbounded but are not: a station name is
#: almost always a place name plus a school, and the school half is a closed vocabulary.
#: ``স্কুল`` alone occurs in 19,600 rows.
#:
#: The Indic words stay Indic: ``বিদ্যালয়`` is *Vidyalaya*, not "School" -- the source
#: distinguishes the two and so must the table. Only words the source itself borrowed from
#: English come back as English.
INSTITUTIONS: Dict[str, str] = {
    "বিদ্যালয়": "Vidyalaya",
    "মহাবিদ্যালয়": "Mahavidyalaya",
    "বিদ্যাপীঠ": "Vidyapith",
    "বিদ্যা": "Vidya",
    "নিকেতন": "Niketan",
    "প্ৰাথমিক": "Prathamik",
    "প্রাথমিক": "Prathamik",
    "প্ৰাঃ": "Pra.",
    "মাধ্যমিক": "Madhyamik",
    "মাঃ": "Ma.",
    "উচ্চতৰ": "Uchchatar",
    "উচ্চ": "Uchcha",
    "উঃ": "U.",
    "বিঃ": "Bi.",
    "আঞ্চলিক": "Anchalik",
    "বুনিয়াদী": "Buniyadi",
    "মজলীয়া": "Majlia",
    "শাখা": "Shakha",
    "বালিকা": "Balika",
    "বালক": "Balak",
    "ছোৱালী": "Sowali",
    "শিশু": "Shishu",
    "কেন্দ্ৰ": "Kendra",
    "আদৰ্শ": "Adarsha",
    "চৰকাৰী": "Sarkari",
    "সরকারী": "Sarkari",
    "ইংৰাজী": "Ingraji",
    "অসমীয়া": "Asomiya",
    "হিন্দী": "Hindi",
    "হিন্দি": "Hindi",
    "বঙালী": "Bengali",
    "বেঙ্গলী": "Bengali",
    "নেপালী": "Nepali",
    "মুক্তাব": "Maktab",
    "মুক্তাৱ": "Maktab",
    "মোক্তাব": "Maktab",
    "মাদ্ৰাছা": "Madrasa",
    "মন্দিৰ": "Mandir",
    "সত্ৰ": "Satra",
    "কাৰ্য্যালয়": "Karyalaya",
    "আৰক্ষী": "Arakshi",
    "জনতা": "Janata",
    # Directional halves of a split station: সোঁ is right, বাওঁ is left, ফাল is side.
    "সোঁ": "Son",
    "সো": "Son",
    "সোঁফাল": "Sonphal",
    "সোফাল": "Sonphal",
    "সোওঁফাল": "Sonphal",
    "সোঁশাখা": "Sonshakha",
    "বাওঁ": "Baon",
    "বাঁও": "Baon",
    "বাওঁফাল": "Baonphal",
    "বাঁওফাল": "Baonphal",
    "বাওফাল": "Baonphal",
    "বাওঁশাখা": "Baonshakha",
    "ফাল": "Phal",
    "মধ্যফাল": "Madhyaphal",
    "মধ্যাংশ": "Madhyansh",
    "মধ্যশাখা": "Madhyashakha",
    "উত্তৰফাল": "Uttarphal",
    "পশ্চিমফাল": "Paschimphal",
    "পূব": "Pub",
    "পুব": "Pub",
    "ওপৰ": "Upar",
    "মাজ": "Maj",
    "মাজত": "Majat",
    "নিজ": "Nij",
    "সৰু": "Saru",
    "বৰ": "Bar",
    "আৰু": "Aru",
    # Tea-garden and habitation vocabulary.
    "চাহ": "Chah",
    "চা": "Cha",
    "বাগান": "Bagan",
    "বাগিছা": "Bagicha",
    "বাগিচা": "Bagicha",
    "চাহবাগিচা": "Chah Bagicha",
    "পথাৰ": "Pathar",
    "পাথাৰ": "Pathar",
    "পুখুৰী": "Pukhuri",
    "বস্তি": "Basti",
    "চুবুৰী": "Suburi",
    "পাৰা": "Para",
    "চুক": "Suk",
    "চৰ": "Char",
    "চাপৰী": "Chapori",
    "বিল": "Bil",
    "হাবি": "Habi",
    "পাম": "Pam",
    "খন্ড": "Khanda",  # spelling variant of খণ্ড
    # Communities and surnames that recur in station names.
    "বড়ো": "Bodo",
    "মিছিং": "Mising",
    "মিকিৰ": "Mikir",
    "মিৰি": "Miri",
    "দেউৰী": "Deuri",
    "কছাৰী": "Kachari",
    "তেৰাং": "Terang",
    "বৰুৱা": "Baruah",
    "শইকীয়া": "Saikia",
    "গোঁহাই": "Gohain",
    "গোহাঁই": "Gohain",
    "বৰদলৈ": "Bordoloi",
    "কোঁৱৰ": "Konwar",
    "দাস": "Das",
    "কুমাৰ": "Kumar",
    "হাজী": "Haji",
    "আব্দুল": "Abdul",
    "আলী": "Ali",
    "আলি": "Ali",
    "শ্ৰী": "Sri",
    "শ্বহীদ": "Shahid",
    "মিলন": "Milan",
    "নৱজ্যোতি": "Nabajyoti",
    "শংকৰদেৱ": "Sankardev",
    "বিবেকানন্দ": "Vivekananda",
    "নেতাজী": "Netaji",
    "বাপুজী": "Bapuji",
    "গান্ধী": "Gandhi",
    "নেহেৰু": "Nehru",
    "অসম": "Asom",
    # Borrowed English again -- these are the source's own loanwords, so they come back
    # as English. See the note on ``GENERIC``.
    "স্কুল": "School",
    "হাই": "High",
    "হাইস্কুল": "High School",
    "হায়াৰ": "Higher",
    "হাইয়াৰ": "Higher",
    "হাইয়ার": "Higher",
    "চেকেণ্ডাৰী": "Secondary",
    "ছেকেণ্ডাৰী": "Secondary",
    "চেকেণ্ডেৰী": "Secondary",
    "সেকেগ্ডারী": "Secondary",  # OCR variant
    "প্ৰাইমেৰী": "Primary",
    "প্রাইমারী": "Primary",
    "বেচিক": "Basic",
    "বেছিক": "Basic",
    "বেসিক": "Basic",
    "জুনিয়ৰ": "Junior",
    "চিনিয়ৰ": "Senior",
    "কলেজ": "College",
    "একাডেমী": "Academy",
    "মডেল": "Model",
    "পাব্লিক": "Public",
    "গার্লস": "Girls",
    "মেমোৰিয়াল": "Memorial",
    "মেমোৰিয়েল": "Memorial",
    "রুম": "Room",
    "ৰুম": "Room",
    "ৰূম": "Room",
    "রোম": "Room",
    "কোঠা": "Kotha",
    "ব্লক": "Block",
    "ক্লাব": "Club",
    "ক্লাৱ": "Club",
    "লেবাৰ": "Labour",
    "মজদুৰ": "Majdur",
    "ষ্টাফ": "Staff",
    "বিল্ডিং": "Building",
    "নিউ": "New",
    "লাইন": "Line",
    "কলনি": "Colony",
    "ভিলেজ": "Village",
    "ফৰেষ্ট": "Forest",
    "ফরেষ্ট": "Forest",
    "ৰিজাৰ্ভ": "Reserve",
    "পাৰ্ট": "Part",
    "ৱাৰ্ড": "Ward",
    "বাৰ্ড": "Ward",  # OCR variant
    "বোৰ্ড": "Board",
    "মিউনিসিপ্যালিটি": "Municipality",
    "মিউনিচিপালিটি": "Municipality",
    "এলপি": "LP",
    "পাবলিক": "Public",
    "ইংলিছ": "English",
    "মেমৰিয়েল": "Memorial",
    "গ্রান্ট": "Grant",
    "গ্ৰান্ট": "Grant",
    "গ্ৰাণ্ট": "Grant",
    "গ্রাণ্ট": "Grant",
    "জুট": "Jute",
    "বস্তী": "Basti",
    "বাও": "Baon",
    "খৃষ্টান": "Khristan",
    "কল্যাণ": "Kalyan",
    "নদী": "Nodi",
    "ক্ষুদ্ৰ": "Kshudra",
    "পুৰ": "Pur",
    "বাৰী": "Bari",
    "পাৰ": "Par",
    "চৌধুৰী": "Choudhury",
    "বাহাদুৰ": "Bahadur",
    "গোপীনাথ": "Gopinath",
    "মাধ্য": "Madhya",
    "বিদ্য": "Vidya",
    "লয়": "laya",
    # নিয় / নিয়ন are printed this way in the source. Transliterated literally rather
    # than repaired to a guess about the intended word.
    "নিয়": "Niya",
    "নিয়ন": "Niyan",
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
    "এ": "A",
    "বি": "B",
    "ডি": "D",
    "ই": "E",
    "এফ": "F",
    "জি": "G",
    "এইচ": "H",
    "জে": "J",
    "কে": "K",
    "এল": "L",
    "এন": "N",
    "আৰ": "R",
    "এস": "S",
    "এছ": "S",
    "টি": "T",
    "ক": "Ka",  # Bengali letters used as enumerators: ব্লক-ক is Block-Ka
    "খ": "Kha",
    #: ``নং`` abbreviates ``নম্বৰ``, itself the English "number" borrowed into Assamese,
    #: so "No." is the loanword coming home rather than a translation.
    "নং": "No.",
    "সি": "C",
    "এচ": "S",
    "আর": "R",
    "ল": "L",
    "আই": "I",
    "ন": "Na",
    "দ": "Da",
    "এমই": "ME",
    #: Ordinal suffixes on a Western numeral: ``১০ম`` is *10ma*, ``২য়`` *2ya*, ``৪ৰ্থ``
    #: *4rtha*. A word transliterator reads them as syllables and returns "10maha", "2yoy".
    "ম": "ma",
    "য়": "ya",
    "ৰ্থ": "rtha",
    "ষ্ঠ": "shtha",
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
    # --- tier 2: habitations that recur across station names ---
    "শান্তিপুৰ": "Santipur",
    "বিষ্ণুপুৰ": "Bishnupur",
    "উলুবাৰী": "Ulubari",
    "জামুগুৰি": "Jamuguri",
    "আমগুৰি": "Amguri",
    "শিমলুগুৰি": "Simaluguri",
    "ৰতনপুৰ": "Ratanpur",
    "গোবিন্দপুৰ": "Gobindapur",
    "উদয়পুৰ": "Udaipur",
    "রংপুর": "Rangpur",
    "মোহনপুর": "Mohanpur",
    "তারাপুর": "Tarapur",
    "ইছলামপুৰ": "Islampur",
    "বালিজান": "Balijan",
    "ৰঙাজান": "Rangajan",
    "মাজগাওঁ": "Majgaon",
    "বালিগাওঁ": "Baligaon",
    "বৰগাওঁ": "Bargaon",
    "নমাটি": "Namati",
    "বৰপুখুৰী": "Barpukhuri",
    "ৰাজগড়": "Rajgarh",
    "মৰিগাঁও": "Morigaon",
    "বঙাইগাঁও": "Bongaigaon",
    "ৰৌমাৰী": "Roumari",
    "বৰবিল": "Barbil",
    "আলগা": "Alga",
    "পানবাৰী": "Panbari",
    "আমবাৰী": "Ambari",
    "শিয়ালমাৰী": "Sialmari",
    "বৰবাম": "Barbam",
    "শিঙিমাৰী": "Singimari",
    "কাকী": "Kaki",
    "চামৰালী": "Chamrali",
    "নাহৰবাৰী": "Naharbari",
    "মোৱামাৰী": "Mowamari",
    "সোনাৰী": "Sonari",
    "উদমাৰী": "Udmari",
    "বৰহোলা": "Barhola",
    "চিৰাখোৱা": "Chirakhowa",
    "উদালী": "Udali",
    "হাতীগড়": "Hatigarh",
    "বাশবাৰী": "Bashbari",
    "বাহবাৰী": "Bahbari",
    "কলবাৰী": "Kalbari",
    "কেন্দুগুৰি": "Kenduguri",
    "শালপাৰা": "Salpara",
    "শলমাৰী": "Solmari",
    "খাটোৱাল": "Khatowal",
    "কমাৰ": "Kamar",
    "টিংৰাই": "Tingrai",
    "কৈমাৰী": "Koimari",
    "আটি": "Ati",
    "দলনি": "Doloni",
    "দুধপাতিল": "Dudhpatil",
    "ৰজাবাৰী": "Rajabari",
    "কোঠালী": "Kothali",
    "চেতিয়া": "Setia",
    "পতিয়া": "Potia",
    "ঘোৰামাৰা": "Ghoramara",
    "কদমতলা": "Kadamtala",
    "কাহিবাৰী": "Kahibari",
    "মধুপুৰ": "Madhupur",
    "শিমলাবাৰী": "Simlabari",
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
LEXICON: Dict[str, str] = {**GENERIC, **INSTITUTIONS, **ABBREVIATIONS, **QUALIFIERS, **PLACES}


def lookup(token: str) -> str:
    """The hand-checked romanization of one native token, or ``""``."""
    return LEXICON.get(token, "")
