"""
review_parameters.py
====================
Find the regulatory figures the calculators need in the indexed documents and write them to
data/processed/bank_parameters.csv for HUMAN verification.

What is looked for (in every directive edition / notice that contains it):
  * loan_loss_provision  — "(क) असल  १ प्रतिशत" ... "(ङ) खराब १०० प्रतिशत" in the loan-loss provision point
  * loan_overdue_band    — "सूक्ष्म निगरानी ... १ महिनादेखि ३ महिनासम्म भाखा नाघेका" etc. in the classification point
  * cash_transaction_limit — "एक पटकमा पाँच लाख रुपैयाँ वा सोभन्दा बढी" in the cash-limit notice

Each row carries the exact quote, file, the PDF page on which the quote appears and whether that page's text
came from OCR (text_source = ocr: a scanned page, so the quote may contain OCR typos).
Nothing is trusted automatically: rows start with verified=false. Check them and mark them, either by
editing the CSV or interactively:

    python -m ingest.review_parameters            # (re)write candidates, keep earlier verifications
    python -m ingest.review_parameters --review   # show each unverified row and ask y/n
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import re
from pathlib import Path

from app.config import get_settings
from app.fiscal_year import to_ascii_digits
from app.logging_setup import setup_logging
from tools.parameters import CSV_COLUMNS, TRUE_VALUES

log = logging.getLogger("review_parameters")

_CATEGORY_RE = {
    "pass": r"असल",
    "watchlist": r"सु?ू?क्ष्म\s*निगरानी",
    "substandard": r"कमसल",
    "doubtful": r"शंकास्पद",
    "loss": r"खराब",
}
_PROVISION_POINT_RE = re.compile(r"नोक्सानी\s*व्यवस्था\s*कायम")
_NUMBER_WORDS = {
    "एक": 1, "दुई": 2, "तीन": 3, "चार": 4, "पाँच": 5, "पाच": 5, "छ": 6, "सात": 7, "आठ": 8, "नौ": 9, "दश": 10,
    "दस": 10, "पन्ध्र": 15, "बीस": 20, "पच्चीस": 25, "तीस": 30, "चालीस": 40, "पचास": 50,
}
_SCALE = {"हजार": 1_000, "लाख": 100_000, "करोड": 10_000_000}


def _rows_id(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]


def _line_quote(text: str, start: int, end: int) -> str:
    a = text.rfind("\n", 0, start) + 1
    b = text.find("\n", end)
    return re.sub(r"\s+", " ", text[a: b if b != -1 else len(text)]).strip()[:300]


def _base_row(c: dict) -> dict:
    return {
        "family": c.get("family") or "general",
        "effective_from_fy": c.get("effective_from_fy") or "",
        "effective_to_fy": c.get("effective_to_fy") or "",
        "source_label": " | ".join(c["header"].split(" | ")[:3]),
        "source_file": c.get("source_file") or "",
        "page": str(c.get("page") or ""),
        "source_chunk_id": c["chunk_id"],
        "amended_by": "; ".join(c.get("amended_by") or [])[:500],
        "verified": "false",
    }


def provision_rates(c: dict) -> list[dict]:
    """Provision % per loan category from a directive point about loan-loss provisioning."""
    if c["doc_type"] != "directive":
        return []
    text = to_ascii_digits(c["text"])
    if not _PROVISION_POINT_RE.search(text):
        return []
    out = []
    for cat, name in _CATEGORY_RE.items():
        m = re.search(rf"^\s*\([^)]{{1,3}}\)\s*{name}[^\n%]{{0,40}}?(\d{{1,3}}(?:\.\d+)?)\s*(?:प्रतिशत|%)",
                      text, re.MULTILINE)
        if m:
            out.append({"parameter": "loan_loss_provision", "category": cat, "value": m.group(1), "unit": "percent",
                        "min_months": "", "max_months": "", "source_quote": _line_quote(text, m.start(), m.end())})
    return out if len(out) == len(_CATEGORY_RE) else []


def _months(n: str, unit: str) -> str:
    v = float(n) * (12 if "वर्ष" in unit else 1)
    return f"{v:g}"


def overdue_bands(c: dict) -> list[dict]:
    """Overdue-period definition of each loan category from the classification point."""
    if c["doc_type"] != "directive":
        return []
    text = to_ascii_digits(c["text"])
    if "भाखा" not in text or "कमसल" not in text:
        return []
    found: dict[str, tuple[str, str, re.Match]] = {}
    unit = r"(महिना|वर्ष)"
    # category headings look like "(२) सूक्ष्म निगरानी (WatchList) ः"; the period follows within a line or two
    head = {cat: rf"{name}\s*\([A-Za-z\- ]+\)\s*[ः:]" for cat, name in _CATEGORY_RE.items()}
    m = re.search(rf"{head['pass']}[\s\S]{{0,120}}?भाखा\s*ननाघेका\s*र\s*(\d+)\s*{unit}\s*सम्म", text)
    if m:
        found["pass"] = ("", _months(m.group(1), m.group(2)), m)
    for cat in ("watchlist", "substandard", "doubtful"):
        m = re.search(rf"{head[cat]}[\s\S]{{0,120}}?(\d+)\s*{unit}\s*देखि\s*(?:बढीमा\s*)?(\d+)\s*{unit}\s*सम्म\s*भाखा",
                      text)
        if m:
            found[cat] = (_months(m.group(1), m.group(2)), _months(m.group(3), m.group(4)), m)
    m = re.search(rf"{head['loss']}[\s\S]{{0,80}}?(\d+)\s*{unit}\s*भन्दा\s*बढी[^\n]{{0,20}}भाखा", text)
    if m:
        found["loss"] = (_months(m.group(1), m.group(2)), "", m)
    if len(found) != 5:
        return []
    # An overdue band is a range, not one figure: it lives in min_months/max_months and `value` stays empty.
    return [{"parameter": "loan_overdue_band", "category": cat, "value": "", "unit": "months",
             "min_months": lo, "max_months": hi,
             "source_quote": re.sub(r"\s+", " ", text[mm.start(): mm.end()])[:300]}
            for cat, (lo, hi, mm) in found.items()]


def _words_amount(words: str) -> int | None:
    """"पाँच लाख" -> 500000 (only the simple "<number word> <scale>" form)."""
    m = re.match(r"\s*(\S+)\s+(हजार|लाख|करोड)", words)
    if not m or m.group(1) not in _NUMBER_WORDS:
        return None
    return _NUMBER_WORDS[m.group(1)] * _SCALE[m.group(2)]


def cash_limit(c: dict) -> list[dict]:
    """The single-transaction cash limit from the cash-transaction notice."""
    if c["doc_type"] not in ("notice", "circular"):
        return []
    text = to_ascii_digits(c["text"])
    if "नगद" not in text:
        return []
    m = re.search(r"एक\s*पटकमा\s*((?:रु\.?\s*)?[\d,]+|\S+\s+(?:हजार|लाख|करोड))\s*(?:रुपैयाँ|रुपेयाँ|रुपियाँ)?\s*वा\s*सो\s*भन्दा\s*बढी",
                  text)
    if not m:
        return []
    raw = m.group(1)
    value = _words_amount(raw) if not re.search(r"\d", raw) else int(re.sub(r"[^\d]", "", raw))
    if not value:
        return []
    row = {"parameter": "cash_transaction_limit", "category": "single_transaction", "value": str(value), "unit": "NPR",
           "min_months": "", "max_months": "", "source_quote": _line_quote(text, m.start(), m.end())}
    return [row]


FINDERS = (provision_rates, overdue_bands, cash_limit)

MIN_TEXT_LAYER_CHARS = 20  # a PDF page with less embedded text than this is a scanned image (its text is OCR)


def _norm(text: str) -> str:
    """Comparable form of a text: ASCII digits, no whitespace."""
    return re.sub(r"\s+", "", to_ascii_digits(text))


class PageLocator:
    """
    Finds the PDF page on which a quote actually appears (a chunk can start on one page and continue on the
    next, so the chunk's start page is not good enough) and whether that page's text came from OCR.
    Pages are numbered as in a PDF viewer (1 = first page of the file).
    """

    def __init__(self, extracted_dir: Path, raw_dir: Path) -> None:
        self.extracted_dir, self.raw_dir = extracted_dir, raw_dir
        self._pages: dict[str, list[tuple[int, str]]] = {}
        self._doc_ocr: dict[str, bool] = {}
        self._layer: dict[tuple[str, int], int | None] = {}
        self._readers: dict[str, object] = {}

    def _load(self, doc_id: str) -> list[tuple[int, str]]:
        if doc_id not in self._pages:
            path = self.extracted_dir / f"{doc_id}.json"
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"pages": [], "warnings": []}
            self._pages[doc_id] = [(p["page"], _norm(p["text"])) for p in data["pages"]]
            self._doc_ocr[doc_id] = any("whole document OCR" in w for w in data.get("warnings", []))
        return self._pages[doc_id]

    def page_of(self, doc_id: str, quote: str, start_page: int | None) -> int | None:
        """Page where the quote begins, searching from the chunk's start page; None when not found."""
        pages = self._load(doc_id)
        probe = _norm(quote)[:40]
        if not probe:
            return start_page
        ordered = sorted(pages, key=lambda p: (p[0] < (start_page or 0), p[0]))
        for no, text in ordered:
            if probe in text:
                return no
        # the quote's opening words may straddle a page break: look in each page joined with the next one
        for (no, text), (_, nxt) in zip(pages, pages[1:]):
            joined = text + nxt
            i = joined.find(probe)
            if i != -1:
                return no if i < len(text) else no + 1
        return start_page

    def _text_layer(self, source_file: str, page: int) -> int | None:
        """Characters of embedded text on one PDF page (None when the PDF or page cannot be read)."""
        key = (source_file, page)
        if key not in self._layer:
            size = None
            try:
                if source_file not in self._readers:
                    from pypdf import PdfReader

                    self._readers[source_file] = PdfReader(str(self.raw_dir / source_file))
                reader = self._readers[source_file]
                if 1 <= page <= len(reader.pages):
                    size = len((reader.pages[page - 1].extract_text() or "").strip())
            except Exception as exc:  # missing or unreadable PDF: the source stays unknown
                log.warning("could not read PDF text layer", extra={"kv": {"file": source_file, "error": str(exc)}})
            self._layer[key] = size
        return self._layer[key]

    def text_source(self, doc_id: str, source_file: str, page: int | None) -> str:
        """"ocr" when the page is a scanned image (or the whole document was OCR'd), "pdf_text" otherwise."""
        self._load(doc_id)
        if self._doc_ocr.get(doc_id):
            return "ocr"
        size = self._text_layer(source_file, page) if page else None
        if size is None:
            return "unknown"
        return "ocr" if size < MIN_TEXT_LAYER_CHARS else "pdf_text"


def candidates(chunks: list[dict], locator: PageLocator | None = None) -> list[dict]:
    rows: list[dict] = []
    for c in chunks:
        for finder in FINDERS:
            for r in finder(c):
                row = _base_row(c) | r
                if finder is cash_limit:
                    row["family"] = "general"
                if locator is not None:
                    page = locator.page_of(c["doc_id"], row["source_quote"], c.get("page"))
                    row["page"] = str(page or "")
                    row["text_source"] = locator.text_source(c["doc_id"], row["source_file"], page)
                row["param_id"] = _rows_id(row["parameter"], row["category"], row["source_chunk_id"])
                rows.append(row)
    return rows


def export(chunks_path: Path, out: Path, locator: PageLocator | None = None) -> int:
    """Write bank_parameters.csv, keeping verified=true on rows whose figure, range and quote did not change."""
    chunks = json.loads(chunks_path.read_text(encoding="utf-8"))["chunks"]
    rows = candidates(chunks, locator)
    previous: dict[str, dict] = {}
    if out.exists():
        with out.open(encoding="utf-8-sig", newline="") as fh:
            previous = {r["param_id"]: r for r in csv.DictReader(fh)}
    for r in rows:
        old = previous.get(r["param_id"])
        same = all(old and old.get(k, "") == r.get(k, "") for k in ("value", "min_months", "max_months", "source_quote"))
        if same:
            r["verified"] = old.get("verified", "false")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for r in sorted(rows, key=lambda r: (r["parameter"], r["family"], r["effective_from_fy"], r["category"])):
            w.writerow({k: r.get(k, "") for k in CSV_COLUMNS})
    return len(rows)


def review(path: Path) -> None:
    """Ask y/n for every unverified row (Enter = skip, q = quit) and save the answers."""
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        if (r.get("verified") or "").lower() in TRUE_VALUES:
            continue
        band = f" ({r['min_months'] or 0}–{r['max_months'] or '∞'} months)" if r["parameter"] == "loan_overdue_band" else ""
        scope = f"[{r['family']}, FY {r['effective_from_fy'] or '-'}]"
        figure = band.strip() if band else f"{r['value']} {r['unit']}"
        print(f"\n{r['parameter']} / {r['category']}: {figure}  {scope}")
        print(f"  source: {r['source_file']} page {r['page']} (page number in the PDF viewer)")
        print(f"  quote : {r['source_quote']}")
        if r.get("text_source") == "ocr":
            print("  note  : this page is a scanned image; the quote was read by OCR and may contain OCR typos.\n"
                  "          Compare the figure with the page by eye.")
        answer = input("  Correct according to the PDF? [y/n/Enter=skip/q=quit] ").strip().lower()
        if answer == "q":
            break
        if answer in ("y", "n"):
            r["verified"] = "true" if answer == "y" else "false"
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    print(f"\nSaved {path}")


def main(argv: list[str] | None = None) -> None:
    setup_logging()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--review", action="store_true", help="interactively verify the candidate rows")
    args = ap.parse_args(argv)
    settings = get_settings()
    locator = PageLocator(settings.processed_dir / "extracted", settings.raw_dir)
    n = export(settings.index_dir / "chunks.json", settings.parameters_path, locator)
    log.info("parameter candidates exported", extra={"kv": {"rows": n, "path": str(settings.parameters_path)}})
    if args.review:
        review(settings.parameters_path)


if __name__ == "__main__":
    main()
