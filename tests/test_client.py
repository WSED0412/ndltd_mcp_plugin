import asyncio
import urllib.request
from urllib.parse import parse_qs

import httpx
import pytest

from ndltd_mcp import parsers
from ndltd_mcp.captcha import HumanCaptchaServer, parse_captcha_page
from ndltd_mcp.client import NdltdClient, SessionApi
from ndltd_mcp.errors import NdltdBlockedError, NdltdCaptchaRequired, NdltdError, NdltdNetworkError
from test_parsers import load

CAPTCHA_HTML = """<html><title>t</title><body>驗證碼檢查機制
<form method="get" action="/cgi-bin/gs32/gsweb.cgi"><img src="/cgi-bin/gs32/gsweb.cgi?img=1">
<input type="hidden" name="randomimg" value="R1"><input type="hidden" name="validpath" value="/tmp/x">
<input type="text" name="validinput"><input type="submit" name="check" value="確定"></form></body></html>"""
BLANK = load("blank_page.html")


def test_parse_captcha_page():
    ch = parse_captcha_page(CAPTCHA_HTML, "https://ndltd.ncl.edu.tw/cgi-bin/gs32/gsweb.cgi/login?o=dwebmge")
    assert ch.method == "GET" and ch.answer_field == "validinput"
    assert ch.action == "https://ndltd.ncl.edu.tw/cgi-bin/gs32/gsweb.cgi"
    assert ch.image_url.endswith("gsweb.cgi?img=1")
    assert ch.fields == {"randomimg": "R1", "validpath": "/tmp/x", "check": "確定"}


async def test_local_server_roundtrip():
    srv = HumanCaptchaServer(b"\x89PNGfake", open_browser=False)

    def human():
        base = srv.url
        import time
        time.sleep(0.3)
        assert urllib.request.urlopen(base).status == 200
        assert urllib.request.urlopen(base + "image.png").read() == b"\x89PNGfake"
        urllib.request.urlopen(urllib.request.Request(base + "submit", data=b"answer=abc123", method="POST"))

    t = asyncio.create_task(asyncio.to_thread(human))
    assert await srv.ask(timeout=10) == "abc123"
    await t


async def test_local_server_wrong_token_and_timeout():
    srv = HumanCaptchaServer(b"x", open_browser=False)
    assert await srv.ask(timeout=0.2) is None


class FakeSite:
    """Minimal stand-in: first hit gives captcha (or blank), then a session, search, detail."""

    total = 500  # the one-result fixture page, reporting a big result set so lookups by index are in range

    def __init__(self, mode="ok"):
        self.mode, self.log, self.answer, self.bodies = mode, [], None, []

    def page(self) -> str:
        return load("search_one_result.html").replace("&nbsp;1&nbsp;", f"&nbsp;{self.total}&nbsp;", 1)

    def __call__(self, req: httpx.Request) -> httpx.Response:
        u = str(req.url)
        self.log.append((req.method, u))
        self.bodies.append((req.method, u, req.content.decode() if req.content else ""))
        if self.mode == "blank":
            return httpx.Response(200, text=BLANK)
        if u.rstrip("/") == "https://ndltd.ncl.edu.tw":
            return httpx.Response(200, text="home")
        if u.endswith("img=1"):
            return httpx.Response(200, content=b"IMG", headers={"content-type": "image/png"})
        if "validinput=" in u:
            self.answer = req.url.params["validinput"]
            if self.answer != "good":
                return httpx.Response(200, text=CAPTCHA_HTML)
            return httpx.Response(200, text="<html>ok /cgi-bin/gs32/gsweb.cgi/ccd=NEW123/search</html>",
                                  headers={})
        if "o=dwebmge" in u and self.mode == "captcha":
            return httpx.Response(200, text=CAPTCHA_HTML)
        if "o=dwebmge" in u:
            return httpx.Response(200, text="<a href='/cgi-bin/gs32/gsweb.cgi/ccd=ABC999/search'>")
        if "/record?r1=" in u:
            return httpx.Response(200, text=load("detail_099NCTU5507087.html"))
        if "/search?s=id" in u:
            return httpx.Response(200, text=load("detail_099NCTU5507087.html"))
        if req.method == "POST" and u.endswith("/result"):
            self.result_body = req.content.decode()
            return httpx.Response(200, text=self.page())
        if u.endswith("/search"):
            return httpx.Response(200, text=self.page())
        return httpx.Response(404)


