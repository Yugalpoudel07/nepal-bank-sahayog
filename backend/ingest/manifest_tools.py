"""
manifest_tools.py
=================
Load and validate `data/raw/manifest.yaml`, and infer the banking-specific metadata
(institution classes, directive family, circular number) from titles and file names.

Each manifest entry describes one source file:

    path            relative to data/raw/ (forward slashes)
    doc_type        act | rules | directive | circular | notice | policy | monetary_policy
    title           title as published by Nepal Rastra Bank (used in citations)
    language        en | ne
    fiscal_year     "2082/83" (directives, circulars, notices, monetary policy; null for standing law)
    family          which rulebook the document belongs to / amends:
                      abc   = unified directive for class A/B/C banks ("क", "ख", "ग" वर्ग)
                      mfi   = class D microfinance (घ वर्ग)
                      nifra = infrastructure development bank (पूर्वाधार)
                      psd   = payment-system directive (payment service providers/operators)
                      fx    = foreign-exchange unified circular (एकीकृत परिपत्र)
                      ncbl  = limited-banking cooperative bank
                      general = everything else
    institutions    who the document applies to: A B C D infra psp fx coop all
    base_year       for directives: the edition year (2082 for "एकीकृत निर्देशन, २०८२");
                    for circulars: the edition year of the directive they amend
    circular_no     "4 (क, ख, ग)" / "2" etc. as printed (optional)
    published       "YYYY-MM" upload month on nrb.org.np, used to order circulars
    source_url      official URL
    ocr             true = always OCR, false = never; omit for automatic detection
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, field_validator

from app.fiscal_year import fy_from_bs_date_string, fy_label, normalize_fy_label, to_ascii_digits

log = logging.getLogger(__name__)

DocType = Literal["act", "rules", "directive", "circular", "notice", "policy", "monetary_policy"]
Family = Literal["abc", "mfi", "nifra", "psd", "fx", "ncbl", "general"]
SUPPORTED_SUFFIXES = {".pdf", ".txt", ".md"}
DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")
AMENDING_TYPES = ("circular", "notice")
FY_SCOPED_TYPES = ("directive", "circular", "notice", "monetary_policy")

FAMILY_INSTITUTIONS: dict[str, list[str]] = {
    "abc": ["A", "B", "C"], "mfi": ["D"], "nifra": ["infra"], "psd": ["psp"], "fx": ["fx"],
    "ncbl": ["coop"], "general": ["all"],
}
FAMILY_NAMES: dict[str, tuple[str, str]] = {
    "abc": ("Unified Directive for class A, B and C banks", "“क”, “ख” र “ग” वर्गका संस्थालाई जारी एकीकृत निर्देशन"),
    "mfi": ("Unified Directive for class D microfinance institutions", "“घ” वर्गका लघुवित्त संस्थालाई जारी एकीकृत निर्देशन"),
    "nifra": ("Unified Directive for the infrastructure development bank", "पूर्वाधार विकास बैंकलाई जारी एकीकृत निर्देशन"),
    "psd": ("Payment System Unified Directive", "भुक्तानी प्रणालीसम्बन्धी एकीकृत निर्देशन"),
    "fx": ("Foreign Exchange Unified Circular", "विदेशी विनिमयसम्बन्धी एकीकृत परिपत्र"),
    "ncbl": ("Directive for limited-banking cooperative banks", "सीमित बैंकिङ सहकारी बैंकका लागि निर्देशन"),
}


class ManifestEntry(BaseModel):
    """One document in the corpus."""

    path: str
    doc_type: DocType
    title: str
    language: Literal["en", "ne"] = "ne"
    fiscal_year: str | None = None
    issue_date_bs: str | None = None
    family: Family | None = None
    institutions: list[str] | None = None
    base_year: int | None = None
    circular_no: str | None = None
    published: str | None = None
    source_url: str | None = None
    ocr: bool | None = None

    @field_validator("path")
    @classmethod
    def _posix_path(cls, v: str) -> str:
        return v.replace("\\", "/").lstrip("./")

    @field_validator("fiscal_year", mode="before")
    @classmethod
    def _norm_fy(cls, v: object) -> str | None:
        if v in (None, ""):
            return None
        label = normalize_fy_label(str(v))
        if label is None:
            raise ValueError(f"Unrecognised fiscal_year {v!r}; use the form 2082/83")
        return label

    def model_post_init(self, _ctx: object) -> None:
        if self.family is None:
            self.family = guess_family(self.title, self.path)  # type: ignore[assignment]
        if self.institutions is None:
            self.institutions = list(FAMILY_INSTITUTIONS[self.family or "general"])
        if self.base_year is None and self.doc_type in ("directive", *AMENDING_TYPES):
            self.base_year = guess_base_year(self.title, self.doc_type)
        if self.circular_no is None and self.doc_type in AMENDING_TYPES:
            self.circular_no = guess_circular_no(self.title)

    @property
    def doc_id(self) -> str:
        """Stable identifier derived from the path (used as chunk-id prefix)."""
        stem = re.sub(r"\.[A-Za-z0-9]+$", "", self.path)
        return re.sub(r"[^A-Za-z0-9]+", "_", stem).strip("_").lower()

    @property
    def doc_key(self) -> str | None:
        """Identity of a rulebook edition ("ud_abc_2082"); circulars point at it via amends_key."""
        if self.doc_type == "directive" and self.family and self.family != "general" and self.base_year:
            return f"ud_{self.family}_{self.base_year}"
        return None

    @property
    def amends_key(self) -> str | None:
        """doc_key of the directive edition a circular amends (if the title says so)."""
        if self.doc_type in AMENDING_TYPES and self.family and self.family != "general" and self.base_year:
            return f"ud_{self.family}_{self.base_year}"
        return None

    def effective_fy(self) -> str | None:
        """fiscal_year, or the FY derived from issue_date_bs when fiscal_year is missing."""
        if self.fiscal_year:
            return self.fiscal_year
        start = fy_from_bs_date_string(self.issue_date_bs)
        return fy_label(start) if start is not None else None


# ── metadata inference ────────────────────────────────────────────────────────
def guess_family(title: str, path: str) -> str:
    """Which rulebook a document belongs to, from its NRB title and file name."""
    t, p = title, path.lower()
    name = p.rsplit("/", 1)[-1]
    if name.startswith("fx-") or "एकीकृत परिपत्र" in t or "विदेशी विनिमय सम्बन्धी एकीकृत" in t:
        return "fx"
    if name.startswith("psd-") or "भुक्तानी प्रणालीसम्बन्धी एकीकृत" in t or "भुक्तानीसम्बन्धी कार्य" in t:
        return "psd"
    if "सहकारी बैंक" in t or "ncbl" in p:
        return "ncbl"
    if "पूर्वाधार" in t or "nifra" in p:
        return "nifra"
    if re.search(r"घ\s*वर्ग|\(\s*घ\s*\)|लघुवित्त", t) or "mfi" in p:
        return "mfi"
    if re.search(r"क\s*[,”\"]?\s*,?\s*[“\"]?ख", t) or "abc" in p or re.search(r"Unified Directives? A,? ?B", t, re.I):
        return "abc"
    return "general"


_EDITION_RE = re.compile(r"(?:एकीकृत\s*(?:निर्देशन|परिपत्र)|Unified\s+Directives?(?:\s+A,?\s*B,?\s*and\s*C)?|निर्देशन)"
                         r"\s*[,–\-]?\s*(20[78]\d)", re.IGNORECASE)


def guess_base_year(title: str, doc_type: str) -> int | None:
    """Edition year of the directive a document is (directive) or amends (circular)."""
    t = to_ascii_digits(title)
    years = [int(m.group(1)) for m in _EDITION_RE.finditer(t)]
    return years[-1] if doc_type == "directive" and years else (years[0] if years else None)


def guess_circular_no(title: str) -> str | None:
    """'परिपत्र नं. ४ (क, ख, ग) २०८३/८४' -> '4 (क, ख, ग)'; '२/२०८३–८४ः ...' -> '2'."""
    t = to_ascii_digits(title)
    m = re.search(r"(?:परिपत्र|सूचना)\s*नं\.?\s*0?(\d{1,2})\s*(\([^)]{1,15}\))?", t)
    if m:
        group = re.sub(r"\s*([(),])\s*", r"\1", m.group(2) or "").replace(",", ", ")
        return f"{m.group(1)} {group}".strip()
    m = re.match(r"\s*(\d{1,2})\s*/\s*20\d\d", t)
    return m.group(1) if m else None


# ── loading ───────────────────────────────────────────────────────────────────
def _guess_language(path: Path) -> str:
    return "ne" if DEVANAGARI_RE.search(path.stem) else "en"


def guess_entry(raw_dir: Path, file_path: Path) -> ManifestEntry:
    """Best-guess entry for a file that is not in the manifest (folder decides the type)."""
    rel = file_path.relative_to(raw_dir).as_posix()
    top = rel.split("/")[0]
    doc_type = {"acts": "act", "rules": "rules", "directives": "directive", "policies": "policy",
                "monetary_policy": "monetary_policy"}.get(top, "circular")
    fy = None
    for part in rel.split("/")[:-1]:
        fy = normalize_fy_label(part.replace("_", "-")) or fy
    title = re.sub(r"[_\-]+", " ", file_path.stem).strip()
    return ManifestEntry(path=rel, doc_type=doc_type, title=title,  # type: ignore[arg-type]
                         language=_guess_language(file_path), fiscal_year=fy)  # type: ignore[arg-type]


def discover_files(raw_dir: Path) -> list[Path]:
    """All supported source files under data/raw (manifest excluded), sorted."""
    return sorted(
        p
        for p in raw_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES and not p.name.startswith(".")
        and p.name.lower() not in {"readme.md"}
    )


def load_manifest(raw_dir: Path, manifest_path: Path | None = None) -> list[ManifestEntry]:
    """
    Load manifest entries for every file present in data/raw.

    Files listed in the manifest but missing on disk are skipped with a warning; files on disk
    but missing from the manifest get guessed metadata (also warned).
    """
    manifest_path = manifest_path or raw_dir / "manifest.yaml"
    listed: dict[str, ManifestEntry] = {}
    if manifest_path.exists():
        data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        for item in data.get("documents", []) or []:
            item = dict(item)
            if "file" in item and "path" not in item:  # format written by older download_nrb.py
                item["path"] = item.pop("file")
            item.pop("present", None)
            entry = ManifestEntry(**item)
            listed[entry.path] = entry
    else:
        log.warning("manifest not found; guessing metadata for every file", extra={"kv": {"path": str(manifest_path)}})

    entries: list[ManifestEntry] = []
    for f in discover_files(raw_dir):
        rel = f.relative_to(raw_dir).as_posix()
        if rel in listed:
            entries.append(listed.pop(rel))
        else:
            log.warning("file not in manifest; using guessed metadata", extra={"kv": {"path": rel}})
            entries.append(guess_entry(raw_dir, f))
    for missing in listed:
        log.warning("manifest entry has no file on disk; skipped", extra={"kv": {"path": missing}})
    return entries
