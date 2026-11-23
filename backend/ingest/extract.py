"""
extract.py
==========
Step 1 of the ingestion pipeline: source file -> clean text per page.

* PDF text via pypdf; pages that contain tables are re-extracted with
  pdfplumber so tables come out as markdown tables (kept whole by the chunker).
* Scanned pages (almost no extractable text) are OCR'd with Tesseract
  (eng+nep). If Tesseract is not installed you get a clear warning and the
  page is left empty instead of crashing.
* Legacy Preeti/Kantipur-font Nepali is detected and converted to Unicode.
* Repeating headers, footers and page numbers are removed by frequency
  analysis across pages.
* .txt/.md files are supported too (pages separated by a form-feed \\f) —
  handy for the SAMPLE corpus and for text you pasted from a website.

Results are cached in data/processed/extracted/<doc_id>.json keyed by the
file's SHA-256, so slow OCR runs only once per file version.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ingest.fonts import LAW_GARBLE_THRESHOLD, convert_marked, law_garble_score, mark_page
from ingest.manifest_tools import ManifestEntry
from ingest.preeti import looks_like_preeti, preeti_to_unicode

log = logging.getLogger(__name__)

EXTRACTOR_VERSION = 6
SCANNED_PAGE_MIN_CHARS = 40
# Tesseract page segmentation: 4 = one column of variable-size text. The default (3) splits the hanging
# indent of Nepali law PDFs into a separate column, moving "(क)" markers and section numbers away from their text.
OCR_PSM = 4
_DIGITS_RE = re.compile(r"[0-9०-९]+")
_PAGE_NUMBER_RE = re.compile(
    r"^\s*(?:[-–—(]\s*)?(?:page|pg\.?|पृष्ठ|पेज)?\s*#\s*(?:(?:of|/|मध्ये)\s*#)?\s*(?:[-–—)])?\s*$",
    re.IGNORECASE,
)
# Structural headings must never be mistaken for running headers.
_HEADING_RE = re.compile(r"^\s*(?:chapter|part|schedule|परिच्छेद|अनुसूची|भाग)\b|^\s*#+[a-z]?\.\s", re.IGNORECASE)


@dataclass
class ExtractedDoc:
    """Clean per-page text of one document."""

    doc_id: str
    path: str
    file_sha256: str
    pages: list[dict] = field(default_factory=list)  # [{"page": 1, "text": "..."}]
    warnings: list[str] = field(default_factory=list)
    extractor_version: int = EXTRACTOR_VERSION


GARBLE_THRESHOLD = 0.01  # invalid Devanagari sequences per word above which the text layer is not trusted
_ZW_RE = re.compile("[\u200b\u200c\u200d\ufeff]")
_INVALID_DEVANAGARI = [
    re.compile("\u094d[\u093e-\u094c\u0901\u0902]"),           # halant followed by a vowel sign
    re.compile("\u094d (?=[\u0915-\u0939])"),                     # conjunct split by a space ("भन् नाले")
    re.compile("(?<![\u0900-\u097F])[\u093e-\u094c]"),           # vowel sign at the start of a word
]


def garble_score(text: str) -> float:
    """
    Share of words with impossible Devanagari sequences. Many Nepali government PDFs embed
    fonts whose text layer extracts wrongly ("आर्थिक" -> "आमथिक", "मिति" -> "मममत");
    such text scores far above GARBLE_THRESHOLD, clean Unicode scores ~0.
    """
    text = _ZW_RE.sub("", text)
    words = len(re.findall("[\u0900-\u097F]+", text))
    if words < 50:
        return 0.0
    return sum(len(p.findall(text)) for p in _INVALID_DEVANAGARI) / words


# ── helpers ───────────────────────────────────────────────────────────────────
_GLYPH_FIXES = (("ª", "ङ्ग"), ("\u0970", "."))  # stray legacy-font glyphs left in Unicode text


def _fix_glyphs(text: str) -> str:
    for bad, good in _GLYPH_FIXES:
        text = text.replace(bad, good)
    text = re.sub(r"(?<=[०-९0-9])ः(?=[०-९0-9])", ":", text)  # Preeti ratio colon: "८०ः२०" -> "८०:२०"
    # pdfplumber sometimes puts a space before a dependent vowel sign ("सशं ोधित"); signs never start a word
    return re.sub(r"(?<=[\u0900-\u097F]) +(?=[\u093e-\u094d\u0901-\u0903])", "", text)
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def _norm_line(line: str) -> str:
    return _DIGITS_RE.sub("#", re.sub(r"\s+", " ", line.strip().lower()))


def strip_repeating_lines(pages: list[str], zone: int = 3, min_ratio: float = 0.5) -> list[str]:
    """
    Remove running headers/footers and page numbers.

    A line in the top/bottom `zone` lines of a page is dropped when its
    digit-normalized form appears in at least `min_ratio` of all pages (needs
    >= 3 pages), or when it is just a page number ("12", "- 12 -", "Page 3 of 9").
    """
    split = [p.splitlines() for p in pages]

    def zone_idx(lines: list[str]) -> list[int]:
        nonempty = [i for i, l in enumerate(lines) if l.strip()]
        return sorted(set(nonempty[:zone] + nonempty[-zone:]))

    counts: Counter[str] = Counter()
    for lines in split:
        counts.update({_norm_line(lines[i]) for i in zone_idx(lines)})
    threshold = max(2, int(len(pages) * min_ratio + 0.999))
    frequent = (
        {k for k, c in counts.items() if c >= threshold and k and not _HEADING_RE.match(k)}
        if len(pages) >= 3 else set()
    )

    cleaned: list[str] = []
    for lines in split:
        drop = {
            i for i in zone_idx(lines)
            if _norm_line(lines[i]) in frequent or _PAGE_NUMBER_RE.match(_norm_line(lines[i]))
        }
        kept = [l.rstrip() for i, l in enumerate(lines) if i not in drop]
        text = "\n".join(kept)
        cleaned.append(re.sub(r"\n{3,}", "\n\n", text).strip())
    return cleaned


def table_to_markdown(rows: list[list[str | None]]) -> str:
    """Convert a pdfplumber table (list of rows) into a GitHub markdown table."""
    clean = [[re.sub(r"\s+", " ", (c or "")).strip().replace("|", "/") for c in r] for r in rows if r]
    clean = [r for r in clean if any(r)]
    if not clean:
        return ""
    width = max(len(r) for r in clean)
    clean = [r + [""] * (width - len(r)) for r in clean]
    head, body = clean[0], clean[1:]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(lines)


# ── OCR ───────────────────────────────────────────────────────────────────────
class OCR:
    """Tesseract wrapper: Nepali model when installed, Hindi (same Devanagari script) otherwise."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = cache_dir  # OCR text per page is cached here: re-extraction never re-OCRs a page
        self.available = False
        self.langs = "eng"
        self.script: str | None = None
        self._warned = False
        try:
            import pytesseract

            cmd = os.getenv("TESSERACT_CMD") or shutil.which("tesseract")
            default_win = Path("C:/Program Files/Tesseract-OCR/tesseract.exe")
            if not cmd and default_win.exists():
                cmd = str(default_win)
            if cmd:
                pytesseract.pytesseract.tesseract_cmd = cmd
                installed = set(pytesseract.get_languages(config=""))
                script = "nep" if "nep" in installed else "hin" if "hin" in installed else None
                if script is None:
                    log.warning("Tesseract has neither 'nep' nor 'hin' language data; Nepali OCR will fail. "
                                "Reinstall Tesseract and tick Nepali under 'Additional language data'.")
                self.script = script
                self.langs = "+".join(x for x in (script, "eng" if "eng" in installed else None) if x) or "eng"
                self.available = True
        except Exception as exc:  # pragma: no cover - environment dependent
            log.warning("Tesseract unavailable", extra={"kv": {"error": str(exc)}})

    def _warn_missing(self) -> None:
        if not self._warned:
            log.warning(
                "OCR needed but Tesseract is not installed. Install it (Windows: UB-Mannheim build, tick "
                "Nepali; Mac: brew install tesseract tesseract-lang; Linux: apt install tesseract-ocr "
                "tesseract-ocr-nep) or set TESSERACT_CMD."
            )
            self._warned = True

    def page_text(self, pdf_path: Path, page_index: int) -> str:
        """OCR one PDF page; returns "" (with a one-time warning) when OCR is unavailable."""
        return self.pages_text(pdf_path, [page_index]).get(page_index, "")

    def pages_text(self, pdf_path: Path, indices: list[int], workers: int = 4,
                   nepali_only: bool = False) -> dict[int, str]:
        """
        OCR several pages in parallel (Tesseract runs as a subprocess, so threads scale).
        nepali_only drops the English model, which otherwise turns smudged Devanagari into Latin junk.
        """
        langs = self.script if nepali_only and self.script else self.langs
        key = f"{langs}|psm{OCR_PSM}"
        cache: dict[str, str] = {}
        cache_file = None
        if self.cache_dir is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file = self.cache_dir / f"{sha256_file(pdf_path)}.json"
            if cache_file.exists():
                cache = json.loads(cache_file.read_text(encoding="utf-8"))
        out: dict[int, str] = {i: cache[f"{key}:{i}"] for i in indices if f"{key}:{i}" in cache}
        todo = [i for i in indices if i not in out]
        if not todo:
            return out
        if not self.available:
            self._warn_missing()
            return out
        from concurrent.futures import ThreadPoolExecutor

        import pypdfium2 as pdfium
        import pytesseract

        pdf = pdfium.PdfDocument(str(pdf_path))
        try:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for start in range(0, len(todo), workers * 2):  # bounded batches keep memory low
                    batch = todo[start: start + workers * 2]
                    images = {i: pdf[i].render(scale=300 / 72).to_pil() for i in batch}
                    futures = {i: pool.submit(pytesseract.image_to_string, img, lang=langs,
                                              config=f"--psm {OCR_PSM}")
                               for i, img in images.items()}
                    for i, fut in futures.items():
                        out[i] = cache[f"{key}:{i}"] = fut.result()
                    if cache_file is not None:
                        cache_file.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
                    if len(todo) > 20:
                        log.info("OCR progress", extra={"kv": {"file": pdf_path.name,
                                                               "pages": f"{min(start + len(batch), len(todo))}/{len(todo)}"}})
        finally:
            pdf.close()
        return out


