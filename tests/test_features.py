"""Options, citations, toc/references, and the tools that expose them (all offline)."""
import pytest

from ndltd_mcp import cite, options, parsers, server
from ndltd_mcp.errors import NdltdError
from test_client import FakeSite, make
from test_parsers import load


def detail():
    return parsers.parse_detail(load("detail_099NCTU5507087.html"))


# ---- options
def test_resolve_fields_and_match():
    assert options.resolve_fields("title,keyword") == ("ti", "kw")
    assert options.resolve_fields("advisor") == ("ad",)
    assert options.resolve_fields("author，abstract") == ("au", "ab")
    assert options.resolve_fields("ti kw") == ("ti", "kw")
    assert len(options.resolve_fields("all")) == 7
    assert options.resolve_match("exact") == "es0" and options.resolve_match("synonym") == "es3"
    with pytest.raises(NdltdError):
        options.resolve_fields("nonsense")
    with pytest.raises(NdltdError):
        options.resolve_match("regex")


# ---- toc / references
def test_toc_and_references_are_parsed_and_optional():
    d = detail()
    assert d.toc.startswith("第一章 緒論") and "第五章 結論與建議" in d.toc
    assert "丁慕玉（2008）" in d.references
    plain = parsers.format_detail(d, "099NCTU5507087")
    assert "【目次】" not in plain and "【參考文獻】" not in plain
    full = parsers.format_detail(d, "099NCTU5507087", include_toc=True, include_references=True)
    assert "【目次】" in full and "【參考文獻】" in full and "丁慕玉（2008）" in full


# ---- citations
def test_apa_chinese_thesis():
    assert cite.apa7(detail()) == (
        "楊嘉勝（2011）。*大一學生微積分學習策略與微積分學習成效之研究*〔碩士論文，國立交通大學〕。"
        "臺灣博碩士論文知識加值系統。https://hdl.handle.net/11296/gnew8c"
    )


def test_apa_refuses_foreign_author_but_allows_english_title_and_missing_url():
    d = detail()
    d.fields["研究生"] = "Yang, Jia-Sheng"
    with pytest.raises(NdltdError, match="bibtex"):
        cite.apa7(d)
    d = detail()  # Chinese author, English title: perfectly normal for a Taiwanese thesis
    d.fields["論文名稱"] = "A Study of Calculus Learning"
    assert cite.apa7(d).startswith("楊嘉勝（2011）。*A Study of Calculus Learning*〔碩士論文，國立交通大學〕。")
    d = detail()
    d.handle = None  # no permanent URL: the reference simply ends after the database name
    assert cite.apa7(d).endswith("臺灣博碩士論文知識加值系統。")


def test_bibtex_and_ris():
    d = detail()
    b = cite.bibtex(d, "099NCTU5507087")
    assert b.startswith("@mastersthesis{ndltdgnew8c,")
    for needle in ("author = {楊嘉勝}", "school = {國立交通大學}", "year = {2011}", "url = {https://hdl.handle.net/11296/gnew8c}",
                   "指導教授：白啟光"):
        assert needle in b
    r = cite.ris(d, "099NCTU5507087")
    assert r.splitlines()[0] == "TY  - THES" and r.splitlines()[-1] == "ER  - "
    for needle in ("AU  - 楊嘉勝", "PY  - 2011///", "PB  - 國立交通大學", "KW  - 微積分學習策略", "KW  - Calculus Achievement",
                   "UR  - https://hdl.handle.net/11296/gnew8c"):
        assert needle in r


def test_bibtex_escapes_and_degree_types():
    d = detail()
    d.fields["論文名稱"] = "A & B_100% {x}"
    d.fields["論文名稱"] = "A & B_100% {x} O(N^2) ~5 C\\C"
    t = [l for l in cite.bibtex(d).splitlines() if l.strip().startswith("title")][0].rstrip(",")
    assert t == r"  title = {{A \& B\_100\% \{x\} O(N\textasciicircum{}2) \textasciitilde{}5 C\textbackslash{}C}}"
    d = detail()
    d.fields["學位類別"] = "博士"
    assert cite.bibtex(d).startswith("@phdthesis{")
    assert "〔博士論文，" in cite.apa7(d)


