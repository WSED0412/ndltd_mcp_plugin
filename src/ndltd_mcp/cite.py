"""Citations for theses, built only from the detail page's own bibliographic fields (no extra requests).

APA 7 follows the database-thesis pattern, in Chinese-style punctuation:
    作者（年）。*論文名稱*〔碩士論文，校院名稱〕。臺灣博碩士論文知識加值系統。網址
Chinese-named authors and titles only: a romanised author name ("Yang, Jia-Sheng") would need "Family, G.-S."
conversion, which is error-prone, so APA is refused for those and BibTeX / RIS are offered instead.
The year is the publication year (西元) from 論文出版年, never the 民國 graduation year."""
from __future__ import annotations

import re

from .errors import NdltdError
from .parsers import Detail

_CJK = re.compile(r"[㐀-鿿]")
DB_NAME = "臺灣博碩士論文知識加值系統"
FORMATS = ("apa", "bibtex", "ris")


def _url(d: Detail) -> str:
    return f"https://hdl.handle.net/{d.handle}" if d.handle else ""


def _year(d: Detail) -> str:
    m = re.match(r"\s*(\d{4})", d.fields.get("論文出版年", ""))
    if not m:
        raise NdltdError("詳目頁沒有可解析的「論文出版年」（西元年），不產生引用。")
    return m.group(1)


def _need(d: Detail, *labels: str) -> None:
    missing = [lab for lab in labels if not d.fields.get(lab)]
    if missing:
        raise NdltdError(f"書目資料不足，無法產生引用（缺：{'、'.join(missing)}）。")


def _kind(d: Detail) -> tuple[str, str, str]:
    """(bibtex entry type, RIS note, Chinese label) from 學位類別."""
    deg = d.fields.get("學位類別", "")
    if "博士" in deg:
        return "phdthesis", "Doctoral thesis", "博士論文"
    if "碩士" in deg:
        return "mastersthesis", "Master's thesis", "碩士論文"
    return "misc", "Thesis", f"{deg or '學位'}論文"


def apa7(d: Detail) -> str:
    _need(d, "研究生", "論文名稱", "校院名稱", "學位類別")
    author, title = d.fields["研究生"], d.fields["論文名稱"]
    if not (_CJK.search(author) and _CJK.search(title)):
        raise NdltdError(
            "目前只支援中文姓名與中文篇名的 APA 引用（外文姓名需轉成「姓, 名縮寫」，自動轉換容易出錯）。"
            "請改用 format=\"bibtex\" 或 \"ris\"，讓書目軟體排版。"
        )
    url = _url(d)
    if not url:
        raise NdltdError("詳目頁沒有永久網址（hdl.handle.net），無法產生含網址的 APA 引用；請改用 bibtex 或 ris。")
    _, _, label = _kind(d)
    return f"{author}（{_year(d)}）。*{title}*〔{label}，{d.fields['校院名稱']}〕。{DB_NAME}。{url}"


def _bib_escape(s: str) -> str:
    return re.sub(r"([&%$#_])", r"\\\1", s.replace("\\", "").replace("{", "(").replace("}", ")"))


def bibtex(d: Detail, thesis_id: str | None = None) -> str:
    _need(d, "研究生", "論文名稱", "校院名稱")
    entry, _, label = _kind(d)
    key = "ndltd" + (d.handle.rsplit("/", 1)[-1] if d.handle else (thesis_id or "").lower() or "x")
    f = d.fields
    rows = [
        ("author", f.get("研究生") or f.get("研究生(外文)", "")),
        ("title", "{" + _bib_escape(f["論文名稱"]) + "}"),
        ("school", f["校院名稱"]),
        ("year", _year(d)),
        ("type", label),
        ("language", f.get("語文別", "")),
        ("keywords", "、".join(x for x in (f.get("中文關鍵詞", ""), f.get("外文關鍵詞", "")) if x)),
        ("url", _url(d)),
        ("note", "；".join(x for x in (
            f"指導教授：{f['指導教授']}" if f.get("指導教授") else "",
            f"系所：{f['系所名稱']}" if f.get("系所名稱") else "",
            f"外文姓名：{f['研究生(外文)']}" if f.get("研究生(外文)") and f.get("研究生") else "",
        ) if x)),
    ]
    body = ",\n".join(f"  {k} = {{{v if k in ('title', 'url') else _bib_escape(v)}}}" for k, v in rows if v)
    return f"@{entry}{{{key},\n{body}\n}}"


def ris(d: Detail, thesis_id: str | None = None) -> str:
    _need(d, "研究生", "論文名稱", "校院名稱")
    _, note, label = _kind(d)
    f = d.fields
    lines = [
        "TY  - THES",
        f"AU  - {f.get('研究生') or f.get('研究生(外文)', '')}",
        f"TI  - {f['論文名稱']}",
        f"PY  - {_year(d)}///",
        f"PB  - {f['校院名稱']}",
        f"M3  - {label}",
        f"DB  - {DB_NAME}",
    ]
    if f.get("語文別"):
        lines.append(f"LA  - {f['語文別']}")
    for kw_field in ("中文關鍵詞", "外文關鍵詞"):
        for kw in re.split(r"[、,;；]", f.get(kw_field, "")):
            if kw.strip():
                lines.append(f"KW  - {kw.strip()}")
    if f.get("指導教授"):
        lines.append(f"N1  - 指導教授：{f['指導教授']}")
    if _url(d):
        lines.append(f"UR  - {_url(d)}")
    lines.append("ER  - ")
    return "\n".join(lines)


def make(fmt: str, d: Detail, thesis_id: str | None = None) -> str:
    fmt = (fmt or "apa").strip().lower()
    if fmt == "apa":
        return apa7(d)
    if fmt == "bibtex":
        return bibtex(d, thesis_id)
    if fmt == "ris":
        return ris(d, thesis_id)
    raise NdltdError(f"不認得的格式 {fmt!r}。可用：{', '.join(FORMATS)}。")
