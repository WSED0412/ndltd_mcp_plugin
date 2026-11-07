import asyncio
import urllib.request

import httpx
import pytest

from ndltd_mcp import parsers
from ndltd_mcp.captcha import HumanCaptchaServer, parse_captcha_page
from ndltd_mcp.client import NdltdClient
from ndltd_mcp.errors import NdltdBlockedError, NdltdCaptchaRequired
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

    def __init__(self, mode="ok"):
        self.mode, self.log, self.answer, self.bodies = mode, [], None, []

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
            return httpx.Response(200, text=load("search_one_result.html"))
        if u.endswith("/search"):
            return httpx.Response(200, text=load("search_one_result.html"))
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
    assert parsers.parse_search(await c.search_html("微積分")).total == 1
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
