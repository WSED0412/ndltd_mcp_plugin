"""Experimental member-only export: preview parser, the request sequence and its safety checks, the gated tool."""
import importlib
import re

import httpx
import pytest

from ndltd_mcp import parsers
from ndltd_mcp.client import ExportError
from ndltd_mcp.errors import NdltdParseError
from test_client import FakeSite, make

TITLES = ["微積分入門", "線性代數與微積分", "微積分教學研究", "分數階微積分", "微積分學習成效"]


def results_page(n=5, total=185, member=True):
    rows = ""
    for i in range(1, n + 1):
        rows += f"""<tr><td class="tdfmt1-first"></td><td class="tdfmt1-second">{i}.</td>
        <td class="tdfmt1-content"><table><tr><td class="std2"><a href="/x/record?r1={i}&h1=1" class="slink">{TITLES[i - 1]}</a></td></tr>
        <tr><td class="std2">某大學／某系／11{i}／碩士／甲門／乙類</td></tr>
        <tr><td class="std2">研究生:作者{i}</td></tr><tr><td class="std2">指導教授:老師{i}</td></tr>
        <a onclick="window.open('/x/nclcdrbequote?dbid=11{i}ABCD0000{i}')">0</a></table></td></tr>"""
    cfield = '<input type=radio name="cfield" value="2">' if member else ""
    return f"""<html><body>檢索結果共<span class="etd_e">&nbsp;{total}&nbsp;</span>筆資料<table>{rows}</table>{cfield}
    <form name=main action="/cgi-bin/gs32/gsweb.cgi/ccd=ABC999/result"><input type=hidden name="jpsize" value="10"></form></body></html>"""


def preview_page(titles, missing_abstract=()):
    out = '<html><body><table class="previceoutput_table">'
    for i, t in enumerate(titles, 1):
        zh = "" if i in missing_abstract else f"第{i}篇的中文摘要。\n第二行"
        out += f"""<tr><td class="potd2"><b>第 {i} 筆</b></td></tr></table>
        <table class="tableoutfmt2" id="format0_disparea"><tr><th class="std1">本論文永久網址:</th><td class="std2">hdl.handle.net/11296/h{i}</td></tr>
        <tr><th class="std1">研究生:</th><td class="std2">作者{i}</td></tr>
        <tr><th class="std1">論文名稱:</th><td class="std2">{t}</td></tr>
        <tr><th class="std1">指導教授:</th><td class="std2"><a>老師甲</a><br><a>老師乙</a></td></tr>
        <tr><th class="std1">校院名稱:</th><td class="std2">某大學</td></tr></table>
        <table class="tableoutfmt2"><tr><td class="stdncl2">{zh}</td></tr></table>
        <table class="tableoutfmt2"><tr><td class="stdncl2">English abstract {i}</td></tr></table>
        <table class="tableoutfmt2"><tr><td class="stdncl2">第一章 緒論 {i}</td></tr></table>
        <table class="tableoutfmt2"><tr><td class="stdncl2">參考文獻條目 {i}</td></tr></table>
        <table class="tableoutfmt2"><tr><td></td></tr></table>
        <table class="tableoutfmt2"><tr><td>國圖紙本論文</td></tr></table>
        <table class="previceoutput_table"><tr><td class="x"></td></tr>"""
    return out + "</table></body></html>"


def checker_body(n):
    return f'<input type=hidden name="stenownum" id="stenownum" value="{n}"> <input type=hidden name="stetotalnum" id="stetotalnum" value="{n}">'


class ExportSite(FakeSite):
    """Member site: a search, tick bookkeeping like the real getchecker, and the preview of the ticked rows."""

    def __init__(self, n=5, total=185, member=True, tick_lie=None, preview_titles=None):
        super().__init__()
        self.n, self.total, self.member, self.tick_lie = n, total, member, tick_lie
        self.preview_titles = preview_titles
        self.ticked = 0

    def __call__(self, req: httpx.Request) -> httpx.Response:
        u = str(req.url)
        if req.method == "POST" and u.endswith("/search"):
            self.log.append((req.method, u))
            self.bodies.append((req.method, u, req.content.decode()))
            return httpx.Response(200, text=results_page(self.n, self.total, self.member))
        if "/getchecker?" in u:
            self.log.append((req.method, u))
            q = req.url.params
            if "out" in q:
                self.ticked = 0
            else:
                a, b = q["in"].split("-")
                self.ticked = int(b) - int(a) + 1
            n = self.tick_lie if (self.tick_lie is not None and "in" in q) else self.ticked
            return httpx.Response(200, text=checker_body(n))
        if "/previewsave?" in u:
            self.log.append((req.method, u))
            titles = self.preview_titles if self.preview_titles is not None else TITLES[: self.ticked]
            return httpx.Response(200, text=preview_page(titles))
        return super().__call__(req)


