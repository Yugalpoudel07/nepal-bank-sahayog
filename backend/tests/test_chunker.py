"""Structure-aware chunking (Acts, unified directives, circulars), Preeti/font handling and references."""

from __future__ import annotations

from ingest.chunker import amendment_rows, chunk_document, parse_directive_units, parse_units
from ingest.extract import ExtractedDoc, strip_repeating_lines
from ingest.fonts import MARK, convert_marked, font_family, law_garble_score
from ingest.heuristics import extract_numeric_facts, find_refs
from ingest.manifest_tools import ManifestEntry
from ingest.preeti import looks_like_preeti, preeti_to_unicode

ACT_TEXT = """Demo Act, 2080
Chapter-1
Preliminary
1. Short title: (1) This Act may be called the Demo Act.
(2) It commences immediately.
2. Definitions: In this Act, "tax" means the demo tax.
Chapter-2
Tax
3. Exempt amounts: The following amounts are exempt:
(a) pensions,
(b) gifts.
4. Long section: (1) {long1}
(2) {long2}
(3) {long3}
Schedule-1
(Relating to Section 3)
| Income | Rate |
|---|---|
| Up to 100 | 1% |
| Above 100 | 10% |
"""


def _doc(text: str, pages: int = 1) -> ExtractedDoc:
    parts = text.split("\f") if pages > 1 else [text]
    return ExtractedDoc(doc_id="demo", path="acts/demo.txt", file_sha256="x",
                        pages=[{"page": i + 1, "text": t} for i, t in enumerate(parts)])


def _entry(**kw) -> ManifestEntry:
    base = {"path": "acts/demo.txt", "doc_type": "act", "title": "Demo Act 2080", "language": "en"}
    return ManifestEntry(**(base | kw))


def test_sections_chapters_and_schedule() -> None:
    text = ACT_TEXT.format(long1="a" * 900, long2="b" * 900, long3="c" * 900)
    units = parse_units(_doc(text).pages)
    kinds = [(u.kind, u.section_no) for u in units]
    assert kinds == [("preamble", None), ("section", "1"), ("section", "2"), ("section", "3"),
                     ("section", "4"), ("schedule", "1")]
    s3 = units[3]
    assert s3.section_title == "Exempt amounts" and s3.chapter_no == "2" and s3.chapter_title == "Tax"
    assert "(b) gifts." in s3.text  # clauses stay with their section


def test_long_section_split_with_parent_and_table_kept_whole() -> None:
    text = ACT_TEXT.format(long1="a" * 900, long2="b" * 900, long3="c" * 900)
    chunks, parents = chunk_document(_entry(), _doc(text), max_chars=1500, target=1000)
    s4 = [c for c in chunks if c["parent_id"] == "acts_demo::s4"]
    assert len(s4) == 3 and all(c["chunk_id"].startswith("acts_demo::s4#") for c in s4)
    assert all(c["text"].lstrip().startswith("(") or c["sub"] == 0 for c in s4)
    assert len(parents["acts_demo::s4"]["text"]) > 2700
    sch = next(c for c in chunks if c["unit_kind"] == "schedule")
    assert sch["is_table"] and "| Above 100 | 10% |" in sch["text"]
    header = next(c for c in chunks if c["section_no"] == "3")["header"]
    assert header == "Demo Act 2080 | Chapter 2: Tax | Section 3: Exempt amounts"


def test_nepali_sections_with_devanagari_digits() -> None:
    text = "परिच्छेद–१\nप्रारम्भिक\n१. संक्षिप्त नाम: यो ऐनको नाम ...\n२. परिभाषा: विषय वा प्रसङ्गले ...\n२क. थप दफा: ..."
    units = parse_units(_doc(text).pages)
    assert [u.section_no for u in units if u.kind == "section"] == ["1", "2", "2A"]
    assert units[0].chapter_no == "1" and units[0].chapter_title == "प्रारम्भिक"