def make(site, solver=None):
    async def never(img, ct):
        raise AssertionError("solver must not be called")

    return NdltdClient(solver=solver or never, min_interval=0, transport=httpx.MockTransport(site))


async def test_blank_page_raises_and_does_not_retry():
    site = FakeSite("blank")
    c = make(site)
    with pytest.raises(NdltdBlockedError):
        await c.search_html("微積分")
    assert len(site.log) == 1  # blank already on the home page: stop before the next request


async def test_search_reuses_session():
    site = FakeSite()
    c = make(site)
    assert parsers.parse_search(await c.search_html("微積分")).total == 500
    await c.search_html("微積分")
    assert sum("o=dwebmge" in u for _, u in site.log) == 1
    assert all("/ccd=ABC999/" in u for _, u in site.log[2:])


async def test_captcha_flow_with_human_solver_and_image_stays_out_of_results():
    site = FakeSite("captcha")
    seen = {}

    async def solver(img, ct):
        seen["img"], seen["ct"] = img, ct
        return "good"

    c = make(site, solver)
    html = await c.detail_html("099NCTU5507087")
    assert parsers.parse_detail(html).get("研究生") == "楊嘉勝"
    assert seen == {"img": b"IMG", "ct": "image/png"}
    assert c.ccd == "NEW123"
    assert "/ccd=NEW123/" in site.log[-1][1]  # request rebuilt on the new session


async def test_wrong_captcha_does_not_loop():
    site = FakeSite("captcha")

    async def solver(img, ct):
        return "bad"

    c = make(site, solver)
    with pytest.raises(NdltdCaptchaRequired):
        await c.search_html("x")
    assert sum("validinput=" in u for _, u in site.log) == 1


async def test_no_answer_raises():
    async def solver(img, ct):
        return None

    with pytest.raises(NdltdCaptchaRequired):
        await make(FakeSite("captcha"), solver).search_html("x")


async def test_paging_replays_whole_result_form():
    site = FakeSite()
    c = make(site)
    await c.search_html("微積分", page=2)
    from urllib.parse import parse_qs
    sent = parse_qs(site.result_body, keep_blank_values=True)
    assert sent["jmpage"] == ["2"] and sent["jumpfmt1page"] == ["跳至"]
    assert sent["jpsize"] == ["20"] and "gonext" in sent
    assert len(sent) > 15  # not just the page number


async def test_record_html_jumps_to_right_page_then_reads_record():
    site = FakeSite()
    c = make(site)
    html = await c.record_html("微積分", 21)
    assert parsers.parse_detail(html).get("研究生") == "楊嘉勝"
    kinds = [(m, u.split("/ccd=ABC999")[-1]) for m, u in site.log if "ccd=ABC999" in u]
    assert kinds == [("POST", "/search"), ("POST", "/result"), ("GET", "/record?r1=21&h1=1")]


async def test_record_html_page_one_has_no_jump():
    site = FakeSite()
    await make(site).record_html("微積分", 12)
    assert not any(u.endswith("/result") for _, u in site.log)


async def test_captcha_in_the_middle_of_a_session_rebuilds_the_request():
    class ExpiringSite(FakeSite):
        expired = False

        def __call__(self, req):
            if "/ccd=ABC999/search?s=id" in str(req.url) and not self.expired:
                self.expired = True
                self.log.append((req.method, str(req.url)))
                return httpx.Response(200, text=CAPTCHA_HTML)
            return super().__call__(req)

    async def solver(img, ct):
        return "good"

    site = ExpiringSite()
    c = make(site, solver)
    html = await c.detail_html("099NCTU5507087")
    assert parsers.parse_detail(html).get("研究生") == "楊嘉勝"
    assert c.ccd == "NEW123"
    assert "/ccd=NEW123/search?s=id" in site.log[-1][1]  # sent again under the session the captcha gave
    assert sum("/search?s=id" in u for _, u in site.log) == 2  # the expired attempt and one retry, no more


