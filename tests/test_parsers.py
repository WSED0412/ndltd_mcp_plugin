import pathlib

import pytest

from ndltd_mcp import parsers
from ndltd_mcp.errors import NdltdParseError

FX = pathlib.Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return (FX / name).read_text(encoding="utf-8")


def test_page_kind():
    assert parsers.page_kind(load("blank_page.html")) == "blank"
    assert parsers.page_kind("<title>x</title>驗證碼檢查機制") == "captcha"
    assert parsers.page_kind(load("detail_099NCTU5507087.html")) == "ok"


def test_find_ccd():
    assert parsers.find_ccd("/cgi-bin/gs32/gsweb.cgi/ccd=XWaACt/search") == "XWaACt"
    assert parsers.find_ccd("nothing here") is None


def test_search_one_result():
    page = parsers.parse_search(load("search_one_result.html"))
    assert page.total == 1
    r = page.rows[0]
    assert r.index == 1
    assert r.title == "大一學生微積分學習策略與微積分學習成效之研究"
    assert (r.school, r.department, r.year, r.degree) == ("國立交通大學", "應用數學系所", "99", "碩士")
    assert r.author == "楊嘉勝" and r.advisor == "白啟光"
    assert r.thesis_id == "099NCTU5507087"
    assert (r.field, r.category, r.kind) == ("數學及統計學門", "數學學類", "學術論文")
    assert (r.cited, r.views, r.downloads, r.favorites) == (15, 1062, 197, 1)


def test_search_row_without_id_and_embargo_note():
    html = """<body>檢索結果共 121 筆資料
    <table><tr><td class="tdfmt1-first"></td><td class="tdfmt1-second">12.</td>
    <td class="tdfmt1-content"><table><tr>
    <td class="std2"><a href="/x/record?r1=12&h1=1" class="slink"><span>某論文</span></a></td></tr>
    <tr><td class="std2">國立政治大學／應用數學系／113／碩士／數學及統計學門／數學學類</td></tr>
    <tr><td class="std2">研究生:孫翊珈</td></tr><tr><td class="std2">指導教授:蔡炎龍</td></tr>
    <tr><td class="std2">電子全文(網際網路公開日期：20300820) 被引用:0</td></tr></table></td></tr></table></body>"""
    page = parsers.parse_search(html)
    assert page.total == 121
    r = page.rows[0]
    assert r.index == 12 and r.thesis_id is None
    assert r.fulltext_note == "網際網路公開日期：20300820"


def test_search_without_total_raises():
    with pytest.raises(NdltdParseError):
        parsers.parse_search("<html><body>hello</body></html>")


def test_detail_fields_and_abstracts():
    d = parsers.parse_detail(load("detail_099NCTU5507087.html"))
    assert d.get("論文名稱") == "大一學生微積分學習策略與微積分學習成效之研究"
    assert d.get("研究生") == "楊嘉勝"
    assert d.get("指導教授") == "白啟光"
    assert d.get("校院名稱") == "國立交通大學"
    assert d.get("系所名稱") == "應用數學系所"
    assert d.get("畢業學年度") == "99"
    assert d.get("中文關鍵詞") == "微積分學習策略、微積分學習成效"
    assert d.get("外文關鍵詞") == "Calculus Learning Strategies、Calculus Achievement"
    assert d.handle == "11296/gnew8c"
    assert d.abstract_zh.startswith("本研究旨在了解國立交通大學的大一學生")
    assert d.abstract_en.startswith("The purpose of this study was to explore")
    assert "目次" not in d.abstract_zh and "參考文獻" not in d.abstract_en
    assert d.notes == []


def test_detail_wrong_page_raises():
    with pytest.raises(NdltdParseError):
        parsers.parse_detail(load("search_one_result.html"))


def test_format_detail_contains_everything():
    d = parsers.parse_detail(load("detail_099NCTU5507087.html"))
    s = parsers.format_detail(d, "099NCTU5507087")
    for needle in ("099NCTU5507087", "hdl.handle.net/11296/gnew8c", "楊嘉勝", "白啟光", "【中文摘要】", "【外文摘要】"):
        assert needle in s


def test_result_form_fields_from_real_page():
    f = parsers.result_form_fields(load("search_one_result.html"))
    for k in ("jmpage", "jpsize", "gonext", "_status_", "searchlist"):
        assert k in f, k
    assert f["jpsize"] == "20"
    assert "jumpfmt1page" not in f


