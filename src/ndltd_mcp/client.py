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
from .errors import NdltdBlockedError, NdltdCaptchaRequired, NdltdError, NdltdNetworkError

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

    async def _search(self, query: str, fields, match: str, sort: str, per_page: int):
        """The search itself, then at most ONE more post of the result form to apply sort and/or page size (the
        page's own select boxes submit the whole form again, so both are just fields of that one post).
        Nothing extra is sent when the page already shows them, has no such control (over 2000 hits: no sort),
        or has no results at all."""
        data = [("qs0", query), *[("dcf", f) for f in fields], ("displayonerecdisable", "1"),
                ("extrasearch", match), ("dbcode", "nclcdr"), ("_status_", "descrition_general")]
        resp = await self._session_request("POST", "/search", content=urlencode(data).encode(), headers=FORM)
        if parsers.result_total(resp.text) == 0:  # nothing to sort or page: the result page has no such form
            return resp
        if not (sort or per_page):
            return resp
        form = parsers.result_form_fields(resp.text)
        changes = {}
        if sort and "sortby" in form and form["sortby"] != sort:
            changes["sortby"] = sort
        if per_page and form.get("jpsize") != str(per_page):
            changes["jpsize"] = str(per_page)
            changes["SubmitChangePage"] = "1"  # the page sets this hidden field when the size select changes; without
            #                                    it the site ignores jpsize (measured live)
        if changes:
            form.update(changes)
            resp = await self._session_request("POST", "/result", content=urlencode(form).encode(), headers=FORM)
        return resp

    async def _jump(self, resp, page: int, per_page: int):
        form = parsers.result_form_fields(resp.text)  # replay the whole form, as a browser would
        form.update({"jmpage": str(page), "jumpfmt1page": "跳至"})
        if per_page:
            form["jpsize"] = str(per_page)
        return await self._session_request("POST", "/result", content=urlencode(form).encode(), headers=FORM)

    async def search_html(self, query: str, page: int = 1, fields: tuple[str, ...] = ("ti", "kw"),
                          match: str = "es0", sort: str = "", per_page: int = 0) -> str:
        """Fresh search every call (result sets are per-session and get overwritten), then jump to `page`.
        per_page 0 = whatever the site shows by default (20 for guests, 10 seen for members)."""
        resp = await self._search(query, fields, match, sort, per_page)
        if page > 1 and parsers.result_total(resp.text) != 0:
            resp = await self._jump(resp, page, per_page)
        return resp.text

    async def detail_html(self, thesis_id: str) -> str:
        path = f"/search?s=id={quote(chr(34) + thesis_id + chr(34))}.&searchmode=basic"
        return (await self._session_request("GET", path)).text

    async def record_html(self, query: str, index: int, fields: tuple[str, ...] = ("ti", "kw"),
                          match: str = "es0", sort: str = "", per_page: int = 0) -> str:
        """Detail page of result number `index` (1-based, global) of a fresh search. Used for rows whose list
        entry carries no thesis id. Result sets are per-session and overwritten by any other query, so the
        search is repeated right before the lookup, and the page that holds `index` is opened first. The page
        size is read from the page itself (it differs between guests and members), not assumed."""
        resp = await self._search(query, fields, match, sort, per_page)
        if parsers.result_total(resp.text) != 0:
            size = int(parsers.result_form_fields(resp.text).get("jpsize") or PAGE_SIZE)
            page = (index - 1) // size + 1
            if page > 1:
                await self._jump(resp, page, per_page)
        return (await self._session_request("GET", f"/record?r1={index}&h1=1")).text


MAX_EXPORT = 20  # records per experimental export call


class ExportError(NdltdError):
    """The member-only export could not be done safely (not logged in, selection did not match, ...)."""


class ExportMixin:
    async def export_full(self, query: str, page: int, fields, match: str, sort: str, limit: int):
        """EXPERIMENTAL, members only: the page's own '完整書目' export of one result page.
        Requests: the search (plus sort / page jump), clear ticks, tick this page's rows, preview, clear ticks.
        Ticks live in the session and survive new searches, so they are cleared first and last; the tick count
        reported by the site is checked before the preview is fetched."""
        html = await self.search_html(query, page, fields=fields, match=match, sort=sort)
        res = parsers.parse_search(html)
        if not res.rows:
            return res, []
        if not parsers.has_full_export(html):
            raise ExportError("結果頁沒有「完整書目」匯出選項：這個功能只提供給已登入的國圖會員，請先在瀏覽器登入。")
        rows = res.rows[: max(1, min(limit, MAX_EXPORT))]
        first, last = rows[0].index, rows[-1].index

        async def checker(arg: str) -> int | None:
            page_ = await self._session_request("GET", f"/getchecker?{arg}&cache={int(time.time() * 1000)}")
            return parsers.checker_count(page_.text)

        try:
            if await checker(f"out=1-{max(res.total, last)}") != 0:
                raise ExportError("清除既有勾選後，站方回報的勾選數不是 0；為避免匯出到不相干的紀錄，已中止。")
            if await checker(f"in={first}-{last}") != len(rows):
                raise ExportError(f"勾選後站方回報的筆數與預期的 {len(rows)} 筆不符；已中止。")
            preview = await self._session_request("GET", "/previewsave?preview=1&crange=1&cfield=2&outfmt=htm")
            records = parsers.parse_preview(preview.text)
        finally:
            try:
                await checker(f"out=1-{max(res.total, last)}")  # leave the member's session as we found it
            except NdltdError:
                pass
        if len(records) != len(rows):
            raise ExportError(f"預覽回傳 {len(records)} 筆，預期 {len(rows)} 筆；已中止，請不要採信這次的輸出。")
        return res, list(zip(rows, records))


class NdltdClient(ExportMixin, SessionApi):
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
