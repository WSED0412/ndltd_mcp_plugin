"""Tool definitions (transport-agnostic; see stdio.py)."""
from __future__ import annotations

import functools
import logging

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import parsers
import os

from .bridge import BridgeClient
from .client import NdltdClient
from .errors import NdltdError

mcp = MCPServer(
    "ndltd",
    instructions=(
        "查詢臺灣博碩士論文知識加值系統（國圖 NDLTD）的書目與摘要，不下載全文。\n"
        "- ndltd_search 搜尋，取得論文編號；ndltd_abstract 以論文編號讀完整書目與中英文摘要。\n"
        "- 站方可能要求圖形驗證碼：工具會在使用者的瀏覽器開一個 localhost 視窗，由使用者本人輸入；"
        "不要嘗試辨識或猜測驗證碼，也看不到圖片。收到「需要驗證碼」就請使用者輸入後再呼叫一次。\n"
        "- 若回報被限流／封鎖（Blank page），請停止並告知使用者，不要重試。"
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


@_tool
async def ndltd_search(query: str, page: int = 1) -> str:
    """搜尋臺灣博碩士論文（論文名稱與關鍵詞欄位）。多個詞以空白分隔。

    Args:
        query: 搜尋詞，例如「微積分 學習策略」。
        page: 頁碼，從 1 開始（每頁 20 筆；每次呼叫都會重新搜尋）。
    回傳總筆數，以及本頁每筆的篇名、研究生、指導教授、學校系所、學年度、論文編號。
    """
    query = (query or "").strip()
    if not query:
        raise NdltdError("query 不能是空的。")
    if page < 1:
        raise NdltdError("page 必須 >= 1。")
    res = parsers.parse_search(await get_client().search_html(query, page))
    lines = [f"共 {res.total} 筆；第 {page} 頁（本頁 {len(res.rows)} 筆）"]
    for r in res.rows:
        note = "" if r.thesis_id else (
            f"（列表無編號{'；全文公開日期 ' + r.fulltext_note if r.fulltext_note else ''}；"
            f"讀摘要請用 ndltd_abstract(query=\"{query}\", index={r.index})）"
        )
        lines.append(
            f"{r.index}. {r.title}\n   研究生：{r.author}｜指導教授：{r.advisor}\n"
            f"   {r.school}／{r.department}／{r.year} 學年度／{r.degree}\n   論文編號：{r.thesis_id or parsers.NONE}{note}"
        )
    return "\n".join(lines)


@_tool
async def ndltd_abstract(thesis_id: str = "", query: str = "", index: int = 0) -> str:
    """讀取完整書目、中英文摘要與關鍵詞。兩種用法擇一：

    1. thesis_id：論文編號，例如 099NCTU5507087（由 ndltd_search 取得）。
    2. query + index：搜尋結果列表沒有論文編號的論文（全文延後公開者），用原本的搜尋詞與
       ndltd_search 顯示的序號（例如 21）。會重新搜尋再取該筆，回傳的篇名請與搜尋結果核對。
    """
    thesis_id, query = (thesis_id or "").strip(), (query or "").strip()
    if thesis_id:
        if not parsers.THESIS_ID_RE.fullmatch(thesis_id):
            raise NdltdError(f"論文編號格式不符：{thesis_id!r}。請用 ndltd_search 取得（例如 099NCTU5507087）。")
        d = parsers.parse_detail(await get_client().detail_html(thesis_id))
        return parsers.format_detail(d, thesis_id)
    if query and index >= 1:
        d = parsers.parse_detail(await get_client().record_html(query, index))
        return f"（依搜尋「{query}」第 {index} 筆取得，請核對篇名）\n" + parsers.format_detail(d, None)
    raise NdltdError("請提供 thesis_id，或同時提供 query 與 index（>=1）。")