def calls(site):
    return [re.sub(r".*ccd=ABC999", "", u).replace("&cache=", "|").split("|")[0] for m, u in site.log if "ccd=ABC999" in u]


# ---- parsers
def test_parse_preview_reads_every_record_in_order():
    recs = parsers.parse_preview(preview_page(TITLES[:3]))
    assert [r.fields["論文名稱"] for r in recs] == TITLES[:3]
    r = recs[1]
    assert r.fields["研究生"] == "作者2" and r.fields["指導教授"] == "老師甲、老師乙" and r.handle == "11296/h2"
    assert r.abstract_zh == "第2篇的中文摘要。\n第二行" and r.abstract_en == "English abstract 2"
    assert r.toc == "第一章 緒論 2" and r.references == "參考文獻條目 2"


def test_parse_preview_keeps_blocks_aligned_when_an_abstract_is_empty():
    recs = parsers.parse_preview(preview_page(TITLES[:3], missing_abstract=(2,)))
    assert recs[1].abstract_zh == "" and recs[1].abstract_en == "English abstract 2" and recs[1].references == "參考文獻條目 2"
    assert recs[2].abstract_zh.startswith("第3篇")


def test_parse_preview_rejects_unexpected_structure():
    with pytest.raises(NdltdParseError, match="區塊"):
        parsers.parse_preview('<html><body><b>第 1 筆</b><table class="tableoutfmt2"><tr><th class="std1">論文名稱:</th><td>x</td></tr></table></body></html>')
    assert parsers.parse_preview("<html><body>nothing</body></html>") == []


def test_has_full_export_and_checker_count():
    assert parsers.has_full_export(results_page(member=True)) and not parsers.has_full_export(results_page(member=False))
    assert parsers.checker_count(checker_body(7)) == 7 and parsers.checker_count("x") is None


# ---- client sequence and safety
async def test_export_full_happy_path_clears_before_and_after_and_checks_counts():
    site = ExportSite()
    res, pairs = await make(site).export_full("微積分", 1, ("ti", "kw"), "es0", "", 20)
    assert [r.title for r, _ in pairs] == TITLES and res.total == 185
    assert calls(site) == ["/search", "/getchecker?out=1-185", "/getchecker?in=1-5",
                           "/previewsave?preview=1&crange=1&cfield=2&outfmt=htm", "/getchecker?out=1-185"]
    assert site.ticked == 0  # the member's session is left as it was found


async def test_export_full_respects_the_record_limit():
    site = ExportSite(n=5)
    res, pairs = await make(site).export_full("微積分", 1, ("ti", "kw"), "es0", "", 2)
    assert len(pairs) == 2 and "/getchecker?in=1-2" in calls(site)


async def test_not_a_member_stops_before_touching_the_selection():
    site = ExportSite(member=False)
    with pytest.raises(ExportError, match="登入"):
        await make(site).export_full("微積分", 1, ("ti", "kw"), "es0", "", 20)
    assert calls(site) == ["/search"]


async def test_wrong_tick_count_aborts_and_still_clears():
    site = ExportSite(tick_lie=3)
    with pytest.raises(ExportError, match="不符"):
        await make(site).export_full("微積分", 1, ("ti", "kw"), "es0", "", 20)
    assert not any("previewsave" in c for c in calls(site))
    assert calls(site)[-1] == "/getchecker?out=1-185"


async def test_preview_with_the_wrong_number_of_records_aborts():
    site = ExportSite(preview_titles=TITLES[:3])
    with pytest.raises(ExportError, match="預覽回傳 3 筆"):
        await make(site).export_full("微積分", 1, ("ti", "kw"), "es0", "", 20)
    assert calls(site)[-1] == "/getchecker?out=1-185"


# ---- the gated tool
@pytest.fixture
def export_server(monkeypatch):
    from ndltd_mcp import server

    monkeypatch.setenv("NDLTD_EXPERIMENTAL_EXPORT", "1")
    srv = importlib.reload(server)
    yield srv
    monkeypatch.delenv("NDLTD_EXPERIMENTAL_EXPORT", raising=False)
    importlib.reload(server)


async def tool(srv, **kw):
    from mcp.server.mcpserver.exceptions import ToolError

    try:
        return str(await srv.mcp.call_tool("ndltd_export_full", kw))
    except ToolError as e:
        return str(e)


async def test_tool_is_absent_by_default():
    from ndltd_mcp import server

    assert "ndltd_export_full" not in [t.name for t in await server.mcp.list_tools()]


async def test_tool_needs_explicit_confirmation_and_sends_nothing_without_it(export_server):
    site = ExportSite()
    export_server._client = make(site)
    out = await tool(export_server, query="微積分")
    assert "confirm=True" in out and site.log == []


