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
        res = await server.mcp.call_tool(name, kw)
    except ToolError as e:
        return str(e)
    content = getattr(res, "content", None)
    return content[0].text if content else str(res)  # the real text, so tests can look at lines


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


# ---- sorting
from urllib.parse import parse_qs  # noqa: E402

import httpx  # noqa: E402


class SortingSite(FakeSite):
    """Like the real site: a /result post with sortby=X answers with the page whose sort select shows X."""

    def __call__(self, req: httpx.Request) -> httpx.Response:
        resp = super().__call__(req)
        if req.method == "POST" and str(req.url).endswith("/result"):
            sort = parse_qs(req.content.decode(), keep_blank_values=True).get("sortby", [""])[0]
            html = resp.text.replace('<option value="-.rule3" selected>', '<option value="-.rule3">')
            html = html.replace(f'<option value="{sort}">', f'<option value="{sort}" selected>')
            return httpx.Response(200, text=html)
        return resp


def result_posts(site):
    return [parse_qs(b, keep_blank_values=True) for m, u, b in site.bodies if m == "POST" and u.endswith("/result")]


def test_resolve_sort():
    assert options.resolve_sort("") == ""
    assert options.resolve_sort("year_desc") == "-yr"
    assert options.resolve_sort("cited_desc") == "-.total_bequote"
    assert options.resolve_sort("-yr") == "-yr"
    with pytest.raises(NdltdError, match="排序"):
        options.resolve_sort("random")


async def test_default_sort_costs_no_extra_request():
    site = SortingSite()
    c = make(site)
    await c.search_html("微積分")
    assert result_posts(site) == []


async def test_sort_adds_one_request_and_keeps_the_rest_of_the_form():
    site = SortingSite()
    c = make(site)
    await c.search_html("微積分", sort="-yr")
    posts = result_posts(site)
    assert len(posts) == 1 and posts[0]["sortby"] == ["-yr"]
    assert posts[0]["_status_"] == ["searchresult"] and "jpsize" in posts[0] and "jumpfmt1page" not in posts[0]


async def test_sort_is_kept_when_jumping_to_page_two():
    site = SortingSite()
    c = make(site)
    await c.search_html("微積分", page=2, sort="-yr")
    posts = result_posts(site)
    assert len(posts) == 2
    assert posts[1]["jmpage"] == ["2"] and posts[1]["sortby"] == ["-yr"]  # the sorted set, not the default one


async def test_record_by_index_uses_the_same_sort():
    site = SortingSite()
    c = make(site)
    await c.record_html("微積分", 21, sort="-yr")
    posts = result_posts(site)
    assert [p.get("sortby") for p in posts] == [["-yr"], ["-yr"]]
    assert site.log[-1][1].endswith("/record?r1=21&h1=1")


async def test_sort_tool_output_and_hints(monkeypatch):
    site = SortingSite()
    monkeypatch.setattr(server, "_client", make(site))
    out = await call("ndltd_search", query="微積分", sort="year_desc")
    assert "排序：year_desc" in out
    assert "不認得的排序" in await call("ndltd_search", query="微積分", sort="random")
    assert "請核對篇名" in await call("ndltd_abstract", query="微積分", index=3, sort="year_desc")
    assert any(p.get("sortby") == ["-yr"] for p in result_posts(site))


# ---- review follow-ups: default sort, empty results, expected_title, one-line notes
def test_resolve_sort_normalises_the_site_default_and_knows_downloads_asc():
    assert options.resolve_sort("relevance") == "" and options.resolve_sort("-.rule3") == ""
    assert options.resolve_sort("downloads_asc") == ".total_fulltext"


async def test_explicit_default_sort_costs_no_extra_request():
    site = SortingSite()
    await make(site).search_html("微積分", sort=options.resolve_sort("relevance"))
    assert result_posts(site) == []


async def test_sort_equal_to_the_pages_current_sort_is_not_posted_again():
    site = SortingSite()
    await make(site).search_html("微積分", sort="-.rule3")  # the fixture page already shows this sort
    assert result_posts(site) == []


