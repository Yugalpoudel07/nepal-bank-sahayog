"""Fiscal-year validity, directive editions, circular amendments and old-vs-new rule selection."""

from __future__ import annotations

from app.section_detect import SectionRef
from app.store import is_valid_for
from ingest.linker import link


def _chunk(cid: str, doc_type: str, fy: str | None, *, key: str | None = None, refs: list[str] | None = None,
           amends: list[str] | None = None, amends_key: str | None = None, title: str = "t") -> dict:
    return {
        "chunk_id": cid, "doc_type": doc_type, "title": cid, "section_no": "2.1", "section_title": title,
        "unit_kind": "section", "sub": 0, "act_key": key, "amends_key": amends_key, "family": "abc",
        "effective_from_fy": fy, "effective_to_fy": None, "refs": refs or [], "amends": amends or [],
        "stated_amended_by": [],
    }


def test_link_editions_and_circulars() -> None:
    old = _chunk("ud81", "directive", "2081/82", key="ud_abc_2081", refs=["ud_abc_2081 D.2", "ud_abc_2081 D.2.1"],
                 title="कर्जा वर्गीकरण")
    new = _chunk("ud82", "directive", "2082/83", key="ud_abc_2082", refs=["ud_abc_2082 D.2", "ud_abc_2082 D.2.1"],
                 title="कर्जा वर्गीकरण")
    other_point = _chunk("ud82p2", "directive", "2082/83", key="ud_abc_2082",
                         refs=["ud_abc_2082 D.2", "ud_abc_2082 D.2.2"], title="अन्य")
    circ81 = _chunk("c81", "circular", "2081/82", amends_key="ud_abc_2081", amends=["ud_abc_2081 D.2"])
    circ83 = _chunk("c83", "circular", "2083/84", amends_key="ud_abc_2082",
                    amends=["ud_abc_2082 D.2", "ud_abc_2082 D.2.1"])
    act = _chunk("act", "act", None)
    link([old, new, other_point, circ81, circ83, act])

    assert old["effective_to_fy"] == "2082/83"  # in force until the next edition (overlap year)
    assert new["effective_to_fy"] is None
    assert circ81["effective_to_fy"] == "2082/83"  # consolidated into the 2082 edition
    assert circ83["effective_to_fy"] is None
    assert new["supersedes"] == ["ud81"] and old["superseded_by"] == ["ud82"]
    # the 2083/84 circular names point 2.1: it is linked to that point only, not to every point of directive 2
    assert new["amended_by_ids"] == ["c83"] and other_point["amended_by_ids"] == []
    assert old["amended_by_ids"] == ["c81"]

    assert is_valid_for(act, 2070)  # Acts are always eligible
    assert is_valid_for(old, 2081) and is_valid_for(old, 2082) and not is_valid_for(old, 2083)
    assert not is_valid_for(new, 2081) and is_valid_for(new, 2090)


def _ids(hits, status: str) -> set[str]:
    return {h.id for h in hits if h.status == status}


def test_old_vs_new_edition_selection(engine) -> None:
    ref = [SectionRef(None, "D", "2.2")]
    old = "directives_sample_ud_abc_2081::d2p2"
    new = "directives_sample_ud_abc_2082::d2p2"

    y81 = engine.retriever.retrieve("loan loss provision", 2081, ref)
    assert old in _ids(y81, "current") and new not in {h.id for h in y81}  # future edition filtered out

    y82 = engine.retriever.retrieve("loan loss provision", 2082, ref)
    assert new in _ids(y82, "current") and old in _ids(y82, "overridden")  # overlap year: newer edition wins

    y83 = engine.retriever.retrieve("loan loss provision", 2083, ref)
    assert new in _ids(y83, "current") and old in _ids(y83, "previous_version")


def test_amending_circular_is_pulled_in(engine) -> None:
    hits = engine.retriever.retrieve("branch opening approval", 2083, [SectionRef(None, "D", "3.1")])
    circ = [h for h in hits if h.chunk["doc_type"] == "circular"]
    assert circ, "the 2083/84 circular amending directive 3 point 1 must be in the context"
    before = engine.retriever.retrieve("branch opening approval", 2082, [SectionRef(None, "D", "3.1")])
    assert not [h for h in before if h.chunk["doc_type"] == "circular"]  # not yet issued in 2082/83


def test_resolve_fy_warning_for_missing_year(engine) -> None:
    res = engine.resolve_fy("What was the provision rule in 2079/80?", None, "en")
    assert res.requested == "2079/80" and res.used == "2083/84" and res.warning
    ok = engine.resolve_fy("provision rule", "2082/83", "en")
    assert ok.used == "2082/83" and ok.source == "ui" and ok.warning is None
