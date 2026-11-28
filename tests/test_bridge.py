import http.client
import json
import threading

import pytest

from ndltd_mcp import parsers
from ndltd_mcp.bridge import Bridge, BridgeClient
from ndltd_mcp.errors import NdltdBlockedError, NdltdCaptchaRequired, NdltdError, NdltdNetworkError
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
    assert http_call(bridge, "GET", "/poll", headers={"Origin": "https://chrome-extension.evil.example"})[0] == 403
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


@pytest.mark.parametrize("origin", ["chrome-extension://abcdefghijklmnop", "moz-extension://0f0e0d0c-1234-5678-9abc-def012345678"])
def test_extension_origins_are_accepted(bridge, origin):
    # Firefox sends moz-extension://<uuid>; both browsers' extensions must get past the Origin check
    assert http_call(bridge, "POST", "/result", body="{}", headers={"Origin": origin})[0] == 400  # authorised, bad job


def test_non_ascii_token_is_refused_not_crashed(bridge):
    # http.client.request() would refuse such a header itself, so the raw bytes are written
    c = http.client.HTTPConnection("127.0.0.1", bridge.port, timeout=5)
    c.putrequest("GET", "/poll")
    c.putheader("X-Ndltd-Token", "t0k\u00e9n".encode("latin-1"))
    c.endheaders()
    assert c.getresponse().status == 403


def test_bad_content_length_is_refused_at_once(bridge):
    for bad in ("-1", "abc"):
        c = http.client.HTTPConnection("127.0.0.1", bridge.port, timeout=5)  # would time out if the server read on
        c.putrequest("POST", "/result")
        c.putheader("X-Ndltd-Token", TOKEN)
        c.putheader("Content-Length", bad)
        c.endheaders()
        assert c.getresponse().status == 400


@pytest.mark.parametrize("value", ["abc", "70000", "-5", "8765.5"])
def test_bad_port_setting_is_a_clear_error(monkeypatch, value):
    monkeypatch.setenv("NDLTD_BRIDGE_PORT", value)
    with pytest.raises(NdltdError, match="NDLTD_BRIDGE_PORT"):
        Bridge(token="x")


def test_unusable_token_file_is_a_clear_error(monkeypatch, tmp_path):
    (tmp_path / "token").mkdir()  # a directory where the file should be: it can be neither read nor written
    monkeypatch.setenv("NDLTD_HOME", str(tmp_path))
    with pytest.raises(NdltdError, match="token"):
        Bridge(port=0)


async def test_follow_up_requests_are_marked_so_the_extension_keeps_them_in_one_session(bridge):
    c = BridgeClient(bridge, min_interval=0)
    page = load("search_one_result.html").replace("共 1 筆", "共 500 筆")
    t, seen = fake_extension(bridge, lambda job: {"ok": True, "status": 200, "html": page}, n=3)
    await c.record_html("微積分", 1)
    await c.detail_html("099NCTU5507087")
    t.join(5)
    assert [(j["path"].split("?")[0], j["cont"]) for j in seen] == [("/search", False), ("/record", True), ("/search", False)]


async def test_lost_tab_is_reported(bridge):
    c = BridgeClient(bridge, min_interval=0)
    t, _ = fake_extension(bridge, lambda job: {"ok": False, "kind": "tab_lost"})
    with pytest.raises(NdltdNetworkError, match="已中止"):
        await c.search_html("x")
    t.join(5)


# ---------------------------------------------------------------- port held only while in use

def _ok(job):
    return {"ok": True, "status": 200, "html": "<html></html>"}


async def test_the_port_is_bound_by_the_first_job_and_released_when_idle():
    b = Bridge(port=0, token=TOKEN, idle_release=0.3, acquire_wait=3)
    b.start()  # only to learn a free port; release it again
    port = b.port
    b.stop()
    assert not b.listening
    import asyncio

    def extension():  # like the real one: retry while nothing listens, re-poll on 204
        while True:
            try:
                st, data = http_call(b, "GET", "/poll")
            except OSError:
                threading.Event().wait(0.05)
                continue
            if st == 200:
                job = json.loads(data)
                http_call(b, "POST", "/result", json.dumps({"id": job["id"], **_ok(job)}))
                return

    threading.Thread(target=extension, daemon=True).start()
    res = await b.submit({"method": "GET", "path": "/x"}, timeout=5)
    assert res["ok"]
    assert b.listening and b.port == port
    for _ in range(60):
        if not b.listening:
            break
        await asyncio.sleep(0.05)
    assert not b.listening  # released after the idle window
    b.stop()


async def test_a_job_waits_for_another_window_to_release_the_port():
    import asyncio
    holder = Bridge(port=0, token=TOKEN, idle_release=None)
    holder.start()
    other = Bridge(port=holder.port, token=TOKEN, idle_release=None, acquire_wait=10)
    threading.Timer(0.7, holder.stop).start()
    task = asyncio.ensure_future(other.submit({"method": "GET", "path": "/x"}, timeout=5))
    await asyncio.sleep(0.3)
    assert not other.listening  # still waiting, not failed
    for _ in range(100):
        if other.listening:
            break
        await asyncio.sleep(0.05)
    assert other.listening
    fake_extension(other, _ok)
    assert (await task)["ok"]
    other.stop()


async def test_a_job_gives_up_with_a_clear_error_when_the_port_stays_taken():
    holder = Bridge(port=0, token=TOKEN, idle_release=None)
    holder.start()
    other = Bridge(port=holder.port, token=TOKEN, acquire_wait=0.6)
    try:
        with pytest.raises(NdltdNetworkError, match="Claude"):
            await other.submit({"method": "GET", "path": "/x"}, timeout=5)
        assert other._busy == 0
    finally:
        holder.stop()


def test_release_wakes_a_blocked_poll_and_another_process_can_bind_at_once():
    a = Bridge(port=0, token=TOKEN, idle_release=None)
    a.start()
    got = {}

    def poll():
        got["status"] = http_call(a, "GET", "/poll")[0]

    t = threading.Thread(target=poll, daemon=True)
    t.start()
    import time
    for _ in range(100):  # wait until the poll is blocked in a handler thread
        if a.last_poll > 0:
            break
        time.sleep(0.02)
    a.stop()
    t.join(3)
    assert got.get("status") == 204  # returned at once instead of after POLL_SECONDS
    b = Bridge(port=a.port, token=TOKEN, idle_release=None)
    b.start()  # the same port is free for the next window, even right after a live connection on it
    assert http_call(b, "GET", "/poll", headers={"X-Ndltd-Token": "wrong"})[0] == 403
    b.stop()


async def test_stopping_fails_waiting_jobs_at_once_and_none_runs_after_a_restart():
    import asyncio
    b = Bridge(port=0, token=TOKEN, idle_release=None)
    b.start()
    task = asyncio.ensure_future(b.submit({"method": "GET", "path": "/x"}, timeout=30))
    for _ in range(100):
        if b.pending:
            break
        await asyncio.sleep(0.01)
    b.stop()
    with pytest.raises(NdltdNetworkError, match="關閉"):
        await asyncio.wait_for(task, 3)
    assert b.jobs.empty() and not b.pending and b._busy == 0


async def test_an_expired_session_is_reported_as_such(bridge):
    c = BridgeClient(bridge, min_interval=0)
    fake_extension(bridge, lambda job: {"ok": True, "status": 200, "html": load("expired_session.html")})
    with pytest.raises(NdltdCaptchaRequired, match="過期"):
        await c.search_html("x")