# ── per-format extraction ─────────────────────────────────────────────────────
def _page_text(plumber_page) -> tuple[str, int]:
    """
    Text of one page with legacy-font runs converted and tables rendered as markdown
    at their position. Returns (text, number of Preeti glyphs).
    """
    preeti = mark_page(plumber_page)
    try:
        tables = plumber_page.find_tables()
    except Exception:  # pdfplumber is best-effort on odd pages
        tables = []
    if not tables:
        return convert_marked(plumber_page.extract_text() or ""), preeti
    boxes = [t.bbox for t in tables]

    def outside(obj: dict) -> bool:
        cx = (obj.get("x0", 0) + obj.get("x1", 0)) / 2
        cy = (obj.get("top", 0) + obj.get("bottom", 0)) / 2
        return not any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in boxes)

    items: list[tuple[float, str]] = []
    for line in plumber_page.filter(outside).extract_text_lines():
        items.append((line["top"], convert_marked(line["text"])))
    for t in tables:
        rows = [[convert_marked(c) if c else c for c in row] for row in t.extract()]
        md = table_to_markdown(rows)
        if md:
            items.append((t.bbox[1], "\n" + md + "\n"))
    items.sort(key=lambda it: it[0])
    return "\n".join(text for _, text in items), preeti


def needs_full_ocr(reader, entry: ManifestEntry) -> tuple[bool, str]:
    """Decide whether the text layer is unusable (broken glyph order) and the document must be OCR'd."""
    if entry.ocr is True:
        return True, "forced by manifest"
    if entry.ocr is False:
        return False, ""
    sample = "\n".join((p.extract_text() or "") for p in reader.pages[:40])
    law = law_garble_score(sample)
    if law > LAW_GARBLE_THRESHOLD:
        return True, f"text layer has broken glyph order (score {law:.1f})"
    score = garble_score(sample)
    if score > GARBLE_THRESHOLD:
        return True, f"text layer garbled (score {score:.3f})"
    return False, ""


