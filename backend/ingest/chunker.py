"""
chunker.py
==========
Step 2: structure-aware chunking (no fixed-size splitting).

* Acts & Rules — split on Chapter (परिच्छेद) and Section (दफा, "N. Title:")
  headings, keeping sub-sections (उपदफा) together. Only very long sections are
  split into groups of sub-sections; those pieces carry `parent_id` so the
  engine can return the whole section to the LLM (small-to-big).
* Unified directives / FX unified circular — split on directive headings
  ("इ.प्रा.निर्देशन नं. २/०८२", "अ.प्रा. निर्देशन नं. ५/०८२", "एकीकृत इ.प्रा. परिपत्र ः ७/२०८२") and, inside a
  directive, on its numbered points ("१. ...", "७.३ ..."); annexes (अनुसूची २.१) become their own units.
* Circulars & notices — amendment tables (क्र.सं. | व्यवस्था नं. | विद्यमान | संशोधित) are split per row,
  everything else into groups of numbered points; markdown tables are never split.

Every chunk carries a header line, e.g.
    "Unified Directive (class A/B/C banks) 2082 | Directive 2: कर्जा प्रवाह ... | Point 1: कर्जा सापटको वर्गीकरण | FY 2082/83"

Output: data/processed/structural_chunks.json
    {"documents": [...manifest entries...], "parents": {id: {...}}, "chunks": [...]}
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.acts import act_key_for_path, canonical_section_no, doc_display_name
from app.fiscal_year import to_ascii_digits
from ingest.extract import ExtractedDoc
from ingest.manifest_tools import ManifestEntry

log = logging.getLogger(__name__)

CHAPTER_RE = re.compile(
    r"^\s*(?:chapter|परिच्छेद)\s*[-–—:.]?\s*(\d{1,2}|[ivxlc]{1,6})\b\s*[:.\-–—]?\s*(.*)$", re.IGNORECASE
)
SECTION_RE = re.compile(r"^\s*(?:section\s+|दफा\s*)?(\d{1,3})([A-Za-zकखगघङ]?)\s*[.,]\s*(.+)$", re.IGNORECASE)
# A schedule heading is a line holding only "Schedule-1" / "अनुसूची–१" (references like "अनुसूची-१ मा ..." are not).
SCHEDULE_RE = re.compile(r"^\s*(?:schedule|अनुसूची)\s*[-–—]?\s*(\d{1,2})\s*[.:ः]?\s*$", re.IGNORECASE)
# OCR sometimes drops a section number; such headings still look like "<short title>ः (१) ..." after a
# finished sentence. Their numbers are filled in only when the gap between numbered sections fits exactly.
UNNUMBERED_HEADING_RE = re.compile(r"^\s*([^\s(“\"'\d][^:ः।|]{1,70}?)\s*[:ः](?=\s|-|–|\(|$)\s*(\S.*)$")
NOT_HEADINGS = ("स्पष्टीकरण", "तर", "प्रतिबन्धात्मक", "टिप्पणी", "explanation", "provided", "note", "illustration")
SENTENCE_END_RE = re.compile(r"[।|.)\]]\s*-?\s*$")
MAX_SECTION_JUMP = 20
# OCR of hanging-indent layouts puts the section number on its own line ("११.") a few lines before
# the heading ("शेयर बिक्री ... व्यवस्था: (१) ..."); such numbers are paired with the next heading.
LONE_NUMBER_RE = re.compile(r"^\s*(\d{1,3})([कखगघङ]?)\s*[.,।]\s*$")
LONE_NUMBER_REACH = 15  # lines
SUBSECTION_SPLIT_RE = re.compile(r"\n(?=\s*\((?:\d{1,2}|[०-९]{1,2})\)\s)")
ITEM_SPLIT_RE = re.compile(r"\n(?=\s*\((?:[a-z]{1,2}|\d{1,2}|[०-९]{1,2}|[क-ह])\)\s)")
POINT_SPLIT_RE = re.compile(r"\n(?=\s*(?:\d{1,2}|[०-९]{1,2})[.)]\s)")
_ROMAN = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100}


def _roman_to_int(s: str) -> int:
    total, prev = 0, 0
    for ch in reversed(s.lower()):
        v = _ROMAN[ch]
        total = total - v if v < prev else total + v
        prev = max(prev, v)
    return total


@dataclass
class Unit:
    """A structural unit (section, schedule, preamble, whole document)."""

    kind: str
    uid: str
    page: int
    section_no: str | None = None
    section_title: str | None = None
    chapter_no: str | None = None
    chapter_title: str | None = None
    lines: list[tuple[str, int]] = field(default_factory=list)

    @property
    def text(self) -> str:
        t = "\n".join(l for l, _ in self.lines)
        t = re.sub(r"[ \t]+", " ", t)
        t = re.sub(r" *\n *", "\n", t)
        return re.sub(r"\n{3,}", "\n\n", t).strip()

    @property
    def page_end(self) -> int:
        return self.lines[-1][1] if self.lines else self.page


# Titles end at ":" or, in Nepali, at the visarga-colon "ः" followed by a space, dash, bracket or line end
# (a visarga inside a word, as in "निःसर्ग" or "पुनः", is not a delimiter).
_TITLE_END_RE = re.compile(r":|ः(?=\s|-|–|\(|$)")


def _section_title(lines: list[tuple[str, int]], i: int, rest: str) -> str | None:
    """Section titles end at the first ':' / 'ः' and may wrap onto the next line."""
    joined = rest
    if not _TITLE_END_RE.search(joined) and i + 1 < len(lines):
        joined = rest + " " + lines[i + 1][0].strip()
    m = _TITLE_END_RE.search(joined)
    if not m:
        return None
    title = re.sub(r"\s+", " ", joined[: m.start()]).strip(" .-–—")
    if re.search(r"\d", re.sub(r"(?<!\d)(19|20)\d{2}(?!\d)", "", title)) or len(title.split()) > 14:
        return None  # real titles are short; apart from years (Act names) they hold no numbers, OCR noise does
    return title if 0 < len(title) <= 160 else None


def parse_units(pages: list[dict]) -> list[Unit]:
    """Split a document into preamble / sections / schedules, tracking chapters and pages."""
    lines = [(l, p["page"]) for p in pages for l in p["text"].split("\n")]
    units: list[Unit] = []
    chapter_no: str | None = None
    chapter_title: str | None = None
    current = Unit(kind="preamble", uid="preamble", page=pages[0]["page"] if pages else 1)
    last_sec = 0
    in_schedules = False
    used: set[str] = set()
    pending: list[tuple[int, str, int]] = []  # lone section numbers waiting for their heading

    def flush() -> None:
        if current.text:
            uid, n = current.uid, 2
            while uid in used:
                uid, n = f"{current.uid}_{n}", n + 1
            current.uid = uid
            used.add(uid)
            units.append(current)

    i = 0
    while i < len(lines):
        raw, page = lines[i]
        line = to_ascii_digits(raw)

        m = CHAPTER_RE.match(line)
        if m and len(line) < 120 and not in_schedules:
            num = m.group(1)
            chapter_no = num if num.isdigit() else str(_roman_to_int(num))
            title = m.group(2).strip()
            if not title:
                j = i + 1
                while j < len(lines) and not lines[j][0].strip():
                    j += 1
                nxt = to_ascii_digits(lines[j][0]) if j < len(lines) else ""
                if j < len(lines) and not SECTION_RE.match(nxt) and len(nxt) < 120:
                    title, i = lines[j][0].strip(), j
            chapter_title = title if title and not re.fullmatch(r"[\d\W]+", title) else None
            i += 1
            continue

        m = SCHEDULE_RE.match(line)
        if m and len(line.strip()) < 80 and not re.search(r"\b(of|in|by|to)\b\s*$", line, re.I):
            flush()
            in_schedules = True
            n = m.group(1)
            current = Unit(kind="schedule", uid=f"sch{n}", page=page, section_no=n,
                           section_title=f"Schedule-{n}", chapter_no=None, chapter_title=None,
                           lines=[(raw, page)])
            i += 1
            continue

        lone = LONE_NUMBER_RE.match(line)
        if lone and not in_schedules:
            pending.append((int(lone.group(1)), lone.group(2), i))
            i += 1
            continue
        pending = [p for p in pending if i - p[2] <= LONE_NUMBER_REACH]
        if pending and not in_schedules and _heading_title(line):
            n, suffix, _ = pending.pop(0)
            title = _heading_title(line)
            fits = (last_sec < n <= last_sec + MAX_SECTION_JUMP) or (bool(suffix) and n == last_sec)
            flush()
            no = canonical_section_no(f"{n}{suffix}") if fits else None
            if fits:
                last_sec = n
            current = Unit(kind="section", uid=f"s{no}" if no else f"u{i}", page=page, section_no=no,
                           section_title=title, chapter_no=chapter_no, chapter_title=chapter_title,
                           lines=[((f"{n}. " if fits else "") + raw, page)])
            i += 1
            continue

        m = SECTION_RE.match(line)
        if m and not in_schedules:
            n = int(m.group(1))
            suffix = m.group(2)
            ok_seq = (last_sec < n <= last_sec + MAX_SECTION_JUMP) or (bool(suffix) and n == last_sec)
            title = _section_title([(to_ascii_digits(l), p) for l, p in lines], i, m.group(3)) if ok_seq else None
            if title:
                flush()
                last_sec = n
                no = canonical_section_no(f"{n}{suffix}")
                current = Unit(kind="section", uid=f"s{no}", page=page, section_no=no, section_title=title,
                               chapter_no=chapter_no, chapter_title=chapter_title, lines=[(raw, page)])
                i += 1
                continue

        if not in_schedules and last_sec and _is_unnumbered_heading(line, current):
            flush()
            title = UNNUMBERED_HEADING_RE.match(line).group(1).strip(" .-–—")  # type: ignore[union-attr]
            current = Unit(kind="section", uid=f"u{i}", page=page, section_no=None, section_title=title,
                           chapter_no=chapter_no, chapter_title=chapter_title, lines=[(raw, page)])
            i += 1
            continue

        current.lines.append((raw, page))
        i += 1
    flush()
    _number_unnumbered(units)
    return units


def _heading_title(line: str) -> str | None:
    """Title of a "<short title>: (१) ..." heading line, or None."""
    m = UNNUMBERED_HEADING_RE.match(line)
    if not m:
        return None
    title = m.group(1).strip(" .-–—")
    if len(title.split()) > 9 or title.lower().startswith(NOT_HEADINGS) or "भन्नाले" in title or title.startswith("("):
        return None
    return title


def _is_unnumbered_heading(line: str, current: Unit) -> bool:
    """A number-less section heading: short title + colon + text, right after a finished sentence."""
    m = UNNUMBERED_HEADING_RE.match(line)
    if not m or current.kind != "section":
        return False
    title = m.group(1).strip()
    if len(title.split()) > 9 or title.lower().startswith(NOT_HEADINGS) or "भन्नाले" in line[: m.end(1)]:
        return False
    previous = next((l for l, _ in reversed(current.lines) if l.strip()), "")
    return bool(SENTENCE_END_RE.search(previous))


def _number_unnumbered(units: list[Unit]) -> None:
    """
    Give OCR-dropped section numbers back when the gap is unambiguous: k unnumbered sections
    between numbered sections a and b with b - a - 1 == k get a+1 .. b-1. Otherwise they keep
    no number (cited by title only) rather than a guessed one.
    """
    used = {u.uid for u in units}
    idx = 0
    while idx < len(units):
        if units[idx].kind == "section" and units[idx].section_no is None:
            start = idx
            while idx < len(units) and units[idx].kind == "section" and units[idx].section_no is None:
                idx += 1
            prev = next((u for u in reversed(units[:start]) if u.kind == "section" and u.section_no), None)
            nxt = units[idx] if idx < len(units) and units[idx].kind == "section" else None
            run = units[start:idx]
            a = _base_no(prev.section_no) if prev else None
            b = _base_no(nxt.section_no) if nxt and nxt.section_no else None
            if a is not None and b is not None and b - a - 1 == len(run):
                for k, u in enumerate(run, 1):
                    no = str(a + k)
                    if f"s{no}" not in used:
                        u.section_no, u.uid = no, f"s{no}"
                        used.add(u.uid)
        else:
            idx += 1


def _base_no(section_no: str | None) -> int | None:
    m = re.match(r"\d+", section_no or "")
    return int(m.group(0)) if m else None


# ── splitting helpers ─────────────────────────────────────────────────────────
def _group(pieces: list[str], target: int) -> list[str]:
    groups: list[str] = []
    buf = ""
    for p in pieces:
        if buf and len(buf) + len(p) > target:
            groups.append(buf.strip())
            buf = ""
        buf += ("\n" if buf else "") + p
    if buf.strip():
        groups.append(buf.strip())
    return groups


def split_keep_tables(text: str, target: int) -> list[str]:
    """Split on blank lines, never inside a markdown table."""
    blocks: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        if blocks and para.lstrip().startswith("|") and blocks[-1].rstrip().splitlines()[-1].lstrip().startswith("|"):
            blocks[-1] += "\n" + para
        else:
            blocks.append(para)
    return _group(blocks, target)


def _has_table(text: str) -> bool:
    return bool(re.search(r"^\s*\|.*\|\s*$", text, re.MULTILINE))


# ── unified directives ────────────────────────────────────────────────────────
# "इ.प्रा.निर्देशन नं. २/०८२", "अ.प्रा. निर्देशन नं.१०/०८२", "निर्देशन नं. ३/२०८२ः कर्जाको सीमा ...",
# "एकीकृत इ.प्रा. परिपत्र ः १/२०८२" (FX). Cross references ("... निर्देशन नं. २)” र ...") never match:
# the number must be followed by the end of the line, a "/year" or a colon + title.
DIRECTIVE_HEAD_RE = re.compile(
    r"^\s*(?:एकीकृत\s*)?(?:[इअ]\s*\.?\s*प्रा\s*\.?\s*)?(?:निर्देशन|परिपत्र)\s*[:ः]?\s*(?:नं\s*\.?)?\s*[:ः]?\s*"
    r"(\d{1,2})\s*(?:/\s*(?:20)?0?\d{2})?\s*(?:[:ः]\s*(.{0,90}))?\s*$"
)
# Points: "१. शीर्षक", "२.  पुँजीकोष", "१." alone; FX-style "१.१  प्रतीतपत्र कारोबारसम्बन्धी व्यवस्था".
POINT_RE = re.compile(r"^\s*(?:(\d{1,2})\.)?(\d{1,2})\s*\.?\s+(.*)$|^\s*(\d{1,2})\s*\.\s*$")
ANNEX_RE = re.compile(r"^\s*(?:अनुसूची|annex(?:ure)?|schedule)\s*(?:नं\s*\.?|no\.?)?\s*[-–—]?\s*"
                      r"(\d{1,2}(?:\s*[.।]\s*\d{1,2}[क-ह]?)?)\s*[:ः.]?\s*(\(.{0,60}\))?\s*$", re.IGNORECASE)
# Reporting forms in the annex volume: "ने.रा. बैंक निर्देशन फा.नं.२.२"
FORM_RE = re.compile(r"^\s*ने\s*\.?\s*रा\s*\.?\s*बै(?:ं|ङ्)क\s*निर्देशन\s*फा\s*\.?\s*नं\s*\.?\s*(\d{1,2}\s*\.\s*\d{1,2}[क-ह]?)")
# "खण्ड–२", "(अनुसूचीहरु)", "इ.प्रा.निर्देशन नं. १/०८२ सँग सम्बन्धित अनुसूची": the annex volume starts here
SECTION_PART_RE = re.compile(r"^\s*(?:खण्ड\s*[-–—]\s*[२2]|\(?अनुसूचीहरु\)?|.{0,40}सम्बन्धि?ी?त\s+अनुसूची)\s*$")
_TITLE_LIKE_RE = re.compile(r"[।|?]\s*$")


def _is_title(text: str) -> bool:
    text = text.strip().rstrip(" ।")
    return 0 < len(text) <= 110 and not _TITLE_LIKE_RE.search(text) and len(text.split()) <= 16


def parse_directive_units(pages: list[dict]) -> list[Unit]:
    """
    Split a unified directive into units: preamble, one unit per numbered point of each directive
    (chapter_no = directive number, section_no = "<directive>.<point>") and one unit per annex.
    """
    lines = [(l, p["page"]) for p in pages for l in p["text"].split("\n")]
    units: list[Unit] = []
    used: set[str] = set()
    current = Unit(kind="preamble", uid="preamble", page=pages[0]["page"] if pages else 1)
    d_no: int = 0
    d_title: str | None = None
    p_no = 0
    in_annex = False

    def flush() -> None:
        if current.text:
            uid, n = current.uid, 2
            while uid in used:
                uid, n = f"{current.uid}_{n}", n + 1
            current.uid = uid
            used.add(uid)
            units.append(current)

    def next_text(i: int) -> tuple[str, int]:
        j = i + 1
        while j < len(lines) and not lines[j][0].strip():
            j += 1
        return (lines[j][0].strip(), j) if j < len(lines) else ("", i)

    i = 0
    while i < len(lines):
        raw, page = lines[i]
        line = to_ascii_digits(raw)

        if SECTION_PART_RE.match(line) and d_no:
            in_annex = True

        m = ANNEX_RE.match(line) or (FORM_RE.match(line) if in_annex else None)
        if m and (in_annex or d_no):
            no = re.sub(r"\s*[.।]\s*", ".", m.group(1))
            title = ((m.group(2) if m.re is ANNEX_RE else "") or "").strip("() ")
            if not title:
                nxt, j = next_text(i)
                if _is_title(to_ascii_digits(nxt)) and not ANNEX_RE.match(to_ascii_digits(nxt)):
                    title = nxt
            flush()
            in_annex = True
            current = Unit(kind="schedule", uid=f"sch{no}", page=page, section_no=no,
                           section_title=f"अनुसूची {no}" + (f": {title}" if title else ""),
                           chapter_no=None, chapter_title=None, lines=[(raw, page)])
            i += 1
            continue

        m = DIRECTIVE_HEAD_RE.match(line)
        if m and len(line.strip()) <= 110:
            n = int(m.group(1))
            # inside annexes only the very next directive number ends the annex (FX circular, NIFRA
            # directive keep their annexes between directives; A/B/C directives keep them at the end)
            expected = n == d_no + 1 if in_annex else (n == d_no + 1 or (d_no == 0 and n <= 2) or (d_no < n <= d_no + 2))
            if expected:
                in_annex = False
                title = (m.group(2) or "").strip(" ।.")
                k = i
                if not title:
                    nxt, j = next_text(i)
                    if _is_title(nxt) and not POINT_RE.match(to_ascii_digits(nxt)):
                        title, k = nxt.strip(" ।."), j
                flush()
                d_no, d_title, p_no = n, title or None, 0
                current = Unit(kind="section", uid=f"d{n}", page=page, section_no=f"{n}.0",
                               section_title=d_title, chapter_no=str(n), chapter_title=d_title,
                               lines=[(l, pg) for l, pg in lines[i: k + 1]])
                i = k + 1
                continue

        if d_no and not in_annex:
            m = POINT_RE.match(line)
            if m:
                lead = int(m.group(1)) if m.group(1) else None
                n = int(m.group(2) or m.group(4))
                rest = (m.group(3) or "").strip()
                if (lead is None or lead == d_no) and n == p_no + 1:
                    title = rest if _is_title(rest) else None
                    head_lines = [(raw, page)]
                    if not rest:  # "१." alone: title is on the line before ("कर्जा सापटको वर्गीकरण\n१.")
                        prev = [k for k, (l, _) in enumerate(current.lines) if l.strip()]
                        if prev and _is_title(current.lines[prev[-1]][0]) and len(current.lines[prev[-1]][0]) < 80:
                            title = current.lines[prev[-1]][0].strip()
                            head_lines = [current.lines.pop(prev[-1]), (raw, page)]
                    if rest or title:
                        flush()
                        p_no = n
                        current = Unit(kind="section", uid=f"d{d_no}p{n}", page=page, section_no=f"{d_no}.{n}",
                                       section_title=title, chapter_no=str(d_no), chapter_title=d_title,
                                       lines=head_lines)
                        i += 1
                        continue

        current.lines.append((raw, page))
        i += 1
    flush()
    return units


# ── circular amendment tables ─────────────────────────────────────────────────
_AMEND_HEADER_RE = re.compile(r"विद्यमान|संशोधित|existing|amended", re.IGNORECASE)


def amendment_rows(text: str) -> tuple[str, list[str]] | None:
    """
    Circular amendment tables (क्र.सं. | व्यवस्था नं. | विद्यमान व्यवस्था | संशोधित व्यवस्था): return
    (text before the first table, one text per row with the column names inlined), or None.
    """
    lines = text.split("\n")
    rows: list[str] = []
    header: list[str] | None = None
    intro: list[str] = []
    seen_table = False
    for line in lines:
        if line.lstrip().startswith("|") and line.rstrip().endswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if all(re.fullmatch(r"-+", c) for c in cells if c):
                continue
            if _AMEND_HEADER_RE.search(" ".join(cells)) and not any(len(c) > 60 for c in cells):
                header = [c for c in cells]
                seen_table = True
                continue
            if header is not None:
                parts = [f"{h}: {c}" if h else c for h, c in zip(header + [""] * len(cells), cells) if c]
                if parts:
                    rows.append("\n".join(parts))
                continue
        elif not seen_table:
            intro.append(line)
        elif rows:
            rows[-1] += "\n" + line
    return ("\n".join(intro).strip(), rows) if header is not None and rows else None


# ── chunk construction ────────────────────────────────────────────────────────
def doc_label(entry: ManifestEntry) -> str:
    """Short citation name: directive edition, Act name, "NRB Circular No. 5 (क, ख, ग)", or the title."""
    if entry.doc_type in ("circular", "notice") and entry.circular_no:
        kind = "Notice" if entry.doc_type == "notice" else "Circular"
        prefix = {"fx": "NRB FX", "psd": "NRB Payment Systems"}.get(entry.family or "", "NRB")
        return f"{prefix} {kind} No. {entry.circular_no}"
    return doc_display_name(entry.doc_key) or doc_display_name(act_key_for_path(entry.path)) or entry.title


def _header(entry: ManifestEntry, unit: Unit, extra: str | None = None) -> str:
    parts = [doc_label(entry)]
    if entry.doc_type == "directive":
        if unit.chapter_no:
            parts.append(f"Directive {unit.chapter_no}" + (f": {unit.chapter_title}" if unit.chapter_title else ""))
        if unit.kind == "section" and unit.section_no and not unit.section_no.endswith(".0"):
            parts.append(f"Point {unit.section_no}" + (f": {unit.section_title}" if unit.section_title else ""))
        elif unit.kind == "schedule":
            parts.append(unit.section_title or f"अनुसूची {unit.section_no}")
        elif unit.kind == "preamble":
            parts.append("Cover letter and contents")
    else:
        if unit.chapter_no:
            parts.append(f"Chapter {unit.chapter_no}" + (f": {unit.chapter_title}" if unit.chapter_title else ""))
        if unit.kind == "section":
            label = "Rule" if entry.doc_type == "rules" else "Section"
            parts.append(f"{label} {unit.section_no}: {unit.section_title}" if unit.section_no
                         else f"{label}: {unit.section_title}")
        elif unit.kind == "schedule":
            parts.append(unit.section_title or f"Schedule-{unit.section_no}")
        elif unit.kind == "preamble":
            parts.append("Preamble")
    if extra:
        parts.append(extra)
    fy = entry.effective_fy()
    if fy:
        parts.append(f"FY {fy}")
    return " | ".join(parts)


def _base_chunk(entry: ManifestEntry, unit: Unit, parent_id: str) -> dict:
    fy = entry.effective_fy()
    return {
        "doc_id": entry.doc_id,
        "parent_id": parent_id,
        "doc_type": entry.doc_type,
        "act_key": act_key_for_path(entry.path) if entry.doc_type == "act" else entry.doc_key,
        "family": entry.family,
        "institutions": entry.institutions,
        "amends_key": entry.amends_key,
        "circular_no": entry.circular_no,
        "published": entry.published,
        "title": entry.title,
        "doc_label": doc_label(entry),
        "source_file": entry.path,
        "source_url": entry.source_url,
        "issue_date_bs": entry.issue_date_bs,
        "language": entry.language,
        "fiscal_year": fy,
        "page": unit.page,
        "page_end": unit.page_end,
        "unit_kind": unit.kind,
        "chapter_no": unit.chapter_no,
        "chapter_title": unit.chapter_title,
        "section_no": unit.section_no,
        "section_title": unit.section_title,
        "effective_from_fy": fy if entry.doc_type in ("directive", "circular", "notice", "monetary_policy") else None,
        "effective_to_fy": fy if entry.doc_type == "monetary_policy" else None,
        "amends": [],
        "amended_by": [],
        "supersedes": [],
        "superseded_by": [],
        "topics": [],
        "is_table": False,
    }


def _split_long(text: str, max_chars: int, target: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    for splitter in (SUBSECTION_SPLIT_RE, ITEM_SPLIT_RE, POINT_SPLIT_RE):
        parts = splitter.split(text)
        if len(parts) > 1:
            groups = _group(parts, target)
            return [g for grp in groups for g in (split_keep_tables(grp, target) if len(grp) > max_chars * 1.5 else [grp])]
    return split_keep_tables(text, target)


def chunk_document(entry: ManifestEntry, doc: ExtractedDoc, max_chars: int, target: int) -> tuple[list[dict], dict]:
    """Return (chunks, parents) for one document."""
    chunks: list[dict] = []
    parents: dict[str, dict] = {}
    if not doc.pages or not any(p["text"].strip() for p in doc.pages):
        log.warning("document has no text; skipped", extra={"kv": {"doc": entry.path}})
        return chunks, parents

    whole = [Unit(kind="document", uid="doc", page=doc.pages[0]["page"],
                  lines=[(l, p["page"]) for p in doc.pages for l in p["text"].split("\n")])]
    if entry.doc_type == "directive":
        units = parse_directive_units(doc.pages)
        if sum(u.kind == "section" for u in units) < 3:
            log.warning("no directive headings detected; using generic structure", extra={"kv": {"doc": entry.path}})
            units = parse_units(doc.pages)
    elif entry.doc_type in ("act", "rules", "policy"):
        units = parse_units(doc.pages)
        if sum(u.kind == "section" for u in units) < 3:
            if entry.doc_type != "policy":
                log.warning("no section headings detected; treating document as plain text",
                            extra={"kv": {"doc": entry.path}})
            units = whole
    else:  # circulars, notices, monetary policy
        units = whole

    for unit in units:
        parent_id = f"{entry.doc_id}::{unit.uid}"
        text = unit.text
        parents[parent_id] = {"header": _header(entry, unit), "text": text, "page": unit.page,
                              "page_end": unit.page_end}
        pieces: list[str]
        rows = amendment_rows(text) if entry.doc_type in ("circular", "notice") else None
        if rows:
            intro, row_texts = rows
            intro_line = " ".join(intro.split())[:400]
            pieces = [f"{intro_line}\n{r}" if intro_line else r for r in row_texts]
            pieces = [p for piece in pieces for p in _split_long(piece, max_chars * 2, target * 2)]
        elif entry.doc_type == "monetary_policy":
            parts = POINT_SPLIT_RE.split(text)
            pieces = _group(parts, target) if len(parts) > 1 else split_keep_tables(text, target)
        elif _has_table(text) and len(text) <= max_chars * 2:
            pieces = [text]  # keep a provision with its table whole
        else:
            pieces = _split_long(text, max_chars, target)

        for k, piece in enumerate(pieces):
            cid = parent_id if len(pieces) == 1 else f"{parent_id}#{k}"
            extra = f"Part {k + 1}/{len(pieces)}" if len(pieces) > 1 else None
            chunk = _base_chunk(entry, unit, parent_id)
            chunk.update({
                "chunk_id": cid,
                "sub": k,
                "header": _header(entry, unit, extra),
                "text": piece,
                "is_table": _has_table(piece),
            })
            chunks.append(chunk)
    return chunks, parents


def chunk_corpus(entries: list[ManifestEntry], docs: list[ExtractedDoc], max_chars: int, target: int) -> dict:
    """Chunk every document; returns the structural_chunks.json payload."""
    by_id = {d.doc_id: d for d in docs}
    all_chunks: list[dict] = []
    all_parents: dict[str, dict] = {}
    for entry in entries:
        doc = by_id.get(entry.doc_id)
        if doc is None:
            continue
        chunks, parents = chunk_document(entry, doc, max_chars, target)
        all_chunks.extend(chunks)
        all_parents.update(parents)
        log.info("chunked", extra={"kv": {"doc": entry.path, "chunks": len(chunks)}})
    documents = [e.model_dump() | {"doc_id": e.doc_id, "effective_fy": e.effective_fy(), "doc_key": e.doc_key,
                                   "amends_key": e.amends_key, "doc_label": doc_label(e)} for e in entries]
    return {"documents": documents, "parents": all_parents, "chunks": all_chunks}
