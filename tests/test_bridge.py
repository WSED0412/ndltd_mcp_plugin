import http.client
import json
import threading

import pytest

from ndltd_mcp import parsers
from ndltd_mcp.bridge import Bridge, BridgeClient
from ndltd_mcp.errors import NdltdBlockedError, NdltdCaptchaRequired, NdltdNetworkError
from test_parsers import load

TOKEN = "t0ken"


@pytest.fixture
def bridge():
    b = Bridge(port=0, token=TOKEN)
    b.start()
    yield b
    b.stop()


def http_call(b, method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", b.port, timeout=5)
    h = {"X-Ndltd-Token": TOKEN, **(headers or {})}
    c.request(method, path, body=body, headers=h)
    r = c.getresponse()
    return r.status, r.read()


def fake_extension(b, responder, n=1):
    """Plays the extension: poll n jobs, answer each with responder(job) -> dict."""
    seen = []

    def run():
        for _ in range(n):
            st, data = http_call(b, "GET", "/poll")
            assert st == 200
            job = json.loads(data)
            seen.append(job)
            http_call(b, "POST", "/result", json.dumps({"id": job["id"], **responder(job)}))

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, seen


def test_security_checks(bridge):
    assert http_call(bridge, "GET", "/poll", headers={"X-Ndltd-Token": "wrong"})[0] == 403
    assert http_call(bridge, "GET", "/poll", headers={"Host": "evil.example:80"})[0] == 403
    assert http_call(bridge, "GET", "/poll", headers={"Origin": "https://evil.example"})[0] == 403
    assert http_call(bridge, "OPTIONS", "/poll")[0] == 403
    assert http_call(bridge, "POST", "/result", body="{}", headers={"X-Ndltd-Token": ""})[0] == 403
    assert http_call(bridge, "POST", "/result", body="not json")[0] == 400


async def test_search_via_extension(bridge):
    c = BridgeClient(bridge, min_interval=0)
    t, seen = fake_extension(bridge, lambda job: {"ok": True, "status": 200, "html": load("search_one_result.html")})
    page = parsers.parse_search(await c.search_html("微積分"))
    t.join(5)
    assert page.total == 1
    assert seen[0]["method"] == "POST" and seen[0]["path"] == "/search"
    assert "qs0=%E5%BE%AE%E7%A9%8D%E5%88%86" in seen[0]["body"]


async def test_detail_via_extension(bridge):
    c = BridgeClient(bridge, min_interval=0)
    t, seen = fake_extension(bridge, lambda job: {"ok": True, "status": 200, "html": load("detail_099NCTU5507087.html")})
    d = parsers.parse_detail(await c.detail_html("099NCTU5507087"))
    t.join(5)
    assert d.get("研究生") == "楊嘉勝"
    assert seen[0]["method"] == "GET" and "099NCTU5507087" in seen[0]["path"] and seen[0]["body"] is None


async def test_blank_page_stops(bridge):
    c = BridgeClient(bridge, min_interval=0)
    t, _ = fake_extension(bridge, lambda job: {"ok": True, "status": 200, "html": load("blank_page.html")})
    with pytest.raises(NdltdBlockedError):
        await c.search_html("x")
    t.join(5)


async def test_captcha_and_nosession_are_reported_for_the_human(bridge):
    c = BridgeClient(bridge, min_interval=0)
    t, _ = fake_extension(bridge, lambda job: {"ok": True, "status": 200, "html": "<p>驗證碼檢查機制</p>"})
    with pytest.raises(NdltdCaptchaRequired):
        await c.search_html("x")
    t.join(5)
    t, _ = fake_extension(bridge, lambda job: {"ok": False, "kind": "nosession"})
    with pytest.raises(NdltdCaptchaRequired):
        await c.search_html("x")
    t.join(5)


async def test_extension_not_connected_times_out_clearly(bridge, monkeypatch):
    import ndltd_mcp.bridge as bm

    monkeypatch.setattr(bm, "JOB_TIMEOUT", 0.3)
    c = BridgeClient(bridge, min_interval=0)
    monkeypatch.setattr(bridge, "submit", lambda job, timeout=0.3, _s=bridge.submit: _s(job, timeout))
    with pytest.raises(NdltdNetworkError, match="擴充功能沒有連上"):
        await c.search_html("x")
    assert bridge.jobs.empty()  # the abandoned job was withdrawn


def test_job_is_requeued_when_poller_hangs_up(bridge):
    import socket
    import time

    bridge.jobs.put({"id": "j1", "method": "GET", "path": "/x", "body": None})
    s = socket.create_connection(("127.0.0.1", bridge.port))
    crlf = chr(13) + chr(10)
    s.sendall(crlf.join(["GET /poll HTTP/1.1", "Host: 127.0.0.1", "X-Ndltd-Token: " + TOKEN, "", ""]).encode())
    s.close()  # hang up right away
    time.sleep(0.5)
    st, data = http_call(bridge, "GET", "/poll")
    assert st == 200 and json.loads(data)["id"] == "j1"