def _extract_pdf(path: Path, entry: ManifestEntry, ocr: OCR, warnings: list[str]) -> list[str]:
    import pdfplumber
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    full_ocr, reason = needs_full_ocr(reader, entry)
    if full_ocr:
        if ocr.available:
            warnings.append(f"whole document OCR'd: {reason}")
            log.info("OCR of whole document", extra={"kv": {"file": path.name, "pages": len(reader.pages),
                                                            "reason": reason}})
            pages = ocr.pages_text(path, list(range(len(reader.pages))), nepali_only=entry.language == "ne")
            return [pages.get(i, "") for i in range(len(reader.pages))]
        ocr._warn_missing()
        warnings.append(f"{reason}, but OCR is unavailable: Nepali text will be unreliable")

    texts: list[str] = []
    preeti_pages = 0
    scanned: list[int] = []
    with pdfplumber.open(str(path)) as plumber:
        for i, page in enumerate(plumber.pages):
            try:
                text, preeti = _page_text(page)
            except Exception as exc:  # fall back to the plain text layer
                warnings.append(f"page {i + 1}: pdfplumber failed ({exc}); used pypdf")
                text, preeti = reader.pages[i].extract_text() or "", 0
            preeti_pages += preeti > 0
            if len(text.strip()) < SCANNED_PAGE_MIN_CHARS:
                scanned.append(i)
            texts.append(text)
    if scanned:
        ocr_texts = ocr.pages_text(path, scanned)
        used = 0
        for i in scanned:
            if ocr_texts.get(i, "").strip():
                texts[i] = ocr_texts[i]
                used += 1
        if used:
            warnings.append(f"{used} scanned page(s) OCR'd")
        if used < len(scanned):
            warnings.append(f"{len(scanned) - used} page(s) without text (scanned, OCR unavailable)")
    if preeti_pages:
        warnings.append(f"{preeti_pages} page(s) had Preeti-font text converted to Unicode")
    return texts


