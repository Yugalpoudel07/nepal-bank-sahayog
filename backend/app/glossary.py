"""
glossary.py
===========
A small English ↔ Nepali ↔ romanized-Nepali glossary of banking terms.

Used on both sides of BM25: when any form of a term appears in a chunk, every form is added to the
chunk's keywords, and when it appears in a question, every form is added to the search query. So a
question typed in another language or script ("karja noksani", "कर्जा नोक्सानी व्यवस्था",
"loan loss provision") matches the same provision. Purely deterministic and free; extend the list as
you find gaps.
"""

from __future__ import annotations

import re

# (English forms, Nepali forms, romanized forms)
GLOSSARY: list[tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = [
    (("loan", "credit", "lending"), ("कर्जा", "ऋण", "कर्जा सापट"), ("karja", "rin")),
    (("loan loss provision", "provisioning", "provision"), ("कर्जा नोक्सानी व्यवस्था", "नोक्सानी व्यवस्था"),
     ("karja noksani", "noksani byabastha")),
    (("loan classification",), ("कर्जा वर्गीकरण", "कर्जा सापटको वर्गीकरण"), ("karja bargikaran",)),
    (("pass loan", "good loan"), ("असल कर्जा", "असल"), ("asal karja",)),
    (("watch list",), ("सूक्ष्म निगरानी",), ("suksma nigrani",)),
    (("substandard",), ("कमसल",), ("kamsal",)),
    (("doubtful",), ("शंकास्पद",), ("shankaspad",)),
    (("bad loan", "loss loan"), ("खराब", "खराब कर्जा"), ("kharab karja",)),
    (("non-performing loan", "npl", "non performing"), ("निष्क्रिय कर्जा",), ("niskriya karja",)),
    (("overdue", "past due"), ("भाखा नाघेको",), ("bhakha naghe",)),
    (("rescheduling", "restructuring"), ("पुनरतालिकीकरण", "पुनरसंरचना"), ("punartalikikaran", "punarsanrachana")),
    (("collateral", "security"), ("धितो", "सुरक्षण"), ("dhito",)),
    (("capital adequacy", "capital fund"), ("पुँजीकोष", "पूँजीकोष", "पुँजीकोष अनुपात"), ("punjikosh",)),
    (("paid-up capital", "paid up capital"), ("चुक्ता पुँजी", "चुक्ता पूँजी"), ("chukta punji",)),
    (("core capital", "tier 1", "primary capital"), ("प्राथमिक पुँजी",), ("prathamik punji",)),
    (("single obligor limit", "single borrower limit"), ("एकल ग्राहक कर्जा सीमा", "एकल ग्राहक"), ("ekal grahak",)),
    (("sectoral limit",), ("क्षेत्रगत कर्जा", "क्षेत्रगत सीमा"), ("chetragat karja",)),
    (("interest rate",), ("ब्याजदर", "ब्याज दर"), ("byajdar", "byaj dar")),
    (("base rate",), ("आधार दर",), ("aadhar dar",)),
    (("spread",), ("ब्याजदर अन्तर", "स्प्रेड"), ("spread dar",)),
    (("deposit",), ("निक्षेप",), ("nikshep",)),
    (("savings account", "saving"), ("बचत खाता", "बचत"), ("bachat khata",)),
    (("fixed deposit", "term deposit"), ("मुद्दती निक्षेप", "मुद्दती"), ("muddati",)),
    (("cash reserve ratio", "crr"), ("अनिवार्य नगद मौज्दात", "अनिवार्य मौज्दात"), ("anivarya maujdat",)),
    (("statutory liquidity ratio", "slr"), ("वैधानिक तरलता",), ("baidhanik taralata",)),
    (("credit to deposit", "cd ratio", "ccd ratio"), ("कर्जा निक्षेप अनुपात", "कर्जा तथा निक्षेप अनुपात"), ("karja nikshep anupat",)),
    (("liquidity",), ("तरलता",), ("taralata",)),
    (("dividend", "bonus share"), ("लाभांश", "बोनस शेयर"), ("labhansa",)),
    (("board of directors", "director"), ("सञ्चालक समिति", "सञ्चालक"), ("sanchalak",)),
    (("chief executive officer", "ceo"), ("प्रमुख कार्यकारी अधिकृत",), ("pramukh karyakari",)),
    (("corporate governance", "governance"), ("संस्थागत सुशासन", "सुशासन"), ("susasan",)),
    (("promoter", "promoter share"), ("संस्थापक", "संस्थापक शेयर"), ("sansthapak",)),
    (("branch",), ("शाखा",), ("shakha",)),
    (("close a branch", "branch closure", "relocate", "shift"), ("शाखा बन्द", "बन्द गर्ने", "स्थानान्तरण", "गाभ्ने"),
     ("shakha banda",)),
    (("approval", "permission", "prior approval"), ("स्वीकृति", "पूर्वस्वीकृति", "अनुमति"), ("swikriti", "anumati")),
    (("bank account", "account"), ("खाता",), ("khata",)),
    (("cheque", "check"), ("चेक",), ("chek",)),
    (("bounce", "bounced", "dishonour", "dishonor", "dishonoured"), ("अनादर", "भुक्तानी नहुने", "बाउन्स"), ("anadar",)),
    (("student", "study abroad", "higher education", "abroad study"), ("विद्यार्थी", "उच्च शिक्षा", "अध्ययन"),
     ("bidyarthi", "uchha shiksha")),
    (("indian rupee", "indian currency", "inr"), ("भारतीय रुपैयाँ", "भारतीय मुद्रा", "भा.रू.", "भा.रु"), ("bharu",)),
    (("us dollar", "dollar", "usd"), ("अमेरिकी डलर", "डलर"), ("dollar",)),
    (("maximum", "upper limit", "ceiling"), ("बढीमा", "अधिकतम"), ("badhima",)),
    (("daily", "per day"), ("दैनिक",), ("dainik",)),
    (("transfer", "send money", "fund transfer"), ("रकमान्तर", "पठाउने", "स्थानान्तरण"), ("rakamantar",)),
    (("know your customer", "kyc", "customer due diligence"), ("ग्राहक पहिचान", "ग्राहक पहिचान तथा सम्पुष्टि"), ("grahak pahichan",)),
    (("money laundering", "aml"), ("सम्पत्ति शुद्धीकरण",), ("sampatti suddhikaran",)),
    (("terrorist financing", "cft"), ("आतंकवादी कार्यमा वित्तीय लगानी",), ("aatankabadi",)),
    (("blacklist", "blacklisted", "blacklisting"), ("कालोसूची", "कालो सूची"), ("kalo suchi",)),
    (("credit information bureau", "credit information"), ("कर्जा सूचना केन्द्र", "कर्जा सूचना"), ("karja suchana",)),
    (("customer protection", "grievance"), ("ग्राहक संरक्षण", "गुनासो"), ("gunaso",)),
    (("fee", "charge", "service charge"), ("शुल्क", "सेवा शुल्क"), ("sulka", "sewa sulka")),
    (("penalty", "fine", "action"), ("जरिवाना", "कारबाही", "कारवाही"), ("jariwana", "karbahi")),
    (("cash transaction", "cash"), ("नगद कारोबार", "नगद"), ("nagad karobar", "nagad")),
    (("cash limit", "cash transaction limit", "cash payment limit"), ("नगद कारोबारको सीमा", "एक पटकमा"),
     ("nagad sima",)),
    (("payment service provider", "psp", "wallet", "e-wallet", "digital wallet"), ("भुक्तानी सेवा प्रदायक", "वालेट", "ई-मनी"),
     ("bhuktani sewa", "wallet")),
    (("load limit", "transaction limit", "wallet limit"), ("कारोबार सीमा", "रकम जम्मा गर्ने सीमा"), ("karobar sima",)),
    (("payment system operator", "pso"), ("भुक्तानी प्रणाली सञ्चालक",), ("bhuktani pranali",)),
    (("mobile banking",), ("मोबाइल बैंकिङ", "मोबाइल बैंकिङ्ग"), ("mobile banking",)),
    (("digital lending",), ("विद्युतीय कर्जा", "विद्युतीय माध्यमबाट प्रवाह हुने कर्जा"), ("digital karja",)),
    (("foreign exchange", "forex"), ("विदेशी विनिमय", "विदेशी मुद्रा"), ("bideshi binimaya", "bideshi mudra")),
    (("currency exchange",), ("सटही",), ("satahi",)),
    (("letter of credit", "lc"), ("प्रतीतपत्र",), ("pratitpatra",)),
    (("remittance",), ("विप्रेषण", "रेमिट्यान्स"), ("bipreshan", "remittance")),
    (("monetary policy",), ("मौद्रिक नीति",), ("maudrik niti",)),
    (("policy rate",), ("नीतिगत दर",), ("nitigat dar",)),
    (("inflation",), ("मुद्रास्फीति",), ("mudrasfiti",)),
    (("merger", "acquisition"), ("मर्जर", "गाभ्ने", "प्राप्ति"), ("merger", "gabhne")),
    (("microfinance",), ("लघुवित्त",), ("laghubitta",)),
    (("commercial bank",), ("वाणिज्य बैंक",), ("banijya bank",)),
    (("development bank",), ("विकास बैंक",), ("bikas bank",)),
    (("finance company",), ("वित्त कम्पनी",), ("bitta company",)),
    (("unified directive", "directive"), ("एकीकृत निर्देशन", "निर्देशन"), ("ekikrit nirdeshan", "nirdeshan")),
    (("circular",), ("परिपत्र",), ("paripatra",)),
    (("notice",), ("सूचना",), ("suchana",)),
    (("fiscal year",), ("आर्थिक वर्ष",), ("arthik barsa",)),
    (("annex", "schedule", "form"), ("अनुसूची", "फाराम"), ("anusuchi",)),
    (("section",), ("दफा",), ("dafa",)),
    (("point", "clause"), ("बुँदा", "उपबुँदा"), ("bunda",)),
    (("licence", "license"), ("इजाजतपत्र", "अनुमतिपत्र"), ("ijajatpatra", "anumatipatra")),
    (("deprived sector", "priority sector"), ("विपन्न वर्ग", "तोकिएका क्षेत्र", "उत्पादनशील क्षेत्र"), ("bipanna barga",)),
    (("gold loan",), ("सुनचाँदी कर्जा", "सुन चाँदी"), ("sun chandi karja",)),
    (("margin lending", "share loan"), ("मार्जिन प्रकृतिको कर्जा", "शेयर धितो कर्जा"), ("margin karja",)),
]


def _pattern(form: str) -> re.Pattern[str]:
    if re.search(r"[A-Za-z]", form):
        return re.compile(rf"(?<![A-Za-z]){re.escape(form)}(?![A-Za-z])", re.IGNORECASE)
    return re.compile(re.escape(form))


_COMPILED = [
    ([_pattern(f) for f in (*en, *ne, *rom)], [*en, *ne, *rom]) for en, ne, rom in GLOSSARY
]


def glossary_terms(text: str) -> list[str]:
    """Every glossary form (all languages) of each term that occurs in the text."""
    found: list[str] = []
    for patterns, forms in _COMPILED:
        if any(p.search(text) for p in patterns):
            found.extend(f for f in forms if f not in found)
    return found
