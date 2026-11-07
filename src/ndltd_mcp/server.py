"""Tool definitions (transport-agnostic; see stdio.py)."""
from __future__ import annotations

import functools
import logging
import os

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import cite, options, parsers
from .bridge import BridgeClient
from .client import NdltdClient
from .errors import NdltdError

mcp = MCPServer(
    "ndltd",
    instructions=(
        "查詢臺灣博碩士論文知識加值系統（國圖 NDLTD）的書目與摘要，不下載全文。\n"
        "- ndltd_search 搜尋（可指定欄位與比對方式），取得論文編號；ndltd_abstract 以論文編號（或搜尋詞＋序號）"
        "讀完整書目與中英文摘要，可選附目次、參考文獻；ndltd_cite 產生 APA 7／BibTeX／RIS 引用。\n"
        "- 請求由使用者自己的 Chrome 發出（需載入本專案的擴充功能並開著）。站方要求圖形驗證碼時，"
        "由使用者在瀏覽器分頁本人輸入；不要嘗試辨識或猜測驗證碼。收到「需要驗證碼」就請使用者輸入後再呼叫一次。\n"
        "- 若回報被限流／封鎖（Blank page），請停止並告知使用者，不要重試。每次呼叫都會對站方發出請求，請節制。"
    ),
)
logging.getLogger("httpx").setLevel(logging.WARNING)

_client = None


def get_client():
    """Default: browser bridge (requests are made by the user's own Chrome). NDLTD_MODE=http selects the plain
    HTTP client, which the site currently answers with 'Blank page'."""
    global _client
    if _client is None:
        _client = NdltdClient() if os.environ.get("NDLTD_MODE") == "http" else BridgeClient()
    return _client


def _start_bridge_early() -> None:
    """Listen as soon as the server starts so the extension is already connected at the first call.
    A busy port is not fatal here (e.g. a second Claude session); the first request reports it."""
    if os.environ.get("NDLTD_MODE") == "http":
        return
    try:
        get_client().bridge.start()
    except NdltdError:
        pass


_start_bridge_early()


def _tool(fn):
    @functools.wraps(fn)
    async def wrapper(*a, **kw):
        try:
            return await fn(*a, **kw)
        except NdltdError as e:  # SDK v2 hides every other exception message
            raise ToolError(f"{type(e).__name__}: {e}") from e

    return mcp.tool()(wrapper)


async def _load_detail(thesis_id: str, query: str, index: int, fields: str, match: str, sort: str = ""):
    """Detail page by thesis id, or by (query, index) for rows the list shows without an id.
    Returns (Detail, thesis_id or None, provenance note or '')."""
    thesis_id, query = (thesis_id or "").strip(), (query or "").strip()
    if thesis_id:
        if not parsers.THESIS_ID_RE.fullmatch(thesis_id):
            raise NdltdError(f"論文編號格式不符：{thesis_id!r}。請用 ndltd_search 取得（例如 099NCTU5507087）。")
        return parsers.parse_detail(await get_client().detail_html(thesis_id)), thesis_id, ""
    if query and index >= 1:
        f, m, so = options.resolve_fields(fields), options.resolve_match(match), options.resolve_sort(sort)
        d = parsers.parse_detail(await get_client().record_html(query, index, fields=f, match=m, sort=so))
        return d, None, f"依搜尋「{query}」第 {index} 筆取得，請核對篇名"
    raise NdltdError("請提供 thesis_id，或同時提供 query 與 index（>=1）。")