def _extract_text_file(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").split("\f")


def extract_document(entry: ManifestEntry, raw_dir: Path, ocr: OCR | None = None) -> ExtractedDoc:
    """Extract clean per-page text for one manifest entry."""
    path = raw_dir / entry.path
    doc = ExtractedDoc(doc_id=entry.doc_id, path=entry.path, file_sha256=sha256_file(path))
    if path.suffix.lower() == ".pdf":
        raw_pages = _extract_pdf(path, entry, ocr or OCR(), doc.warnings)
    else:
        raw_pages = _extract_text_file(path)

    if entry.language == "ne" and path.suffix.lower() != ".pdf":  # PDFs are converted per font run
        converted = 0
        for i, text in enumerate(raw_pages):
            if looks_like_preeti(text):
                raw_pages[i] = preeti_to_unicode(text)
                converted += 1
        if converted:
            doc.warnings.append(f"{converted} page(s) converted from Preeti to Unicode")

    clean = strip_repeating_lines([_fix_glyphs(_ZW_RE.sub("", t)) for t in raw_pages])
    doc.pages = [{"page": i + 1, "text": t} for i, t in enumerate(clean)]
    return doc


def extract_all(entries: list[ManifestEntry], raw_dir: Path, cache_dir: Path) -> list[ExtractedDoc]:
    """Extract every document, reusing cached results when the file is unchanged."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    ocr: OCR | None = None
    docs: list[ExtractedDoc] = []
    for entry in entries:
        cache = cache_dir / f"{entry.doc_id}.json"
        digest = sha256_file(raw_dir / entry.path)
        if cache.exists():
            cached = json.loads(cache.read_text(encoding="utf-8"))
            if cached.get("file_sha256") == digest and cached.get("extractor_version") == EXTRACTOR_VERSION:
                docs.append(ExtractedDoc(**cached))
                continue
        if ocr is None and entry.path.lower().endswith(".pdf"):
            ocr = OCR(cache_dir=cache_dir.parent / "ocr_cache")
        try:
            doc = extract_document(entry, raw_dir, ocr)
        except Exception as exc:  # truncated/corrupt file: skip it, keep the rest of the corpus
            log.error("could not read document; skipped (download it again)",
                      extra={"kv": {"doc": entry.path, "error": str(exc)[:200]}})
            continue
        for w in doc.warnings:
            log.warning("extract", extra={"kv": {"doc": entry.path, "note": w}})
        cache.write_text(json.dumps(asdict(doc), ensure_ascii=False, indent=1), encoding="utf-8")
        log.info("extracted", extra={"kv": {"doc": entry.path, "pages": len(doc.pages)}})
        docs.append(doc)
    return docs