UD_TEXT = """एकीकृत निर्देशन, २०८२
विषय–सूची
१. २
इ.प्रा.निर्देशन नं. १/०८२
न्यूनतम पुँजीकोष सम्बन्धी व्यवस्था
१. कायम गर्नु पर्ने न्यूनतम पुँजीकोष
पुँजीकोष कायम गर्नु पर्नेछ ।
२.  पुँजीकोष
पुँजीकोष भन्नाले प्राथमिक पुँजी र पुरक पुँजीको योग सम्झनु पर्दछ ।
इ.प्रा.निर्देशन नं. २/०८२

कर्जा प्रवाह, वर्गीकरण तथा कर्जा नोक्सानी सम्बन्धी व्यवस्था ।
कर्जा सापटको वर्गीकरण
१.
इजाजतपत्रप्राप्त संस्थाले देहायबमोजिम वर्गीकरण गर्नु पर्नेछ ः
(क) सक्रिय कर्जा ः इ.प्रा.निर्देशन नं. १)” बमोजिम ...
२. खराब कर्जा सम्बन्धी थप व्यवस्था
विवरण ।
इ.प्रा.निर्देशन नं. ३/०८२ः शाखा खोल्ने सम्बन्धी व्यवस्था
१. शाखा खोल्न स्वीकृति
विवरण ।
इ.प्रा.निर्देशन नं. १/०८२ सँग सम्बन्धित अनुसूची
अनुसूची नं. १.१
Capital Adequacy Framework
ने.रा. बैंक निर्देशन फा.नं.२.२
ग्राहक पिच्छे कर्जा विवरण
"""


def test_unified_directive_structure() -> None:
    units = parse_directive_units(_doc(UD_TEXT).pages)
    got = [(u.kind, u.section_no, u.section_title) for u in units]
    assert got == [
        ("preamble", None, None),
        ("section", "1.0", "न्यूनतम पुँजीकोष सम्बन्धी व्यवस्था"),
        ("section", "1.1", "कायम गर्नु पर्ने न्यूनतम पुँजीकोष"),
        ("section", "1.2", "पुँजीकोष"),
        ("section", "2.0", "कर्जा प्रवाह, वर्गीकरण तथा कर्जा नोक्सानी सम्बन्धी व्यवस्था"),
        ("section", "2.1", "कर्जा सापटको वर्गीकरण"),  # "१." alone: title from the line before
        ("section", "2.2", "खराब कर्जा सम्बन्धी थप व्यवस्था"),
        ("section", "3.0", "शाखा खोल्ने सम्बन्धी व्यवस्था"),
        ("section", "3.1", "शाखा खोल्न स्वीकृति"),
        ("schedule", "1.1", "अनुसूची 1.1: Capital Adequacy Framework"),
        ("schedule", "2.2", "अनुसूची 2.2: ग्राहक पिच्छे कर्जा विवरण"),
    ]
    assert units[5].chapter_title == "कर्जा प्रवाह, वर्गीकरण तथा कर्जा नोक्सानी सम्बन्धी व्यवस्था"


def test_directive_chunks_header_and_edition_key() -> None:
    entry = _entry(path="directives/ud.txt", doc_type="directive", fiscal_year="2082/83",
                   title="परिपत्र नं. १० (क, ख, ग) २०८२/८३: एकीकृत निर्देशन, २०८२ जारी गरिएको सम्बन्धमा ।")
    assert entry.family == "abc" and entry.doc_key == "ud_abc_2082" and entry.institutions == ["A", "B", "C"]
    chunks, _ = chunk_document(entry, _doc(UD_TEXT), 3000, 1800)
    c = next(c for c in chunks if c["section_no"] == "2.1")
    assert c["header"] == ("Unified Directive (class A/B/C banks) 2082 | Directive 2: कर्जा प्रवाह, वर्गीकरण तथा "
                           "कर्जा नोक्सानी सम्बन्धी व्यवस्था | Point 2.1: कर्जा सापटको वर्गीकरण | FY 2082/83")
    assert c["act_key"] == "ud_abc_2082" and c["effective_from_fy"] == "2082/83"


