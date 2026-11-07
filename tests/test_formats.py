"""Generated RIS / BibTeX must be readable by real parsers (skipped when the dev packages are not installed)."""
import pytest

from ndltd_mcp import cite, parsers
from test_parsers import load

NOTE = "依搜尋「微積分」第 3 筆取得，請核對篇名"


def detail():
    return parsers.parse_detail(load("detail_099NCTU5507087.html"))


def test_ris_is_parsed_by_rispy():
    rispy = pytest.importorskip("rispy")
    entries = rispy.loads(cite.ris(detail(), "099NCTU5507087", NOTE))
    assert len(entries) == 1
    e = entries[0]
    assert e["type_of_reference"] == "THES"
    assert e["authors"] == ["楊嘉勝"]
    assert e["title"] == "大一學生微積分學習策略與微積分學習成效之研究"
    assert e["publisher"] == "國立交通大學"
    assert e["keywords"] == ["微積分學習策略", "微積分學習成效", "Calculus Learning Strategies", "Calculus Achievement"]
    assert e["urls"] == ["https://hdl.handle.net/11296/gnew8c"]
    assert e["name_of_database"] == "臺灣博碩士論文知識加值系統"
    assert "指導教授：白啟光" in e["notes"] and NOTE in e["notes"]


def test_bibtex_is_parsed_by_bibtexparser_including_the_provenance_comment():
    bibtexparser = pytest.importorskip("bibtexparser")
    db = bibtexparser.loads(cite.bibtex(detail(), "099NCTU5507087", NOTE))
    assert len(db.entries) == 1  # the leading "% note" line is a comment, not an entry
    e = db.entries[0]
    assert e["ENTRYTYPE"] == "mastersthesis" and e["ID"] == "ndltdgnew8c"
    assert e["author"] == "楊嘉勝" and e["year"] == "2011" and e["school"] == "國立交通大學"
    assert e["keywords"] == "微積分學習策略, 微積分學習成效, Calculus Learning Strategies, Calculus Achievement"
    assert e["url"] == "https://hdl.handle.net/11296/gnew8c"


def test_bibtex_with_special_characters_still_parses():
    bibtexparser = pytest.importorskip("bibtexparser")
    d = detail()
    d.fields["論文名稱"] = "A & B_100% {x} O(N^2) ~5 C\\C"
    e = bibtexparser.loads(cite.bibtex(d)).entries[0]
    assert "\\&" in e["title"] and "\\_" in e["title"] and "\\textasciicircum{}" in e["title"]
