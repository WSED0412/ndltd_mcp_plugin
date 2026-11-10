import pytest

from ndltd_mcp import server
from ndltd_mcp.errors import NdltdError
from test_client import FakeSite, make


@pytest.fixture
def fake(monkeypatch):
    site = FakeSite()
    c = make(site)
    monkeypatch.setattr(server, "_client", c)
    return site


async def call(name, **kw):
    from mcp.server.mcpserver.exceptions import ToolError
    try:
        return await server.mcp.call_tool(name, kw)
    except ToolError as e:
        return str(e)


def text(res):
    return res if isinstance(res, str) else str(res)


async def test_search_output(fake):
    out = text(await call("ndltd_search", query="微積分"))
    assert "共 500 筆" in out and "099NCTU5507087" in out and "楊嘉勝" in out


async def test_abstract_by_id(fake):
    out = text(await call("ndltd_abstract", thesis_id="099NCTU5507087"))
    assert "白啟光" in out and "【中文摘要】" in out


async def test_abstract_by_query_index(fake):
    out = text(await call("ndltd_abstract", query="微積分", index=3))
    assert "請核對篇名" in out and "楊嘉勝" in out


async def test_abstract_bad_args(fake):
    assert "請提供 thesis_id" in text(await call("ndltd_abstract"))
    assert "格式不符" in text(await call("ndltd_abstract", thesis_id="abc"))
