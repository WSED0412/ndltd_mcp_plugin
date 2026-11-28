"""Local bridge between the MCP server and the Chrome extension.

The extension (see ../../extension) long-polls this server, runs each job as a same-origin fetch inside the
user's own ndltd tab (so requests are the real browser's, and the human types the captcha there), and posts
the response HTML back. The HTML stays inside this process; only parsed bibliographic text reaches the model.

Security: binds 127.0.0.1 only; requires header X-Ndltd-Token (a random secret kept in
~/.ndltd_mcp/token, pasted once into the extension's options); rejects any Host other than 127.0.0.1/localhost
(DNS rebinding) and any Origin that is not a browser-extension one
(chrome-extension:// or moz-extension://); no CORS headers are ever sent.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import queue
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import parsers
from .client import MIN_INTERVAL, ExportMixin, SessionApi
from .errors import NdltdBlockedError, NdltdCaptchaRequired, NdltdError, NdltdNetworkError

DEFAULT_PORT = 8765
POLL_SECONDS = 20.0  # shorter than the service worker's ~30 s idle limit
JOB_TIMEOUT = 90.0
MAX_BODY = 4 * 1024 * 1024
UNAUTH_DRAIN = 64 * 1024  # read at most this much of an unauthorised body
EXTENSION_ORIGINS = ("chrome-extension://", "moz-extension://")  # Chrome / Firefox; a web page cannot have either
EXT_ALIVE_WITHIN = 70.0  # the extension polls every <= 25 s; longer silence means it is not connected
# The port is held only while this process is using it: bound by the first job, released IDLE_RELEASE seconds after
# the last one, so several Claude windows (each with its own ndltd-mcp) can take turns. A job that finds the port
# taken waits up to ACQUIRE_WAIT for the holder to let go; that must cover the holder's idle window plus one job of
# its own, or a call made just after another window's call would fail. Tune the two together.
IDLE_RELEASE = 15.0
ACQUIRE_WAIT = IDLE_RELEASE + JOB_TIMEOUT + 15.0  # + slack for the release itself and a slow multi-request call
ACQUIRE_RETRY = 0.5
SERVE_POLL = 0.05  # serve_forever's poll interval: stop() waits up to this long, so keep it short
STOP_CHECK = 0.25  # how often a blocked /poll checks whether its server was stopped


def token_path() -> Path:
    return Path(os.environ.get("NDLTD_HOME", Path.home() / ".ndltd_mcp")) / "token"


def load_or_create_token() -> str:
    p = token_path()
    try:
        if p.exists() and p.read_text(encoding="utf-8").strip():
            return p.read_text(encoding="utf-8").strip()
        p.parent.mkdir(parents=True, exist_ok=True)
        tok = secrets.token_urlsafe(24)
        p.write_text(tok, encoding="utf-8")
    except (OSError, UnicodeError) as e:  # reported by the tool call instead of killing the server at start-up
        raise NdltdError(f"無法讀寫橋接 token 檔（{type(e).__name__}）：{p}。請檢查該資料夾的權限，或用環境變數 NDLTD_HOME 換位置。") from e
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return tok


def bridge_port() -> int:
    raw = os.environ.get("NDLTD_BRIDGE_PORT", "").strip()
    if not raw:
        return DEFAULT_PORT
    try:
        port = int(raw)
    except ValueError:
        port = -1
    if not 0 <= port <= 65535:
        raise NdltdError(f"環境變數 NDLTD_BRIDGE_PORT 必須是 0 到 65535 的整數，目前是 {raw!r}。")
    return port


class _Server(ThreadingHTTPServer):
    # Windows: SO_REUSEADDR lets a second process bind the same port, so a stale server and a new one would listen side
    # by side and the extension could end up talking to the wrong one; keep it off. Elsewhere it only allows binding
    # over TIME_WAIT connections (needed to take the port over right after a release), never two listeners.
    allow_reuse_address = os.name != "nt"
    daemon_threads = True

    def __init__(self, *a, **kw):
        self.stopped = threading.Event()  # set by Bridge.stop(); handler threads of this server then return
        super().__init__(*a, **kw)


class _Handler(BaseHTTPRequestHandler):
    server_version = "ndltd-bridge"
    timeout = 30  # a stalled client cannot hold a handler thread forever

    def log_message(self, *a):
        pass

    def _reply(self, code: int, obj=None):
        body = b"" if obj is None else json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        b: Bridge = self.server.owner  # type: ignore[attr-defined]
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        if host not in ("127.0.0.1", "localhost"):
            return False
        origin = self.headers.get("Origin")
        if origin and not origin.startswith(EXTENSION_ORIGINS):
            return False
        # bytes, not str: compare_digest raises TypeError on a str with non-ASCII characters, and header values
        # are whatever the client sent (decoded as latin-1), so a stray byte would kill the handler instead of a 403
        given = (self.headers.get("X-Ndltd-Token") or "").encode("latin-1", "replace")
        return secrets.compare_digest(given, b.token.encode("utf-8"))

    def do_OPTIONS(self):  # no CORS: a preflight from any web page is refused
        self._reply(403, {"error": "forbidden"})

    def do_GET(self):
        b: Bridge = self.server.owner  # type: ignore[attr-defined]
        if not self._authorized():
            return self._reply(403, {"error": "forbidden"})
        if urlparse(self.path).path != "/poll":
            return self._reply(404, {"error": "not found"})
        b.last_poll = time.monotonic()
        deadline = time.monotonic() + POLL_SECONDS
        stopped = self.server.stopped  # type: ignore[attr-defined]
        while True:
            job = None
            if not stopped.is_set():
                try:
                    job = b.jobs.get(timeout=max(0.0, min(STOP_CHECK, deadline - time.monotonic())))
                except queue.Empty:
                    if time.monotonic() < deadline:
                        continue
            if job is not None and stopped.is_set():
                b.jobs.put(job)  # this server was released meanwhile: leave the job to the one that is listening
                job = None
            if job is None:
                b.last_poll = time.monotonic()
                try:
                    return self._reply(204)
                except OSError:  # poller already gone; nothing to hand over
                    return None
            if job["id"] in b.pending:  # a job whose caller gave up (timeout/cancel) is dropped here, never run
                break
        b.last_poll = time.monotonic()
        try:
            self._reply(200, job)
        except OSError:  # the poller hung up (e.g. its service worker was recycled): do not lose the job
            b.jobs.put(job)

    def do_POST(self):
        b: Bridge = self.server.owner  # type: ignore[attr-defined]
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0:  # read(-1) would wait for the client to hang up
            self.close_connection = True
            return self._reply(400, {"error": "bad length"})
        if not self._authorized():
            # refuse without buffering the body; drain a little so the client sees the 403 instead of a reset
            self.rfile.read(max(0, min(n, UNAUTH_DRAIN)))
            self.close_connection = True
            return self._reply(403, {"error": "forbidden"})
        if n > MAX_BODY:
            return self._reply(413, {"error": "too large"})
        raw = self.rfile.read(n)
        if urlparse(self.path).path != "/result":
            return self._reply(404, {"error": "not found"})
        try:
            res = json.loads(raw.decode("utf-8"))
            fut = b.pending.pop(res["id"])
        except (ValueError, KeyError, TypeError):
            return self._reply(400, {"error": "bad result"})
        if not fut.done():
            fut.set_result(res)
        self._reply(200, {"ok": True})


class Bridge:
    def __init__(self, port: int | None = None, token: str | None = None, idle_release: float | None = IDLE_RELEASE,
                 acquire_wait: float = ACQUIRE_WAIT):
        self.port = port if port is not None else bridge_port()
        self.token = token or load_or_create_token()
        self.jobs: queue.Queue[dict] = queue.Queue()
        self.pending: dict[str, concurrent.futures.Future] = {}
        self.last_poll = 0.0
        self.idle_release = idle_release  # None: keep the port until stop()
        self.acquire_wait = acquire_wait
        self._httpd: ThreadingHTTPServer | None = None
        self._lock = threading.RLock()  # start / stop / the idle check
        self._busy = 0  # jobs of this process in flight
        self._last_active = 0.0

    def start(self) -> None:
        """Bind now, or raise at once if the port is taken (submit() is the one that waits)."""
        with self._lock:
            if self._httpd:
                return
            try:
                self._httpd = _Server(("127.0.0.1", self.port), _Handler)
            except OSError as e:
                raise NdltdNetworkError(
                    f"橋接口 127.0.0.1:{self.port} 無法使用（{e}）。可能另一個 Claude 視窗的 ndltd-mcp 正在使用；"
                    "也可用環境變數 NDLTD_BRIDGE_PORT 換埠（擴充功能設定要同步）。"
                ) from e
            self.port = self._httpd.server_address[1]  # port 0: keep the one we got, so a rebind reuses it
            self._httpd.owner = self  # type: ignore[attr-defined]
            self._httpd.daemon_threads = True
            threading.Thread(target=self._httpd.serve_forever, args=(SERVE_POLL,), daemon=True).start()

    def stop(self) -> None:
        with self._lock:
            if not self._httpd:
                return
            self._httpd.stopped.set()  # type: ignore[attr-defined]  # blocked polls answer 204 and close
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
            # jobs still waiting can no longer be answered: fail them now instead of after JOB_TIMEOUT, and make sure
            # none of them runs after a later start()
            while True:
                try:
                    self.jobs.get_nowait()
                except queue.Empty:
                    break
            for fut in list(self.pending.values()):
                if not fut.done():
                    fut.set_exception(NdltdNetworkError("橋接口已關閉，這個工作沒有送出。"))

    @property
    def listening(self) -> bool:
        return self._httpd is not None

    def _release_if_idle(self) -> None:
        with self._lock:
            if self._busy or self.idle_release is None:
                return
            if time.monotonic() - self._last_active >= self.idle_release - 0.05:
                self.stop()

    async def _acquire(self) -> None:
        deadline = time.monotonic() + self.acquire_wait
        while True:
            with self._lock:
                try:
                    self.start()
                    self._busy += 1
                    return
                except NdltdNetworkError as e:
                    if time.monotonic() >= deadline:
                        raise NdltdNetworkError(
                            f"{e}（已等候 {self.acquire_wait:.0f} 秒仍未釋出，請稍後再試，或關掉沒在用的 Claude 視窗。）"
                        ) from None
            await asyncio.sleep(ACQUIRE_RETRY)

    def _done(self) -> None:
        with self._lock:
            self._busy -= 1
            self._last_active = time.monotonic()
            if self._busy == 0 and self.idle_release is not None:
                t = threading.Timer(self.idle_release, self._release_if_idle)
                t.daemon = True
                t.start()

    @property
    def extension_alive(self) -> bool:
        return self.last_poll > 0 and time.monotonic() - self.last_poll < EXT_ALIVE_WITHIN

    async def submit(self, job: dict, timeout: float = JOB_TIMEOUT) -> dict:
        await self._acquire()
        try:
            return await self._run(job, timeout)
        finally:
            self._done()

    async def _run(self, job: dict, timeout: float) -> dict:
        job = {**job, "id": secrets.token_hex(8)}
        fut: concurrent.futures.Future = concurrent.futures.Future()
        self.pending[job["id"]] = fut
        self.jobs.put(job)
        try:
            return await asyncio.wait_for(asyncio.wrap_future(fut), timeout)
        except asyncio.TimeoutError:
            if not self.extension_alive:
                raise NdltdNetworkError(
                    "Chrome 擴充功能沒有連上橋接口。請確認已載入擴充功能、在選項頁貼上 token、Chrome 正在執行。"
                ) from None
            raise NdltdNetworkError(f"瀏覽器 {timeout:.0f} 秒內沒有回應這個工作；請檢查 ndltd 分頁。") from None
        finally:
            # timeout, cancellation or any error: the job must not run later and clobber the session's result set
            self.pending.pop(job["id"], None)


class _Page:
    def __init__(self, text: str):
        self.text = text


class BridgeClient(ExportMixin, SessionApi):
    """Same high-level API as NdltdClient, but every request is made by the user's own Chrome."""

    def __init__(self, bridge: Bridge | None = None, min_interval: float = MIN_INTERVAL):
        self.bridge = bridge or Bridge()
        self._min_interval = min_interval
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def aclose(self) -> None:
        self.bridge.stop()

    async def _session_request(self, method: str, path: str, content: bytes | None = None, headers=None) -> _Page:
        # cont: this request reads the result set an earlier request of the same operation made (everything except
        # a search, which makes a new one). The extension then insists on the same tab and session, because any
        # other tab holds a different result set and would answer with a different thesis.
        job = {"method": method, "path": path, "body": content.decode() if content else None,
               "form": bool(content), "cont": not path.startswith("/search")}
        async with self._lock:
            wait = self._min_interval - (time.monotonic() - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                res = await self.bridge.submit(job)
            finally:
                self._last = time.monotonic()
        if not res.get("ok"):
            kind = res.get("kind", "")
            if kind in ("nosession", "captcha"):
                raise NdltdCaptchaRequired(
                    "瀏覽器的 ndltd 分頁還沒有可用的 session。已把分頁帶到國圖首頁；若出現驗證碼，請你本人輸入，"
                    "進入搜尋頁後再呼叫一次。"
                )
            if kind == "tab_lost":
                raise NdltdNetworkError(
                    "這個操作進行到一半時，原本使用的 ndltd 分頁已關閉、離開了查詢頁或換了 session，後續的請求會對到"
                    "別的搜尋結果，所以已中止。請確認瀏覽器有一個停在國圖查詢頁的分頁（出現驗證碼請本人輸入），再呼叫一次。"
                )
            raise NdltdNetworkError(f"瀏覽器端失敗：{res.get('error') or kind or '未知'}")
        status, html = res.get("status"), res.get("html") or ""
        if status != 200:
            raise NdltdNetworkError(f"國圖回應 HTTP {status}：{path.split('?')[0]}")
        kind = parsers.page_kind(html)
        if kind == "blank":
            raise NdltdBlockedError(
                "國圖回了「Blank page」，這是站方的限流／封鎖。請停止，等幾小時後再試；不要重試或換 IP 繞過。"
            )
        if kind == "captcha":
            raise NdltdCaptchaRequired(
                "國圖要求驗證碼。已把瀏覽器分頁帶到驗證碼頁，請你本人輸入，進入搜尋頁後再呼叫一次。"
            )
        return _Page(html)
