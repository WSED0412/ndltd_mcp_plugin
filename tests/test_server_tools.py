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


async def test_sort_with_no_results_does_not_claim_the_sort_was_refused(monkeypatch):
    class Empty:
        async def search_html(self, *a, **kw):
            return "<html><body>檢索結果共 0 筆</body></html>"

    monkeypatch.setattr(server, "_client", Empty())
    out = text(await call("ndltd_search", query="查無此詞", sort="year_desc"))
    assert "共 0 筆" in out and "排序沒有套用" not in out


def test_importing_the_server_opens_no_port_and_writes_no_token(tmp_path):
    import os
    import subprocess
    import sys

    env = {**os.environ, "NDLTD_HOME": str(tmp_path), "PYTHONPATH": "src"}
    code = "import ndltd_mcp.server as s; assert s._client is None"
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    subprocess.run([sys.executable, "-c", code], env=env, cwd=root, check=True, timeout=60)
    assert not (tmp_path / "token").exists()


def test_a_bad_bridge_setting_does_not_stop_the_server_from_starting(monkeypatch):
    monkeypatch.setattr(server, "_client", None)
    monkeypatch.setenv("NDLTD_BRIDGE_PORT", "abc")
    monkeypatch.delenv("NDLTD_MODE", raising=False)
    server.start_bridge_early()  # no exception: the first tool call reports it instead
    assert server._client is None


async def test_a_bad_bridge_setting_is_reported_by_the_tool_call(monkeypatch):
    monkeypatch.setattr(server, "_client", None)
    monkeypatch.setenv("NDLTD_BRIDGE_PORT", "abc")
    monkeypatch.delenv("NDLTD_MODE", raising=False)
    assert "NDLTD_BRIDGE_PORT" in text(await call("ndltd_search", query="微積分"))


async def test_hint_quotes_the_callers_own_arguments_safely(monkeypatch):
    from test_features import NoIdSite
    monkeypatch.setattr(server, "_client", make(NoIdSite()))
    out = text(await call("ndltd_search", query="微積分", fields='title", x="1', compact=False))
    assert "不認得的搜尋欄位" in out  # refused before any hint is built: a quote can never reach the hint unescaped
    out = text(await call("ndltd_search", query="微積分", fields="Title , keyword", match="exact", sort="Year_Desc"))
    assert 'fields="Title , keyword", match="exact", sort="Year_Desc"' in out