def test_cite_unknown_format_and_missing_year():
    d = detail()
    with pytest.raises(NdltdError, match="格式"):
        cite.make("endnote", d)
    del d.fields["論文出版年"]
    with pytest.raises(NdltdError, match="出版年"):
        cite.apa7(d)


# ---- tools
@pytest.fixture
def fake(monkeypatch):
    site = FakeSite()
    monkeypatch.setattr(server, "_client", make(site))
    return site


async def call(name, **kw):
    from mcp.server.mcpserver.exceptions import ToolError

    try:
        return str(await server.mcp.call_tool(name, kw))
    except ToolError as e:
        return str(e)


async def test_search_sends_chosen_fields_and_match(fake):
    out = await call("ndltd_search", query="蔡炎龍", fields="advisor", match="fuzzy")
    assert "欄位：指導教授｜比對：fuzzy" in out
    post = next(b for m, u, b in fake.bodies if u.endswith("/search") and m == "POST")
    assert "dcf=ad" in post and "dcf=ti" not in post and "extrasearch=es1" in post


async def test_search_bad_option_is_a_clear_error(fake):
    assert "不認得的搜尋欄位" in await call("ndltd_search", query="x", fields="nonsense")
    assert "不認得的比對方式" in await call("ndltd_search", query="x", match="regex")


async def test_abstract_with_toc_and_references(fake):
    out = await call("ndltd_abstract", thesis_id="099NCTU5507087", include_toc=True, include_references=True)
    assert "【目次】" in out and "【參考文獻】" in out and "第一章 緒論" in out


async def test_cite_tool_by_id_and_by_index(fake):
    out = await call("ndltd_cite", thesis_id="099NCTU5507087", format="bibtex")
    assert "@mastersthesis{" in out
    out = await call("ndltd_cite", query="微積分", index=3, format="ris")
    assert "請核對篇名" in out and "TY  - THES" in out
    ris_text = out[out.index("TY  - THES"):]
    assert "N1  - 依搜尋「微積分」第 3 筆取得，請核對篇名" in ris_text
    assert "格式" in await call("ndltd_cite", thesis_id="099NCTU5507087", format="docx")


async def test_non_exact_match_carries_a_breadth_warning(fake):
    assert "非精準比對的範圍可能非常廣" in await call("ndltd_search", query="微積分", match="fuzzy")
    assert "非精準比對" not in await call("ndltd_search", query="微積分")


def test_provenance_note_never_precedes_the_record():
    d = detail()
    note = "依搜尋「微積分」第 3 筆取得，請核對篇名"
    assert cite.make("ris", d, None, note).splitlines()[0] == "TY  - THES"
    bib = cite.make("bibtex", d, None, note)
    assert bib.splitlines()[0] == "% " + note and bib.splitlines()[1].startswith("@mastersthesis{")
    assert cite.make("apa", d, None, note).startswith("（" + note + "）")


def test_keywords_are_split_for_reference_managers():
    d = detail()
    assert "keywords = {微積分學習策略, 微積分學習成效, Calculus Learning Strategies, Calculus Achievement}" in cite.bibtex(d)


def test_fields_accept_chinese_names_and_odd_separators():
    assert options.resolve_fields("論文名稱、指導教授") == ("ti", "ad")
    assert options.resolve_fields("title\u3000keyword") == ("ti", "kw")
    assert options.resolve_fields("title;abstract") == ("ti", "ab")
    assert len(options.resolve_fields("不限欄位")) == 7


def test_missing_foreign_names_do_not_print_nested_empty_parens():
    d = detail()
    del d.fields["研究生(外文)"]
    out = parsers.format_detail(d, "x")
    assert "研究生：楊嘉勝\n" in out and "（（無））" not in out
