"""Search options of the site's basic-search form (field checkboxes `dcf`, match radio `extrasearch`).

Form values were read from the live page (2026-11). Only `exact` and the default fields have been exercised
end-to-end; the other match modes are the form's own values but unverified against live results."""
from __future__ import annotations

from .errors import NdltdError

FIELD_CODES = {
    "title": "ti",       # 論文名稱
    "author": "au",      # 研究生
    "advisor": "ad",     # 指導教授
    "committee": "say",  # 口試委員
    "keyword": "kw",     # 關鍵詞
    "abstract": "ab",    # 摘要
    "references": "rf",  # 參考文獻
}
FIELD_LABELS = {"ti": "論文名稱", "au": "研究生", "ad": "指導教授", "say": "口試委員", "kw": "關鍵詞", "ab": "摘要", "rf": "參考文獻"}
MATCH_CODES = {"exact": "es0", "fuzzy": "es1", "homophone": "es2", "synonym": "es3"}  # 精準／模糊／同音／同義詞

DEFAULT_FIELDS = "title,keyword"
DEFAULT_MATCH = "exact"


def resolve_fields(spec: str) -> tuple[str, ...]:
    """'title,keyword' | 'advisor' | 'all' (names or the site's own codes) -> ('ti', 'kw')."""
    spec = (spec or "").strip().lower() or DEFAULT_FIELDS
    if spec == "all":
        return tuple(FIELD_CODES.values())
    out: list[str] = []
    for part in (p.strip() for p in spec.replace("，", ",").replace(" ", ",").split(",")):
        if not part:
            continue
        code = FIELD_CODES.get(part) or (part if part in FIELD_LABELS else None)
        if code is None:
            raise NdltdError(f"不認得的搜尋欄位 {part!r}。可用：{', '.join(FIELD_CODES)}，或 all。")
        if code not in out:
            out.append(code)
    if not out:
        raise NdltdError("fields 不能是空的。")
    return tuple(out)


def resolve_match(spec: str) -> str:
    spec = (spec or "").strip().lower() or DEFAULT_MATCH
    code = MATCH_CODES.get(spec) or (spec if spec in MATCH_CODES.values() else None)
    if code is None:
        raise NdltdError(f"不認得的比對方式 {spec!r}。可用：{', '.join(MATCH_CODES)}。")
    return code


def describe(fields: tuple[str, ...], match: str) -> str:
    names = "、".join(FIELD_LABELS[f] for f in fields)
    mode = next(k for k, v in MATCH_CODES.items() if v == match)
    return f"欄位：{names}｜比對：{mode}"
