"""HTTP layer: rate limit, one reusable session, honest errors. No caching, no full-text endpoints.

Session facts (observed 2026-11): the session id is the `ccd=XXXXXX` path segment (plus HttpOnly cookies);
the result set lives server-side in that session, so a later query overwrites an earlier one.
Entering the CGI via deep links (handle / o=dnclcdr&s=...) without a session got our IP throttled, so the
entry is the home page, then the login URL its script jumps to, then everything under /ccd=<id>/.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from urllib.parse import quote, urlencode

import httpx

from . import parsers
from .captcha import CaptchaChallenge, HumanCaptchaServer, parse_captcha_page
from .errors import NdltdBlockedError, NdltdCaptchaRequired, NdltdNetworkError

BASE = "https://ndltd.ncl.edu.tw"
CGI = BASE + "/cgi-bin/gs32/gsweb.cgi"
HOME = BASE + "/"  # sets the webnum cookie; its script then jumps to ENTRY (observed 2026-11-08)
# no cache=<ms> parameter (the page script adds one): robots.txt disallows URLs containing "cache="
ENTRY = CGI + "/login?ssoauth=1&loadingjs=1&o=dwebmge"
TIMEOUT = 30.0
MIN_INTERVAL = 3.0
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ndltd-mcp/0.1; metadata-and-abstract-only)",
    "Accept-Language": "zh-TW",
}
PAGE_SIZE = 20
FORM = {"Content-Type": "application/x-www-form-urlencoded"}

# (image bytes, content type) -> what the human typed, or None. Default opens a localhost page.
Solver = Callable[[bytes, str], Awaitable[str | None]]


async def human_solver(image: bytes, content_type: str) -> str | None:
    return await HumanCaptchaServer(image, content_type).ask()


class SessionApi:
    """High-level operations on top of `_session_request(method, path, content=None, headers=None) -> .text`.
    Shared by the plain-HTTP client and the browser-bridge client."""

    async def search_html(self, query: str, page: int = 1, fields: tuple[str, ...] = ("ti", "kw"),
                          match: str = "es0", sort: str = "") -> str:
        """Fresh search every call (result sets are per-session and get overwritten), then jump to `page`."""
        data = [("qs0", query), *[("dcf", f) for f in fields], ("displayonerecdisable", "1"),
                ("extrasearch", match), ("dbcode", "nclcdr"), ("_status_", "descrition_general")]
        resp = await self._session_request("POST", "/search", content=urlencode(data).encode(), headers=FORM)
        if sort:  # the page's sort select submits the whole result form again with another sortby
            form = parsers.result_form_fields(resp.text)
            form["sortby"] = sort
            resp = await self._session_request("POST", "/result", content=urlencode(form).encode(), headers=FORM)
        if page > 1:
            form = parsers.result_form_fields(resp.text)  # replay the whole form, as a browser would
            form.update({"jmpage": str(page), "jumpfmt1page": "跳至"})
            resp = await self._session_request("POST", "/result", content=urlencode(form).encode(), headers=FORM)
        return resp.text

    async def detail_html(self, thesis_id: str) -> str:
        path = f"/search?s=id={quote(chr(34) + thesis_id + chr(34))}.&searchmode=basic"
        return (await self._session_request("GET", path)).text

    async def record_html(self, query: str, index: int, fields: tuple[str, ...] = ("ti", "kw"),
                          match: str = "es0", sort: str = "") -> str:
        """Detail page of result number `index` (1-based, global) of a fresh search. Used for rows whose list
        entry carries no thesis id. Result sets are per-session and overwritten by any other query, so the
        search is repeated right before the lookup (and the page jump when needed)."""
        await self.search_html(query, page=(index - 1) // PAGE_SIZE + 1, fields=fields, match=match, sort=sort)
        return (await self._session_request("GET", f"/record?r1={index}&h1=1")).text


class NdltdClient(SessionApi):
    def __init__(self, *, solver: Solver = human_solver, min_interval: float = MIN_INTERVAL,
                 transport: httpx.AsyncBaseTransport | None = None):
        self._http = httpx.AsyncClient(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True, transport=transport)
        self._solver = solver
        self._min_interval = min_interval
        self._lock = asyncio.Lock()
        self._last = 0.0
        self.ccd: str | None = None

    async def aclose(self) -> None:
        await self._http.aclose()

    # ---- raw, throttled request -------------------------------------------------
    async def _raw(self, method: str, url: str, **kw) -> httpx.Response:
        async with self._lock:
            wait = self._min_interval - (time.monotonic() - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                resp = await self._http.request(method, url, **kw)
            except httpx.TimeoutException as e:
                raise NdltdNetworkError(f"連線國圖逾時（{TIMEOUT:.0f} 秒）。站方可能正在限流，請稍後再試，不要連續重試。") from e
            except httpx.HTTPError as e:
                raise NdltdNetworkError(f"無法連線國圖（{type(e).__name__}）：{e}") from e
            finally:
                self._last = time.monotonic()
        if resp.status_code != 200:
            raise NdltdNetworkError(f"國圖回應 HTTP {resp.status_code}：{url}")
        return resp

    # ---- session ---------------------------------------------------------------
    def _adopt(self, resp: httpx.Response) -> None:
        ccd = parsers.find_ccd(str(resp.url)) or parsers.find_ccd(resp.text)
        if ccd:
            self.ccd = ccd

    async def _pass_captcha(self, resp: httpx.Response) -> httpx.Response:
        ch: CaptchaChallenge = parse_captcha_page(resp.text, str(resp.url))
        img = await self._raw("GET", ch.image_url)
        ctype = img.headers.get("content-type", "image/png").split(";")[0]
        answer = await self._solver(img.content, ctype)  # image goes to the human only
        del img
        if not answer:
            raise NdltdCaptchaRequired("沒有收到驗證碼（逾時或取消）。請再呼叫一次，並在彈出的視窗輸入。")
        data = {**ch.fields, ch.answer_field: answer}
        if ch.method == "GET":
            out = await self._raw("GET", ch.action, params=data)
        else:
            out = await self._raw("POST", ch.action, content=urlencode(data).encode(), headers=FORM)
        kind = parsers.page_kind(out.text)
        if kind == "blank":
            raise NdltdBlockedError(
                "國圖回了「Blank page」，這是站方的限流／封鎖。請停止，等幾小時後再試；不要重試或換 IP 繞過。"
            )
        if kind != "ok":
            raise NdltdCaptchaRequired("驗證碼未通過（可能輸入錯誤）。請再呼叫一次，不會自動重試。")
        return out

    async def _checked(self, resp: httpx.Response) -> httpx.Response:
        kind = parsers.page_kind(resp.text)
        if kind == "blank":
            self.ccd = None
            raise NdltdBlockedError(
                "國圖回了「Blank page」，這是站方的限流／封鎖。請停止，等幾小時後再試；不要重試或換 IP 繞過。"
            )
        if kind == "captcha":
            self.ccd = None
            resp = await self._pass_captcha(resp)
        self._adopt(resp)
        return resp

    @staticmethod
    async def _checked_home(resp: httpx.Response) -> None:
        if parsers.page_kind(resp.text) == "blank":  # site already refuses us: do not send the next request
            raise NdltdBlockedError(
                "國圖回了「Blank page」，這是站方的限流／封鎖。請停止，等幾小時後再試；不要重試或換 IP 繞過。"
            )

    async def _ensure_session(self) -> str:
        if self.ccd is None:
            await self._checked_home(await self._raw("GET", HOME))
            await self._checked(await self._raw("GET", ENTRY))
        if self.ccd is None:
            raise NdltdNetworkError("進站後沒有取得 session（網址裡找不到 ccd=…）。")
        return self.ccd

    async def _session_request(self, method: str, path: str, **kw) -> httpx.Response:
        """Request `path` under the current session. A captcha gives a *new* session, so after one the
        request is built again with the new ccd (at most once; no retry loops)."""
        for attempt in range(2):
            before = self.ccd
            ccd = await self._ensure_session()
            if attempt == 1 and ccd == before:
                break
            resp = await self._raw(method, f"{CGI}/ccd={ccd}{path}", **kw)
            if parsers.page_kind(resp.text) == "ok":
                self._adopt(resp)
                return resp
            await self._checked(resp)  # raises on blank; solves captcha and refreshes self.ccd
        raise NdltdNetworkError("session 反覆失效，已停止。請稍後再試。")