def test_circular_metadata_and_amendment_rows() -> None:
    entry = _entry(path="circulars/2083-84/bfr-circular-4-f.pdf", doc_type="circular", fiscal_year="2083/84",
                   title="परिपत्र नं. ४ (क, ख, ग) २०८३/८४: नेपाल राष्ट्र बैंकबाट “क”, “ख” र “ग” वर्गका इजाजतपत्रप्राप्त "
                         "संस्थाहरुलाई जारी गरिएको एकीकृत निर्देशन, २०८२ मा संशोधन/परिमार्जन/थप ।")
    assert entry.amends_key == "ud_abc_2082" and entry.circular_no == "4 (क, ख, ग)"
    text = ("एकीकृत निर्देशन, २०८२ मा संशोधन\n\n"
            "| क्र.सं. | व्यवस्था नं. | विद्यमान व्यवस्था | संशोधित व्यवस्था |\n|---|---|---|---|\n"
            "| १. | इ.प्रा. निर्देशन नं. १४/०८२ को बुँदा नं. २ | पुरानो | नयाँ |\n"
            "| २. | इ.प्रा. निर्देशन नं. २/०८२ | पुरानो २ | नयाँ २ |\n")
    intro, rows = amendment_rows(text)  # type: ignore[misc]
    assert intro.startswith("एकीकृत") and len(rows) == 2
    assert "संशोधित व्यवस्था: नयाँ" in rows[0]
    chunks, _ = chunk_document(entry, _doc(text), 3000, 1800)
    assert len(chunks) == 2


def test_find_refs_acts_and_directives() -> None:
    text = "नेपाल राष्ट्र बैंक ऐन, २०५८ को दफा ७९ ले दिएको अधिकार प्रयोग गरी इ.प्रा. निर्देशन नं. १४/०८२ को बुँदा नं. २ मा"
    assert find_refs(text, "ud_abc_2082") == ["nrb_act s.79", "ud_abc_2082 D.14", "ud_abc_2082 D.14.2"]
    assert find_refs("निर्देशन नं. ५/०८१ मा संशोधन", "ud_mfi_2082") == ["ud_mfi_2081 D.5"]
    assert find_refs("दफा १० बमोजिम", None) == []  # the act is not named: no guess


def test_numeric_facts_percent_and_amount() -> None:
    facts = extract_numeric_facts("(क) असल १ प्रतिशत\nएक पटकमा रु. ५,००,००० वा सोभन्दा बढी", "2082/83")
    assert facts[0]["rate_percent"] == 1.0 and facts[1]["amount"] == "500000"


def test_repeating_headers_and_page_numbers_removed() -> None:
    pages = [f"MINISTRY OF FINANCE HEADER\nChapter-{i}\nBody text {i} unique\nPage {i} of 4" for i in range(1, 5)]
    cleaned = strip_repeating_lines(pages)
    assert all("MINISTRY" not in p and "Page" not in p for p in cleaned)
    assert all(f"Chapter-{i}" in p for i, p in enumerate(cleaned, 1))  # headings are never stripped


def test_preeti_conversion() -> None:
    assert preeti_to_unicode("cfos/ P]g, @)%*") == "आयकर ऐन, २०५८"
    assert preeti_to_unicode("g]kfn") == "नेपाल"
    assert preeti_to_unicode("sfo{") == "कार्य"
    assert preeti_to_unicode("ljleGg") == "विभिन्न"
    assert looks_like_preeti("cfos/ P]g, @)%* sf] bkmf !) df ;+zf]wg ul/Psf] 5 . " * 3)
    assert not looks_like_preeti("This is ordinary English text about the income tax act and VAT. " * 3)
    # glyph pairs and reph between a pair
    assert preeti_to_unicode("e'QmfgL") == "भुक्तानी"
    assert preeti_to_unicode("qm=;+=") == "क्र.सं."
    assert preeti_to_unicode("dfk{mt") == "मार्फत"
    assert preeti_to_unicode("æsÆ") == "“क”"