async def test_captcha_that_gives_no_new_session_stops_after_one_attempt():
    class StuckSite(FakeSite):
        def __call__(self, req):
            u = str(req.url)
            if "validinput=" in u:  # the captcha is accepted but hands back the same session
                self.log.append((req.method, u))
                return httpx.Response(200, text="<html>ok /cgi-bin/gs32/gsweb.cgi/ccd=ABC999/search</html>")
            if "/search?s=id" in u:
                self.log.append((req.method, u))
                return httpx.Response(200, text=CAPTCHA_HTML)
            return super().__call__(req)

    async def solver(img, ct):
        return "good"

    site = StuckSite()
    with pytest.raises(NdltdNetworkError):
        await make(site, solver).detail_html("099NCTU5507087")
    assert sum("/search?s=id" in u for _, u in site.log) == 1


class OneResultSetSite(SessionApi):
    """Like the real site: one result set per session, overwritten by every search (a lookup by id is one too).
    Requests are serialised one at a time, as the real clients do; that alone does not keep operations apart."""

    def __init__(self, earlier_searches=()):
        self.history = list(earlier_searches)  # the site numbers a session's searches: h1=1, 2, 3, ...
        self.current = self.history[-1] if self.history else None
        self.log = []
        self._req = asyncio.Lock()

    async def _session_request(self, method, path, content=None, headers=None):
        async with self._req:
            await asyncio.sleep(0)  # let the other operations run if nothing holds them back
            if path == "/search":
                self.current = parse_qs(content.decode())["qs0"][0]
                self.history.append(self.current)
            elif path.startswith("/search?s=id"):
                self.current = "by-id"
                self.history.append(self.current)
            self.log.append((self.current, path.split("?")[0]))
            if path.startswith("/record"):  # row r1 of search number h1, whichever search that was
                shown = self.history[int(parse_qs(path.split("?", 1)[1])["h1"][0]) - 1]
                return httpx.Response(200, text=f"<p>result set: {shown}</p>")
            h1 = len(self.history)
            form = f'<form action="x/result"><input name="jpsize" value="20"><input type="hidden" name="h1" value="{h1}"></form>'
            rows = "".join(f'<td class="tdfmt1-content"><a class="slink" href="/x/record?r1={r}&amp;h1={h1}">t</a></td>'
                           for r in range(1, 3))
            return httpx.Response(200, text=f"檢索結果共 50 筆{form}<table><tr>{rows}</tr></table>"
                                            f"<p>result set: {self.current}</p>")


async def test_a_record_is_read_from_the_search_just_made_not_the_sessions_first():
    # reported 2026-11-28: with h1=1 fixed, every query+index lookup read the session's FIRST search
    site = OneResultSetSite(earlier_searches=["自傳式民族誌"])
    assert "result set: 行動研究 網站建置" in await site.record_html("行動研究 網站建置", 1)
    assert site.log[-1][1] == "/record"
    assert "result set: 自我民族誌" in await site.record_html("自我民族誌", 25)  # on page 2: after a jump


async def test_concurrent_record_lookups_do_not_read_each_others_result_set():
    site = OneResultSetSite()
    a, b = await asyncio.gather(site.record_html("AAA", 1), site.record_html("BBB", 1))
    assert "result set: AAA" in a and "result set: BBB" in b


async def test_concurrent_operations_never_interleave_their_requests():
    site = OneResultSetSite()
    await asyncio.gather(site.search_html("AAA", page=2), site.record_html("BBB", 30),
                         site.detail_html("099NCTU5507087"), site.search_html("CCC", page=3))
    # every operation's requests are consecutive: the result set changes only where a new operation starts
    owners = [q for q, _ in site.log]
    assert owners == sorted(owners, key=owners.index)
    assert [p for q, p in site.log if q == "BBB"] == ["/search", "/result", "/record"]


async def test_a_failed_operation_releases_the_operation_lock():
    site = OneResultSetSite()
    with pytest.raises(NdltdError):
        await site.record_html("AAA", 999)  # beyond the 50 hits
    assert "result set: BBB" in await asyncio.wait_for(site.record_html("BBB", 1), 5)