class EmptySite(FakeSite):
    def __call__(self, req: httpx.Request) -> httpx.Response:
        if req.method == "POST" and str(req.url).endswith("/search"):
            self.log.append((req.method, str(req.url)))
            return httpx.Response(200, text="<html><body>檢索結果共 0 筆資料</body></html>")
        return super().__call__(req)


async def test_zero_results_with_sort_or_paging_make_no_further_requests():
    site = EmptySite()
    html = await make(site).search_html("沒有這個詞", page=2, sort="-yr")
    assert parsers.parse_search(html).total == 0
    assert [u for m, u in site.log if "ccd=ABC999" in u] == ["https://ndltd.ncl.edu.tw/cgi-bin/gs32/gsweb.cgi/ccd=ABC999/search"]


def test_result_total():
    assert parsers.result_total("檢索結果共 <span>&nbsp;1,234&nbsp;</span>筆資料") == 1234
    assert parsers.result_total("hello") is None


async def test_expected_title_blocks_a_wrong_record(monkeypatch):
    site = SortingSite()
    monkeypatch.setattr(server, "_client", make(site))
    bad = await call("ndltd_abstract", query="微積分", index=3, expected_title="完全不同的篇名")
    assert "篇名不符" in bad and "sort" in bad
    ok = await call("ndltd_abstract", query="微積分", index=3,
                    expected_title="大一學生 微積分學習策略與微積分學習成效之研究")  # spacing differences are tolerated
    assert "楊嘉勝" in ok and "篇名不符" not in ok
    assert "篇名不符" in await call("ndltd_cite", query="微積分", index=3, expected_title="別篇", format="ris")
    assert "篇名不符" in await call("ndltd_abstract", thesis_id="099NCTU5507087", expected_title="別篇")


class NoIdSite(FakeSite):
    """Like the guest list for embargoed theses: the rows carry no thesis id (search and result pages alike)."""

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if req.method == "POST" and (str(req.url).endswith("/search") or str(req.url).endswith("/result")):
            self.log.append((req.method, str(req.url)))
            html = load("search_one_result.html").replace("099NCTU5507087", "xx").replace("dbid=", "nodb=")
            html = html.replace("id%3D%22xx%22", "nothing")
            return httpx.Response(200, text=html)
        return super().__call__(req)


async def test_search_hint_for_rows_without_id_carries_expected_title(monkeypatch):
    monkeypatch.setattr(server, "_client", make(NoIdSite()))
    out = await call("ndltd_search", query="微積分", compact=False)
    assert 'ndltd_abstract(query="微積分", index=1, expected_title="大一學生微積分學習策略與微積分學習成效之研究")' in out


async def test_provenance_note_is_one_line_even_if_the_query_has_newlines(monkeypatch):
    monkeypatch.setattr(server, "_client", make(SortingSite()))
    out = await call("ndltd_cite", query="微積分\n學習  策略", index=3, format="ris")
    ris_text = out[out.index("TY  - THES"):]
    assert "N1  - 依搜尋「微積分 學習 策略」第 3 筆取得，請核對篇名" in ris_text
    assert all(l[:5] in ("TY  -", "AU  -", "TI  -", "PY  -", "PB  -", "M3  -", "DB  -", "LA  -", "KW  -", "N1  -", "UR  -", "ER  -")
               for l in ris_text.splitlines() if l.strip())


def test_result_total_reads_the_real_pages():
    assert parsers.result_total(load("search_one_result.html")) == 1


# ---- sort is not offered over 2000 hits
import re  # noqa: E402


def test_current_sort_reads_the_selected_option():
    assert parsers.current_sort(load("search_one_result.html")) == "-.rule3"
    no_select = re.sub(r"<select name=sortby.*?</select>", "", load("search_one_result.html"), flags=re.S)
    assert parsers.current_sort(no_select) is None


class OverLimitSite(FakeSite):
    """Result pages without the sort control, as the site serves them for more than 2000 hits."""

    def __call__(self, req: httpx.Request) -> httpx.Response:
        resp = super().__call__(req)
        if req.method == "POST" and (str(req.url).endswith("/search") or str(req.url).endswith("/result")):
            html = re.sub(r"<select name=sortby.*?</select>", "", resp.text, flags=re.S)
            html = html.replace('<input type=hidden name="sortby"', '<input type=hidden name="x_sortby"')
            return httpx.Response(200, text=html)
        return resp