def test_font_aware_conversion_keeps_unicode_words() -> None:
    assert font_family("ABCDEF+Preeti-Bold") == "preeti" and font_family("FONTASY_HIMALI_TT") == "number"
    assert font_family("TimesNewRomanPSMT") is None
    marked = "".join(MARK + ch for ch in "o; a}+saf6 hf/L ul/Psf]") + " ICAAP " + "".join(MARK + ch for ch in "kfngf")
    assert convert_marked(marked) == "यस बैंकबाट जारी गरिएको ICAAP पालना"


def test_law_commission_broken_text_layer_detected() -> None:
    broken = "संशोधन ममलाइएको । कारोबार गने अमधकार ववषय वववरण पने । " * 60
    clean = "संशोधन मिलाइएको । कारोबार गर्ने अधिकार विषय विवरण पर्ने । " * 60
    assert law_garble_score(broken) > 0.5 and law_garble_score(clean) == 0


def test_ocr_tolerant_sections_and_strict_schedules() -> None:
    # OCR dropped the numbers of sections 3 and 4; a schedule reference must not start a schedule;
    # a garbled "12ग. 63 वा ..." line must not be taken as a heading.
    text = (
        "१. संक्षिप्त नाम: यो ऐनको नाम आयकर ऐन हो।\n"
        "२. परिभाषा: यस ऐनमा, अनुसूची-१ मा उल्लिखित दरहरू लागू हुनेछन्।\n"
        "कर लगाउनेः (१) प्रत्येक व्यक्तिलाई कर लाग्नेछ।\n"
        "करको दरः (१) अनुसूची-१ बमोजिम दर लाग्नेछ।\n"
        "१२ग. ६३ वा यी दफा बमोजिम कुनै दाबी\n"
        "५. निर्धारणयोग्य आयः (१) आयको गणना हुनेछ।\n"
        "स्पष्टीकरणः यो शीर्षक होइन।\n"
        "अनुसूची-१\n"
        "| आय | दर |\n|---|---|\n| ५,००,००० सम्म | १% |\n"
    )
    units = parse_units(_doc(text).pages)
    sections = [(u.section_no, u.section_title) for u in units if u.kind == "section"]
    assert sections == [("1", "संक्षिप्त नाम"), ("2", "परिभाषा"), ("3", "कर लगाउने"), ("4", "करको दर"),
                        ("5", "निर्धारणयोग्य आय")]
    assert [u.section_no for u in units if u.kind == "schedule"] == ["1"]


def test_lone_section_numbers_pair_with_headings() -> None:
    # OCR of hanging indents: the number sits alone a few lines before its heading
    text = ("१.\nसंक्षिप्त नाम र प्रारम्भः (१) यस ऐनको नाम ... हो ।\n"
            "२.\nपरिभाषाः विषय वा प्रसङ्गले अर्को अर्थ नलागेमा ।\n"
            "३.\n४.\nसंस्थापनाः (१) बैंक संस्थापना गर्न ...।\nइजाजतपत्रः (१) इजाजतपत्र लिनु पर्नेछ ।\n")
    units = parse_units(_doc(text).pages)
    assert [(u.section_no, u.section_title) for u in units if u.kind == "section"] == [
        ("1", "संक्षिप्त नाम र प्रारम्भ"), ("2", "परिभाषा"), ("3", "संस्थापना"), ("4", "इजाजतपत्र")]


def test_garble_score_flags_broken_text_layer() -> None:
    from ingest.extract import garble_score

    broken = "आमथिक अध्यादेि संिोधन व्यशिले वर्ि भन् नाले प्राकृ मतक ि गने " * 20
    clean = "आर्थिक अध्यादेश संशोधन व्यक्तिले वर्ष भन्नाले प्राकृतिक व्यक्ति गर्ने " * 20
    assert garble_score(broken) > 0.01 > garble_score(clean)
