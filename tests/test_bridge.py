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
    assert not bridge.pending  # the abandoned job is no longer awaited


def test_job_is_requeued_when_poller_hangs_up(bridge):
    import socket
    import time

    import concurrent.futures

    bridge.pending["j1"] = concurrent.futures.Future()
    bridge.jobs.put({"id": "j1", "method": "GET", "path": "/x", "body": None})
    s = socket.create_connection(("127.0.0.1", bridge.port))
    crlf = chr(13) + chr(10)
    s.sendall(crlf.join(["GET /poll HTTP/1.1", "Host: 127.0.0.1", "X-Ndltd-Token: " + TOKEN, "", ""]).encode())
    s.close()  # hang up right away
    time.sleep(0.5)
    st, data = http_call(bridge, "GET", "/poll")
    assert st == 200 and json.loads(data)["id"] == "j1"


async def test_cancelled_caller_never_leaves_a_runnable_job(bridge):
    import asyncio

    task = asyncio.create_task(bridge.submit({"method": "GET", "path": "/x", "body": None}, timeout=30))
    await asyncio.sleep(0.2)
    assert len(bridge.pending) == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not bridge.pending
    # a poller must not be handed the abandoned job: the poll skips it and ends with 204 after its window
    import ndltd_mcp.bridge as bm

    bm.POLL_SECONDS = 0.5
    assert http_call(bridge, "GET", "/poll")[0] == 204


def test_bad_post_bodies_are_400_not_500(bridge):
    assert http_call(bridge, "POST", "/result", body="[]")[0] == 400
    assert http_call(bridge, "POST", "/result", body="null")[0] == 400


def test_unauthorised_body_is_not_buffered_but_the_client_still_gets_its_403(bridge):
    import socket

    crlf = chr(13) + chr(10)
    s = socket.create_connection(("127.0.0.1", bridge.port))
    body = b"x" * 200_000  # larger than the drain cap
    s.sendall(crlf.join(["POST /result HTTP/1.1", "Host: 127.0.0.1", "Content-Length: %d" % len(body), "", ""]).encode())
    try:
        s.sendall(body)
    except OSError:
        pass  # the server may already have refused and closed
    s.settimeout(5)
    data = b""
    try:
        data = s.recv(200)
    except OSError:
        pass
    s.close()
    assert data.startswith(b"HTTP/1.0 403") or data.startswith(b"HTTP/1.1 403") or data == b""
    # and the bridge is still healthy
    assert http_call(bridge, "GET", "/poll", headers={"X-Ndltd-Token": "wrong"})[0] == 403


def test_a_second_bridge_on_the_same_port_fails_loudly(bridge):
    other = Bridge(port=bridge.port, token="x")
    with pytest.raises(NdltdNetworkError, match="橋接口"):
        other.start()