async def test_over_limit_results_send_no_sort_request_and_say_so(monkeypatch):
    site = OverLimitSite()
    monkeypatch.setattr(server, "_client", make(site))
    out = await call("ndltd_search", query="人工智慧", sort="year_desc")
    assert "排序沒有套用" in out and "2000" in out
    assert result_posts(site) == []  # nothing was posted to sort


async def test_applied_sort_does_not_carry_the_warning(monkeypatch):
    monkeypatch.setattr(server, "_client", make(SortingSite()))
    out = await call("ndltd_search", query="微積分", sort="year_desc")
    assert "排序：year_desc" in out and "排序沒有套用" not in out


# ---- per_page
class PagedSite(SortingSite):
    """Echoes the chosen page size like the real result page does: the size only changes when the post carries
    SubmitChangePage=1 (measured live: without that hidden field the site ignores jpsize)."""

    def __call__(self, req: httpx.Request) -> httpx.Response:
        resp = super().__call__(req)
        if req.method == "POST" and str(req.url).endswith("/result"):
            q = parse_qs(req.content.decode(), keep_blank_values=True)
            size = q.get("jpsize", [""])[0]
            if q.get("SubmitChangePage", [""])[0] == "1":
                html = resp.text.replace('<option value="20" selected>', '<option value="20">')
                html = html.replace(f'<option value="{size}">', f'<option value="{size}" selected>')
                return httpx.Response(200, text=html)
        return resp


def test_resolve_per_page():
    assert options.resolve_per_page(0) == 0 and options.resolve_per_page(50) == 50
    for bad in (7, 200, -1):
        with pytest.raises(NdltdError, match="per_page"):
            options.resolve_per_page(bad)


async def test_default_page_size_costs_nothing_and_the_site_default_is_not_posted_again():
    site = PagedSite()
    await make(site).search_html("微積分")
    assert result_posts(site) == []
    await make(site).search_html("微積分", per_page=20)  # the fixture page already shows 20
    assert result_posts(site) == []


async def test_changing_the_page_size_is_one_extra_post_and_merges_with_sort():
    site = PagedSite()
    await make(site).search_html("微積分", per_page=50)
    posts = result_posts(site)
    assert len(posts) == 1 and posts[0]["jpsize"] == ["50"] and "jumpfmt1page" not in posts[0]
    assert posts[0]["SubmitChangePage"] == ["1"]  # the field the real page sets when its size select changes
    site = PagedSite()
    await make(site).search_html("微積分", per_page=100, sort="-yr")
    posts = result_posts(site)
    assert len(posts) == 1 and posts[0]["jpsize"] == ["100"] and posts[0]["sortby"] == ["-yr"]  # one post, both changes
    assert posts[0]["SubmitChangePage"] == ["1"]


async def test_paging_with_a_new_size_keeps_the_size():
    site = PagedSite()
    await make(site).search_html("微積分", page=3, per_page=50, sort="-yr")
    posts = result_posts(site)
    assert len(posts) == 2
    assert posts[1]["jmpage"] == ["3"] and posts[1]["jpsize"] == ["50"] and posts[1]["sortby"] == ["-yr"]


def jumps(site):
    """Pages the client actually jumped to (a jump is the post that carries the 跳至 button)."""
    return [p["jmpage"][0] for p in result_posts(site) if "jumpfmt1page" in p]


async def test_record_lookup_uses_the_real_page_size_not_a_fixed_20():
    site = PagedSite()
    await make(site).record_html("微積分", 21)  # fixture page size 20 -> page 2
    assert jumps(site) == ["2"]
    site = PagedSite()
    await make(site).record_html("微積分", 21, per_page=50)  # size 50 -> index 21 is on page 1: no jump
    assert jumps(site) == [] and result_posts(site)[0]["jpsize"] == ["50"]
    site = PagedSite()
    await make(site).record_html("微積分", 120, per_page=50)  # 120 -> page 3 of 50
    assert jumps(site) == ["3"]
    assert site.log[-1][1].endswith("/record?r1=120&h1=1")