async def test_tool_returns_abstracts_and_checks_titles(export_server):
    site = ExportSite()
    export_server._client = make(site)
    out = await tool(export_server, query="微積分", max_records=3, confirm=True)
    assert "實驗性會員匯出" in out and "共 185 筆" in out and "本次取得 3 筆" in out
    assert "第2篇的中文摘要" in out and "English abstract 3" in out
    assert "【目次】" not in out and "【參考文獻】" not in out
    out = await tool(export_server, query="微積分", max_records=2, include_toc=True, include_references=True, confirm=True)
    assert "【目次】" in out and "參考文獻條目 2" in out


async def test_tool_aborts_when_a_title_does_not_match(export_server):
    site = ExportSite(preview_titles=["別的論文", "線性代數與微積分", "微積分教學研究", "分數階微積分", "微積分學習成效"])
    export_server._client = make(site)
    out = await tool(export_server, query="微積分", confirm=True)
    assert "不符" in out and "別的論文" in out


async def test_tool_validates_arguments(export_server):
    export_server._client = make(ExportSite())
    assert "max_records" in await tool(export_server, query="微積分", max_records=21, confirm=True)
    assert "query" in await tool(export_server, query=" ", confirm=True)


# ---- review follow-ups: stop means stop, no cache= parameter, suspicious block alignment
from test_client import BLANK  # noqa: E402


class StopSite(ExportSite):
    """The site refuses (blank page / captcha) when the preview is requested."""

    def __init__(self, page_text, **kw):
        super().__init__(**kw)
        self.page_text = page_text

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if "/previewsave?" in str(req.url):
            self.log.append((req.method, str(req.url)))
            return httpx.Response(200, text=self.page_text)
        return super().__call__(req)


async def test_blank_page_during_the_preview_stops_everything_including_the_cleanup():
    from ndltd_mcp.errors import NdltdBlockedError

    site = StopSite(BLANK)
    with pytest.raises(NdltdBlockedError):
        await make(site).export_full("微積分", 1, ("ti", "kw"), "es0", "", 20)
    assert calls(site)[-1] == "/previewsave?preview=1&crange=1&cfield=2&outfmt=htm"  # nothing sent afterwards


async def test_blank_page_during_the_selection_stops_everything_too():
    from ndltd_mcp.errors import NdltdBlockedError

    class BlankOnTick(ExportSite):
        def __call__(self, req):
            if "/getchecker?in=" in str(req.url):
                self.log.append((req.method, str(req.url)))
                return httpx.Response(200, text=BLANK)
            return super().__call__(req)

    site = BlankOnTick()
    with pytest.raises(NdltdBlockedError):
        await make(site).export_full("微積分", 1, ("ti", "kw"), "es0", "", 20)
    assert calls(site)[-1] == "/getchecker?in=1-5"  # no cleanup request after the refusal


async def test_a_parse_error_still_cleans_up_because_the_site_answered_normally():
    site = StopSite("<html><body><b>第 1 筆</b></body></html>")  # an answer, just not what we expect
    with pytest.raises(NdltdParseError):
        await make(site).export_full("微積分", 1, ("ti", "kw"), "es0", "", 20)
    assert calls(site)[-1] == "/getchecker?out=1-185"
    assert site.ticked == 0


async def test_export_requests_carry_no_cache_parameter():
    site = ExportSite()
    await make(site).export_full("微積分", 1, ("ti", "kw"), "es0", "", 20)
    assert not any("cache=" in u for _, u in site.log)


def test_parse_preview_warns_when_the_blocks_plainly_do_not_fit():
    page = preview_page(TITLES[:1])
    # simulate a shift: the Chinese-abstract slot holds English text, the English slot holds Chinese
    shifted = page.replace("第1篇的中文摘要。\n第二行", "English only text").replace("English abstract 1", "這是中文")
    rec = parsers.parse_preview(shifted)[0]
    assert any("中文摘要區塊不含中文" in n for n in rec.notes) and any("外文摘要區塊以中文為主" in n for n in rec.notes)
    assert parsers.parse_preview(page)[0].notes == []


async def test_tool_output_shows_the_alignment_note(export_server):
    site = ExportSite(preview_titles=None)
    site.__class__ = type("Shifted", (ExportSite,), {
        "__call__": lambda self, req: (
            httpx.Response(200, text=preview_page(TITLES[: self.ticked]).replace("第1篇的中文摘要。\n第二行", "English only"))
            if "/previewsave?" in str(req.url) and not self.log.append((req.method, str(req.url))) else ExportSite.__call__(self, req)),
    })
    export_server._client = make(site)
    out = await tool(export_server, query="微積分", max_records=1, confirm=True)
    assert "區塊對齊可疑" in out