@_tool
async def ndltd_search(query: str, page: int = 1, fields: str = options.DEFAULT_FIELDS,
                       match: str = options.DEFAULT_MATCH, sort: str = options.DEFAULT_SORT) -> str:
    """搜尋臺灣博碩士論文。多個詞以空白分隔。

    Args:
        query: 搜尋詞，例如「微積分 學習策略」。
        page: 頁碼，從 1 開始（每頁 20 筆；每次呼叫都會重新搜尋）。
        fields: 搜尋欄位，逗號分隔：title（論文名稱）、author（研究生）、advisor（指導教授）、committee（口試委員）、
            keyword（關鍵詞）、abstract（摘要）、references（參考文獻），或 all。預設 title,keyword。
        match: 比對方式：exact（精準，預設）、fuzzy（模糊）、homophone（同音）、synonym（同義詞）。
            非精準模式都不建議當預設：fuzzy 對常見詞回極大量無關結果；synonym 實測筆數多約五成且夾雜無關論文；
            homophone 只試過一例（搜人名，結果與 exact 相同）。除非精準比對找不到，否則不要使用。
        sort: 排序，留空＝站方預設（相關度）。year_desc／year_asc（畢業學年度，要最新論文用 year_desc）、
            cited_desc（被引用）、views_desc（點閱）、downloads_desc、title_asc、author_asc、school_asc 等。
            指定排序會多發一個請求。
    回傳總筆數，以及本頁每筆的篇名、研究生、指導教授、學校系所、學年度、論文編號。
    """
    query = (query or "").strip()
    if not query:
        raise NdltdError("query 不能是空的。")
    if page < 1:
        raise NdltdError("page 必須 >= 1。")
    f, m, so = options.resolve_fields(fields), options.resolve_match(match), options.resolve_sort(sort)
    res = parsers.parse_search(await get_client().search_html(query, page, fields=f, match=m, sort=so))
    extra = ""
    if (fields, match) != (options.DEFAULT_FIELDS, options.DEFAULT_MATCH):
        extra = f", fields=\"{fields}\", match=\"{match}\""
    if so:
        extra += f", sort=\"{sort}\""
    lines = [f"共 {res.total} 筆；第 {page} 頁（本頁 {len(res.rows)} 筆）｜{options.describe(f, m)}"
             + (f"｜排序：{sort}" if so else "")]
    if m != options.MATCH_CODES["exact"]:
        lines.append(
            "注意：非精準比對的範圍可能非常廣（實測 fuzzy 搜「微積分」會回三十多萬筆、前幾筆是無關的論文），"
            "請對照精準比對的筆數，不要直接當作相關結果。"
        )
    for r in res.rows:
        note = "" if r.thesis_id else (
            f"（列表無編號{'；全文公開日期 ' + r.fulltext_note if r.fulltext_note else ''}；"
            f"讀摘要請用 ndltd_abstract(query=\"{query}\", index={r.index}{extra})）"
        )
        lines.append(
            f"{r.index}. {r.title}\n   研究生：{r.author}｜指導教授：{r.advisor}\n"
            f"   {r.school}／{r.department}／{r.year} 學年度／{r.degree}\n   論文編號：{r.thesis_id or parsers.NONE}{note}"
        )
    return "\n".join(lines)


@_tool
async def ndltd_abstract(thesis_id: str = "", query: str = "", index: int = 0,
                         fields: str = options.DEFAULT_FIELDS, match: str = options.DEFAULT_MATCH,
                         sort: str = options.DEFAULT_SORT,
                         include_toc: bool = False, include_references: bool = False) -> str:
    """讀取完整書目、中英文摘要與關鍵詞。兩種用法擇一：

    1. thesis_id：論文編號，例如 099NCTU5507087（由 ndltd_search 取得）。
    2. query + index：搜尋結果列表沒有論文編號的論文（全文延後公開者），用原本的搜尋詞與
       ndltd_search 顯示的序號（例如 21）；fields、match、sort 要和當時搜尋時相同。會重新搜尋再取該筆，
       回傳的篇名請與搜尋結果核對。

    Args:
        include_toc: 附上目次（同一頁，不多發請求；預設關閉）。
        include_references: 附上參考文獻（同一頁，清單可能很長；預設關閉）。
    """
    d, tid, note = await _load_detail(thesis_id, query, index, fields, match, sort)
    return (f"（{note}）\n" if note else "") + parsers.format_detail(
        d, tid, include_toc=include_toc, include_references=include_references)


@_tool
async def ndltd_cite(thesis_id: str = "", query: str = "", index: int = 0,
                     fields: str = options.DEFAULT_FIELDS, match: str = options.DEFAULT_MATCH,
                     sort: str = options.DEFAULT_SORT, format: str = "apa") -> str:
    """產生學位論文的引用。用法同 ndltd_abstract（thesis_id，或 query + index）。

    Args:
        format: apa（APA 7，中文格式；只支援中文姓名與篇名）、bibtex（@mastersthesis／@phdthesis）、ris（TY - THES）。
            BibTeX 與 RIS 可匯入 Zotero、Mendeley、EndNote。
    引用只用詳目頁已有的欄位，並以論文出版年（西元）為年份；請與原頁面核對。
    """
    d, tid, note = await _load_detail(thesis_id, query, index, fields, match, sort)
    return cite.make(format, d, tid, note)
