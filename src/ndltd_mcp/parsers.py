"""Pure HTML -> data parsers (no network). Layouts verified against the live site on 2026-11-07/08."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from selectolax.lexbor import LexborHTMLParser as HTMLParser, LexborNode as Node

from .errors import NdltdParseError

NONE = "（無）"
THESIS_ID_RE = re.compile(r"[0-9]{3}[A-Z0-9]{8,14}")
HANDLE_RE = re.compile(r"hdl\.handle\.net/(11296/[0-9A-Za-z]+)")
CCD_RE = re.compile(r"/ccd=([0-9A-Za-z]+)(?:/|\?|$)")


def page_kind(html: str) -> str:
    """'blank' (site throttling), 'captcha' (human must solve it) or 'ok'."""
    if "this is a Blank page" in html:
        return "blank"
    if "驗證碼檢查機制" in html:
        return "captcha"
    return "ok"


def find_ccd(text: str) -> str | None:
    m = CCD_RE.search(text)
    return m.group(1) if m else None


def _clean(s: str) -> str:
    return re.sub(r"[ \t\r\f\v\xa0]+", " ", s).strip()


def _node_text(node: Node) -> str:
    """Text with <br> turned into newlines, whitespace tidied, blank-line runs collapsed."""
    for br in node.css("br"):
        br.insert_after("\n")
    lines = [_clean(x) for x in node.text(deep=True, strip=False).split("\n")]
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return out


# ---------------------------------------------------------------- search results

@dataclass
class SearchRow:
    index: int  # position within the whole result set (1-based, as the site numbers it)
    title: str
    school: str
    department: str
    year: str  # 畢業學年度
    degree: str
    author: str
    advisor: str
    thesis_id: str | None  # None when the list shows no id (e.g. embargoed full text)
    fulltext_note: str = ""


@dataclass
class SearchPage:
    total: int
    rows: list[SearchRow] = field(default_factory=list)


def _after_label(lines: list[str], label: str) -> str:
    for x in lines:
        m = re.match(rf"{label}\s*[:：]\s*(.*)", x)
        if m:
            return m.group(1).strip()
    return ""


def _row_thesis_id(td: Node) -> str | None:
    html = td.html or ""
    for pat in (r"dbid=([0-9A-Za-z]+)", r"id(?:%3D|=)%22([0-9A-Za-z]+)%22"):
        m = re.search(pat, html)
        if m and THESIS_ID_RE.fullmatch(m.group(1)):
            return m.group(1)
    return None


def parse_search(html: str) -> SearchPage:
    tree = HTMLParser(html)
    text = tree.body.text() if tree.body else ""
    m = re.search(r"檢索結果共\s*([\d,]+)\s*筆", text)
    if not m:
        raise NdltdParseError("找不到「檢索結果共 N 筆」；頁面結構可能改版，或這不是結果頁。")
    page = SearchPage(total=int(m.group(1).replace(",", "")))
    for td in tree.css("td.tdfmt1-content"):
        link = td.css_first("a.slink")
        if link is None:
            continue
        lines = [_clean(c.text()) for c in td.css("td.std2") if _clean(c.text())]
        meta = next((x for x in lines[1:] if "／" in x), "")
        parts = [p.strip() for p in meta.split("／")]
        parts += [""] * (6 - len(parts))
        author = _after_label(lines, "研究生")
        advisor = _after_label(lines, "指導教授")
        num = td.parent.css_first("td.tdfmt1-second") if td.parent else None
        idx = int(re.sub(r"\D", "", num.text()) or 0) if num else 0
        ft = re.search(r"電子全文\s*\(([^)]*)\)", td.text())
        page.rows.append(SearchRow(
            index=idx, title=_clean(link.text()), school=parts[0], department=parts[1], year=parts[2],
            degree=parts[3], author=author, advisor=advisor, thesis_id=_row_thesis_id(td),
            fulltext_note=ft.group(1) if ft else "",
        ))
    return page


# ---------------------------------------------------------------- detail page

@dataclass
class Detail:
    fields: dict[str, str]
    handle: str | None
    abstract_zh: str
    abstract_en: str
    notes: list[str] = field(default_factory=list)
    toc: str = ""
    references: str = ""

    def get(self, label: str) -> str:
        return self.fields.get(label) or NONE


def parse_detail(html: str) -> Detail:
    tree = HTMLParser(html)
    fields: dict[str, str] = {}
    for th in tree.css("th.std1"):
        label = _clean(th.text()).rstrip(":：").strip()
        row = th.parent  # stay inside this <tr>: a header row without a <td> must not borrow the next row's cell
        td = row.css_first("td") if row is not None else None
        if not label or td is None or label.startswith("本論文永久網址"):
            continue
        val = "、".join(x for x in (_clean(l) for l in _node_text(td).split("\n")) if x)
        if label == "相關次數":
            val = _clean(td.text(separator=" "))
        fields[label] = val
    if "論文名稱" not in fields:
        raise NdltdParseError("詳目頁找不到「論文名稱」欄位；頁面結構可能改版，或編號不存在。")

    handle = None
    m = HANDLE_RE.search(html)
    if m:
        handle = m.group(1)

    # Tab labels and tab bodies appear in the same order; the first body is the bibliographic table.
    # The page has several tab groups (record, user tools, related papers); only the record group is ours.
    scope = tree.css_first("#gs32_levelrecord") or tree
    labels = [_clean(a.attributes.get("title") or "") for a in scope.css(".yui-nav li a")]
    bodies = scope.css(".yui-content > div")
    by_label: dict[str, Node] = {}
    # Newer pages list "論文基本資料" as the first tab (labels and bodies then line up from 0); the older layout
    # has no such tab and the first body is the bibliographic table. Extra blank/widget tabs may trail either.
    offset = 0 if labels and labels[0] == "論文基本資料" else 1
    for lab, body in zip(labels, bodies[offset:]):
        by_label.setdefault(lab, body)
    notes: list[str] = []

    def block(label: str, required: bool = True) -> str:
        node = by_label.get(label)
        if node is None:
            if required:
                notes.append(f"頁面沒有「{label}」區塊")
            return ""
        cell = node.css_first("td.stdncl2") or node
        return _node_text(cell)

    return Detail(fields=fields, handle=handle, abstract_zh=block("摘要"), abstract_en=block("外文摘要"), notes=notes,
                  toc=block("目次", required=False), references=block("參考文獻", required=False))


def _paren(d: Detail, label: str) -> str:
    v = d.fields.get(label)
    return f"（{v}）" if v else ""


def format_detail(d: Detail, thesis_id: str | None = None, include_toc: bool = False,
                  include_references: bool = False) -> str:
    def j(label: str) -> str:
        return d.get(label)

    out = [
        f"論文編號：{thesis_id or NONE}",
        f"永久網址：https://hdl.handle.net/{d.handle}" if d.handle else f"永久網址：{NONE}",
        f"論文名稱：{j('論文名稱')}",
        f"論文名稱（外文）：{j('論文名稱(外文)')}",
        f"研究生：{j('研究生')}{_paren(d, '研究生(外文)')}",
        f"指導教授：{j('指導教授')}{_paren(d, '指導教授(外文)')}",
        f"學位類別：{j('學位類別')}",
        f"校院名稱：{j('校院名稱')}",
        f"系所名稱：{j('系所名稱')}",
        f"畢業學年度：{j('畢業學年度')}　論文出版年：{j('論文出版年')}",
        f"語文別：{j('語文別')}　頁數：{j('論文頁數')}",
        f"中文關鍵詞：{j('中文關鍵詞')}",
        f"外文關鍵詞：{j('外文關鍵詞')}",
        "",
        "【中文摘要】",
        d.abstract_zh or NONE,
        "",
        "【外文摘要】",
        d.abstract_en or NONE,
    ]
    if include_toc:
        out += ["", "【目次】", d.toc or NONE]
    if include_references:
        out += ["", "【參考文獻】", d.references or NONE]
    if d.notes:
        out += ["", "備註：" + "；".join(d.notes)]
    return "\n".join(out)



def current_sort(html: str) -> str | None:
    """The sortby value the result page shows as selected, or None when the page has no sort control at all
    (the site offers none for result sets over 2000 hits: '超過排序上限 2000 筆')."""
    sel = HTMLParser(html).css_first("select[name=sortby]")
    if sel is None:
        return None
    opt = sel.css_first("option[selected]") or sel.css_first("option")
    return (opt.attributes.get("value") or "") if opt else ""


def result_total(html: str) -> int | None:
    """The total in '檢索結果共 N 筆', or None when the page does not say (not a result page)."""
    gap = r"(?:\s|&nbsp;|<[^>]*>)*"  # the real page puts the number inside a <span> between 共 and 筆
    m = re.search(rf"檢索結果共{gap}([\d,]+){gap}筆", html)
    return int(m.group(1).replace(",", "")) if m else None


def result_form_fields(html: str) -> dict[str, str]:
    """Every field the browser would post from the result page's paging form (action .../result), as a dict.
    The site wants the whole form back (hidden state like _status_=searchresult, searchlist, sortby ...),
    not just the page number. Image/submit/button inputs are left out; the caller adds its own submitter."""
    tree = HTMLParser(html)
    for form in tree.css("form"):
        if not (form.attributes.get("action") or "").split("?")[0].endswith("/result"):
            continue
        out: dict[str, str] = {}
        for i in form.css("input, select"):
            name = i.attributes.get("name")
            if not name:
                continue
            if i.tag == "select":
                opt = i.css_first("option[selected]") or i.css_first("option")
                out[name] = (opt.attributes.get("value") if opt else "") or ""
                continue
            typ = (i.attributes.get("type") or "text").lower()
            if typ in ("image", "submit", "button", "reset", "file"):
                continue
            if typ in ("checkbox", "radio") and "checked" not in i.attributes:
                continue
            out[name] = i.attributes.get("value") or ""
        return out
    raise NdltdParseError("結果頁找不到換頁表單（action 以 /result 結尾）；頁面結構可能改版。")