def test_detail_new_layout_with_basic_info_tab():
    html = """<body><ul class="yui-nav"><li><a title="論文基本資料"></a></li><li><a title="摘要"></a></li>
    <li><a title="外文摘要"></a></li><li><a title="目次"></a></li><li><a title="share"></a></li><li><a title=""></a></li></ul>
    <div class="yui-content"><div id="aa">basic</div>
    <div id="aa"><table><tr><td class="stdncl2">中文摘要內容</td></tr></table></div>
    <div id="aa"><table><tr><td class="stdncl2">English abstract</td></tr></table></div>
    <div id="aa"><table><tr><td class="stdncl2">第一章</td></tr></table></div></div>
    <table><tr><th class="std1">論文名稱:</th><td class="std2">題目</td></tr></table>
    hdl.handle.net/11296/36zjzz</body>"""
    d = parsers.parse_detail(html)
    assert d.abstract_zh == "中文摘要內容" and d.abstract_en == "English abstract"
    assert d.handle == "11296/36zjzz" and d.notes == []


def test_search_row_accepts_fullwidth_colon():
    html = """<body>檢索結果共 1 筆資料<table><tr><td class="tdfmt1-second">1.</td>
    <td class="tdfmt1-content"><table><tr><td class="std2"><a class="slink">題目</a></td></tr>
    <tr><td class="std2">某大學／某系／100／碩士／甲門／乙類</td></tr>
    <tr><td class="std2">研究生：王小明</td></tr><tr><td class="std2">指導教授：李老師</td></tr></table></td></tr></table></body>"""
    r = parsers.parse_search(html).rows[0]
    assert (r.author, r.advisor) == ("王小明", "李老師")


def test_detail_multivalue_cells_are_joined_not_glued_and_rows_do_not_leak():
    html = """<body><table>
    <tr><th class="std1">論文名稱:</th><td class="std2">題目</td></tr>
    <tr><th class="std1">指導教授:</th><td class="std2"><a>張三</a><br><a>李四</a></td></tr>
    <tr><th class="std1">單獨的標題列:</th></tr>
    <tr><th class="std1">校院名稱:</th><td class="std2">某大學</td></tr></table></body>"""
    d = parsers.parse_detail(html)
    assert d.get("指導教授") == "張三、李四"
    assert d.fields.get("單獨的標題列") in (None, "")  # no borrowing from the next row
    assert d.get("校院名稱") == "某大學"


def test_detail_only_reads_the_record_tab_group():
    html = """<body><table><tr><th class="std1">論文名稱:</th><td class="std2">題目</td></tr></table>
    <div id="gs32_usercommand"><ul class="yui-nav"><li><a title="share"></a></li><li><a title="摘要"></a></li></ul>
    <div class="yui-content"><div>廣告</div><div><table><tr><td class="stdncl2">錯的摘要</td></tr></table></div></div></div>
    <div id="gs32_levelrecord"><ul class="yui-nav"><li><a title="論文基本資料"></a></li><li><a title="摘要"></a></li>
    <li><a title="外文摘要"></a></li></ul>
    <div class="yui-content"><div>basic</div>
    <div><table><tr><td class="stdncl2">對的中文摘要</td></tr></table></div>
    <div><table><tr><td class="stdncl2">right english</td></tr></table></div></div></div></body>"""
    d = parsers.parse_detail(html)
    assert d.abstract_zh == "對的中文摘要" and d.abstract_en == "right english"


def test_find_ccd_accepts_dots_and_underscores():
    assert parsers.find_ccd("/cgi-bin/gs32/gsweb.cgi/ccd=Jy2D._/search") == "Jy2D._"
    assert parsers.find_ccd("https://x/cgi-bin/gs32/gsweb.cgi/ccd=a-b~c.d_e?x=1") == "a-b~c.d_e"


def test_page_kind_ignores_the_marker_phrases_inside_content_pages():
    # a result list echoes the query and a record page shows the abstract: either may contain the phrases
    for phrase in ("驗證碼檢查機制", "this is a Blank page"):
        assert parsers.page_kind(load("search_one_result.html").replace("</body>", phrase + "</body>")) == "ok"
        assert parsers.page_kind(load("detail_099NCTU5507087.html").replace("</body>", phrase + "</body>")) == "ok"
        assert parsers.page_kind(f"<html><body>檢索結果共 0 筆 {phrase}</body></html>") == "ok"
        assert parsers.page_kind(f'<table class="tableoutfmt2"><td>{phrase}</td></table>') == "ok"
    # the real pages are still recognised
    assert parsers.page_kind(load("blank_page.html")) == "blank"
    assert parsers.page_kind("<title>x</title><form>驗證碼檢查機制<input name=validinput></form>") == "captcha"