async def test_per_page_in_the_tools(monkeypatch):
    site = PagedSite()
    monkeypatch.setattr(server, "_client", make(site))
    out = await call("ndltd_search", query="微積分", per_page=50)
    assert "每頁 50 筆" in out and "沒有套用" not in out
    assert "per_page 只能是" in await call("ndltd_search", query="微積分", per_page=7)
    assert "請核對篇名" in await call("ndltd_abstract", query="微積分", index=3, per_page=50)
    assert any(p.get("jpsize") == ["50"] for p in result_posts(site))


async def test_sort_alone_does_not_set_the_size_change_flag():
    site = PagedSite()
    await make(site).search_html("微積分", sort="-yr")
    assert result_posts(site)[0].get("SubmitChangePage") == [""]  # unchanged from the page: only the sort differs


def test_current_page_size_reads_the_selected_option():
    assert parsers.current_page_size(load("search_one_result.html")) == 20
    assert parsers.current_page_size("<html><body>no select</body></html>") is None


async def test_header_tells_the_truth_when_the_site_ignored_the_page_size(monkeypatch):
    monkeypatch.setattr(server, "_client", make(SortingSite()))  # this site never changes the page size
    out = await call("ndltd_search", query="微積分", per_page=50)
    assert "每頁 20 筆（你要求 50，但站方沒有套用）" in out
    out = await call("ndltd_search", query="微積分")
    assert "每頁" not in out.splitlines()[0]  # nothing requested, nothing claimed


async def test_no_size_note_for_an_empty_result(monkeypatch):
    monkeypatch.setattr(server, "_client", make(EmptySite()))
    assert "每頁" not in await call("ndltd_search", query="沒有這個詞", per_page=50)


# ---- compact output (the default) and the extra columns
async def test_compact_is_the_default_one_line_per_row_with_a_legend(monkeypatch):
    monkeypatch.setattr(server, "_client", make(SortingSite()))
    out = await call("ndltd_search", query="微積分")
    lines = out.splitlines()
    assert lines[1].startswith("欄位：序號｜篇名｜研究生｜指導教授｜學校 系所 學年度 學位｜學門／學類｜被引用/點閱/下載/收藏｜論文編號")
    assert lines[2] == ("1｜大一學生微積分學習策略與微積分學習成效之研究｜楊嘉勝｜白啟光｜國立交通大學 應用數學系所 99 碩士｜"
                        "數學及統計學門／數學學類｜15/1062/197/1｜099NCTU5507087")
    assert len(lines) == 3  # header, legend, one row: no footer when every row has an id


async def test_compact_writes_the_no_id_hint_once_and_marks_the_rows(monkeypatch):
    site = NoIdSite()
    monkeypatch.setattr(server, "_client", make(site))
    out = await call("ndltd_search", query="微積分", sort="year_desc", per_page=20)
    row = [l for l in out.splitlines() if l.startswith("1｜")][0]
    assert row.endswith("｜（無編號）")
    assert out.count("expected_title=<該筆的篇名>") == 1
    assert 'ndltd_abstract／ndltd_cite(query="微積分", index=<序號>, sort="year_desc", per_page=20, expected_title=' in out


async def test_long_format_is_still_available_and_has_the_new_fields(monkeypatch):
    monkeypatch.setattr(server, "_client", make(SortingSite()))
    out = await call("ndltd_search", query="微積分", compact=False)
    assert "學門：數學及統計學門／數學學類｜論文種類：學術論文" in out
    assert "被引用：15｜點閱：1062｜下載：197｜書目收藏：1" in out
    assert "欄位：序號" not in out


def test_cell_and_short_note_helpers():
    assert server._cell("a｜b\n  c") == "a/b c" and server._cell(None) == "" and server._cell(0) == "0"
    assert server._short_fulltext("網際網路公開日期：20290609") == "；20290609公開"
    assert server._short_fulltext("本篇電子全文限研究生所屬學校校內系統及IP範圍內開放") == "；限校內"
    assert server._short_fulltext("") == ""
