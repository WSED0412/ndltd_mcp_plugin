# ndltd-mcp

**English** | [繁體中文](README.zh-TW.md)

A **local** MCP server for the Taiwan thesis system (NDLTD, National Central Library: <https://ndltd.ncl.edu.tw/>). It searches bibliographic records, reads abstracts and produces citations. It does **not** download full texts.

> ⚠️ **Read "Terms of use and your own risk" below before using this.** The site rate-limits automated access, and this project's way of accessing it is deliberately conservative.

Tool descriptions and tool output are in Traditional Chinese, because that is the language of the data (and of the site's field names, which are quoted below with an English gloss).

## Tools

| Tool | What it does |
|---|---|
| `ndltd_search(query, page=1, fields="title,keyword", match="exact", sort="", per_page=0, compact=True)` | Search. Returns the total hit count and, for each hit on the page: title, student, advisor, school and department, graduation year, degree, field/category, counts of citations / views / downloads / favorites, and the thesis ID (the counts and the field come from the same page, so they cost no extra request). **The default output is compact (`compact=True`): one line per hit, columns separated by `｜`, a legend line at the top, and the hint for hits without a thesis ID written once at the end.** `compact=False` gives a multi-line layout that is easier to read but much longer. `fields`: `title` (論文名稱), `author` (研究生), `advisor` (指導教授), `committee` (口試委員), `keyword`, `abstract`, `references`, comma separated, or `all`. `match`: `exact` (default), `fuzzy`, `homophone`, `synonym`. **None of the non-exact modes is recommended as a default**: `fuzzy` returns a huge number of unrelated hits for common words (measured: 330,000+ hits for 「微積分」, calculus); `synonym` returns about 50 % more hits mixed with unrelated theses; `homophone` was tried once and returned the same as `exact`. The output carries a warning. `sort`: empty = the site's default (relevance); `year_desc` (newest first), `year_asc`, `cited_desc`, `views_desc`, `downloads_desc`, `title_asc`, `author_asc`, `school_asc` and so on. Sorting costs one extra request (`relevance` equals the default and costs nothing). **The site offers no sorting when a result set has more than 2000 hits** (the page says 「超過排序上限 2000 筆」, "over the sort limit of 2000"); the results then stay in the default order and the output says "sort not applied". Within 2000 hits sorting was verified. `per_page`: 10 / 20 / 30 / 50 / 100; 0 (default) = the site's default (20 for guests, 10 was seen for logged-in members). Changing it is merged with `sort` into a single extra request (measured: 「微積分」 with `per_page=50` + `year_desc` in one post gave 50 rows, years 114…107 descending, 4 pages; when the page size changes the page sets the hidden field `SubmitChangePage` to 1 — without it the site ignores the size; confirmed with `ndltd_search` itself, 50 rows). The "per page N" in the output header is the size the site's page actually shows; if the site did not apply your request, the header says so. When you read an abstract or a citation by `query` + `index`, `sort` and `per_page` must be the same as in the search. |
| `ndltd_abstract(thesis_id)` or `ndltd_abstract(query, index, fields, match, sort, per_page, expected_title)` | Full bibliographic record + Chinese and English abstracts + keywords; optionally `include_toc` (table of contents) and `include_references`, both from the same page at no extra request. For theses that the list shows without an ID (typically embargoed full text), read them by the original query and the hit's serial number (`fields`, `match`, `sort` and `per_page` must be the same as in the search). Passing `expected_title` is recommended (the hint in the search output already includes it): if the title that comes back differs, the call fails instead of returning another thesis. |
| `ndltd_export_full(query, …, confirm=True)` | **Experimental, absent by default, for logged-in members of the library only**: one page of results as bibliography + Chinese and English abstracts in a single go. See the next section. |
| `ndltd_cite(thesis_id)` or `ndltd_cite(query, index, …, expected_title, format="apa")` | Citation of a thesis: `apa` (APA 7 in Chinese style: `Author（year）。*Title*〔碩士論文，School〕。臺灣博碩士論文知識加值系統。URL`), `bibtex` (`@mastersthesis` / `@phdthesis`), `ris` (`TY - THES`); importable into Zotero, Mendeley and EndNote. The year is the publication year (Gregorian). **APA supports Chinese-named authors only** (a romanised name would need conversion to "Family, G.-S.", which is error-prone, so use bibtex / ris then); an English title is fine; without a permanent URL the reference simply omits it. Citations use only fields already on the record page — please check them against the page. |

## Usage walkthrough

The output below was taken from the real site in November 2026 (excerpt; the bibliographic data comes from the library's public search results).

**Search, sorted by number of citations**: `ndltd_search(query="微積分", sort="cited_desc", per_page=20)`

```
共 185 筆；第 1 頁（本頁 20 筆）｜欄位：論文名稱、關鍵詞｜比對：exact｜排序：cited_desc｜每頁 20 筆
欄位：序號｜篇名｜研究生｜指導教授｜學校 系所 學年度 學位｜學門／學類｜被引用/點閱/下載/收藏｜論文編號
1｜台灣地區大學生微積分學習態度的研究｜黃冠仁｜陳福祥｜國立交通大學 應用數學系所 94 碩士｜數學及統計學門／數學學類｜28/1153/215/4｜（無編號）
2｜自我效能、成就目標、考試焦慮、學習策略對數學學習成就的影響- 以中央大學微積分聯合教學為例｜謝書旻｜蕭嘉璋｜國立中央大學 數學系 102 碩士｜數學及統計學門／數學學類｜23/1381/545/1｜102NCU05479004
…
標示「（無編號…）」的沒有論文編號；讀摘要或引用請用 ndltd_abstract／ndltd_cite(query="微積分", index=<序號>, sort="cited_desc", per_page=20, expected_title=<該筆的篇名>)。
```

In English: "185 hits; page 1 (20 on this page) | fields: title, keyword | match: exact | sort: cited_desc | 20 per page". The legend line reads: serial number | title | student | advisor | school, department, year, degree | field / category | citations / views / downloads / favorites | thesis ID. The last line says: hits marked "（無編號…）" have no thesis ID; to read the abstract or the citation, call `ndltd_abstract` / `ndltd_cite` with the query, `index=<serial number>`, the same sort and page size, and `expected_title=<that hit's title>`.

**Read a thesis that has no ID**: pass the same conditions as in the hint, plus `expected_title`; if the serial number does not lead to the same thesis, the call fails:

```
ndltd_abstract(query="微積分", index=1, sort="cited_desc", per_page=20, expected_title="台灣地區大學生微積分學習態度的研究")
```

**Read a thesis that has an ID, with its table of contents**: `ndltd_abstract(thesis_id="102NCU05479004", include_toc=True)`

**Cite**: `ndltd_cite(thesis_id="113NCCU5507018", format="apa")`

```
林佩昀（2025）。*ScoreRAG：基於檢索增強生成與一致性相關性評分的結構化新聞生成框架*〔碩士論文，國立政治大學〕。臺灣博碩士論文知識加值系統。https://hdl.handle.net/11296/967g89
```

`format` can also be `bibtex` or `ris`, for Zotero, Mendeley and EndNote.

**Find by advisor**: `ndltd_search(query="蔡炎龍", fields="advisor")` (65 hits when measured).

**Too many hits**: above 2000 hits the site offers no sorting and the output says so; add keywords or change the field to narrow the search first, then sort.

## Experimental: member export (off by default)

`ndltd_export_full` uses the site's own "完整書目" (complete record) export and returns one page of search results as bibliography + Chinese and English abstracts (optionally table of contents and references).
**The site offers it to logged-in library members only** (guests do not get the complete record), so the tool does not exist unless you turn it on explicitly:

```powershell
claude mcp remove ndltd
claude mcp add --scope user -e NDLTD_EXPERIMENTAL_EXPORT=1 ndltd -- uv --directory "<full path of this project>" run --quiet ndltd-mcp
```

- **You log in to the site yourself, in your browser** (this project never handles your account name or password); the tool reuses the login state of that browser tab.
- Every call needs an explicit `confirm=True`, otherwise no request is sent. At most 20 records per call (the site's own export limit is 50).
- About 5 requests per call: search, clear ticks, tick this page, preview, clear ticks (up to 2 more when you give `sort`, `per_page` or `page>1`). That is cheaper than one `ndltd_abstract` per thesis, but the requests carry your member identity. On a blank page or a captcha the tool stops immediately — it does not even send the final "clear ticks".
- The preview's bibliography, Chinese abstract, foreign-language abstract, table of contents and references are matched **by block order** (identical on all 10 records measured). If the site leaves a block out when something is missing, the content could be shifted. The tool only does a light check and adds a note to the output when content plainly does not fit; please compare with the record page.
- The site keeps ticked records in your session across searches, so the tool clears first, checks the tick count the site reports, fetches the preview, clears again, and checks that every title matches the search result; it aborts at the first mismatch.
- **What we do not know**: we could not read the site's terms of use, so whether bulk export is allowed is unknown, your account might be restricted, and the risk is yours. The feature relies on internal requests of the page (`getchecker`, `previewsave`) and will break if the site changes.
- Measured (November 2026): a 10-record preview was 1 request and about 120,000 characters, 9 of the 10 with abstract and references. The tick requests also work without a `cache=` parameter (clear→0, tick 1–3→3, tick 4–5→5, and repeating the same URL gives 5, not 3, so the extension's `cache: "no-store"` works and nothing is served from the HTTP cache). With `ndltd_export_full` itself, 2–3 records came back with bibliography, both abstracts, table of contents and references as real content (the table of contents and references are long, so they are off by default); about 5 requests per call.

## Why a browser extension

The site answers Python HTTP clients with a "Blank page" (rate limit / block), while a person using a browser gets in (and sees an image captcha).
This project **does not pretend to be a browser, does not solve the captcha and does not change IP**; instead, the requests are really made by your own browser:

```
Claude ──stdio──> ndltd-mcp ──127.0.0.1 long-poll──> browser extension ──same-origin fetch──> ndltd tab
```

- You type the captcha yourself in the browser tab; the program never reads the image, never uses OCR and never uses a solving service.
- The HTML stays inside the local MCP process and is parsed there; the conversation only receives the parsed bibliography and abstracts.
- The local bridge binds to `127.0.0.1` only, requires a token header, checks `Host` / `Origin` and sends no CORS headers.
- The extension has no resident page script: it injects a small function into the ndltd tab only when a job arrives, so ordinary browsing of the site is not affected.

## Behaviour and limits (deliberate)

- At least 3 seconds between requests; one page per call; **no automatic multi-page bulk harvesting** (deliberately not built, see below). The one batch exception is the member export above, which is off by default: it returns the abstracts of one page, at most 20 records.
- No full-text download, no caching.
- On a "Blank page" or a captcha it stops and does not retry.
- Errors are reported separately: cannot connect / timeout, page structure does not match, rate-limited, captcha needed. Fields that cannot be parsed are written as 「（無）」 ("none") — nothing is guessed.

## Terms of use and your own risk (please read)

**What we know:**

- The site's [`robots.txt`](https://ndltd.ncl.edu.tw/robots.txt) allows `/cgi-bin` and disallows some paths (for example `/download/`, URLs containing `cache=`, some `dnclcdr` search URLs). This project only goes through the site's search and record pages and never touches the full-text download paths; the plain-HTTP mode's entry URL also deliberately carries no `cache=` parameter (the site's own home-page script adds one — that is the page's behaviour inside the browser, not a request we send separately). But we did not check every rule line by line, so we **cannot guarantee full compliance**.
- On entry the site asks for an image captcha; the reason the page gives is that many people are using the system and it wants to keep the service quality up.
- The site answers programmatic access that does not look like a browser with a blank page (measured; the reason is unknown, it may be rate limiting or a client fingerprint).

**What we do not know:**

- **We could not read the site's terms of use / copyright notice** (the relevant pages were behind the captcha while this was developed; only `robots.txt` was read). So whether this project complies with the site's rules on "automated programs", on reuse of bibliographic data and abstracts, on AI processing and so on is something **the author cannot determine**. Please read the site's terms yourself in a browser, and if in doubt ask your school's library or the National Central Library.
- A late-2024 official letter from the National Central Library says that extending the fields of the system's API is still being evaluated, and that requests for thesis bibliographic data must first consult the Intellectual Property Office (abstracts and similar fields may involve copyright). If you need bibliographic data in bulk, official data is the more suitable route: the NCHC platform has [the bibliographic files of the thesis system, split by academic year, as CSV](https://ark.nchc.org.tw/dataset/best_wish14024).

**The author is not a lawyer and this is not legal advice. You use this project (code, documentation and any output) entirely at your own risk**, including but not limited to your IP address or browser being restricted by the site, and legal liability.

**Please:**

1. Use it for **personal, non-commercial** research reference only; do not reproduce, redistribute or use commercially.
2. Do not use this project to harvest the site's content in bulk, and do not put the bibliographic data or abstracts you obtain into your own database, knowledge base or vector database, or use them to train models.
3. When blocked, stop: do not refresh repeatedly, do not change IP or browser fingerprint to get around it.
4. Rate limiting, honest identification, no disguise and no caching are **only ways to lower the load on the site; they are not permission from the site**.

## Installation

You need [uv](https://docs.astral.sh/uv/) and Chrome (or Firefox).

```powershell
cd <this project>
uv sync
uv run pytest        # offline tests, no network
claude mcp add --scope user ndltd -- uv --directory "<full path of this project>" run --quiet ndltd-mcp
```

### Load the extension (Chrome)

1. Open `chrome://extensions` and turn on "Developer mode".
2. "Load unpacked" → choose this project's `extension` folder. (Chrome prints a harmless warning about `background.scripts`; that key is for Firefox.)
3. Start ndltd-mcp once (call any tool); it creates `%USERPROFILE%\.ndltd_mcp\token`.
4. Open the extension's options page, paste the token and save. (The default port is 8765; change it with the environment variable `NDLTD_BRIDGE_PORT` and keep the options page in sync.)
5. First open `https://ndltd.ncl.edu.tw/` in the browser yourself, type the captcha, **stay on a page with the search box**, then call a tool. When the tool has no usable session it takes the tab to the home page; type the captcha yourself and call again.
6. After changing the extension, press "Reload" on `chrome://extensions`.

### Load in Firefox

Open `about:debugging#/runtime/this-firefox` → "Load Temporary Add-on" → choose `extension/manifest.json` (it must be loaded again after the browser restarts). The token and options steps are the same. Do not load the extension in two browsers at once, or they will fight over the same bridge port.

### When you are blocked with a blank page

Do not refresh repeatedly and do not change IP. First check by hand in a browser whether you can get in; if one browser profile is blocked, clear that site's cookies and data, or use another browser or profile.

### When a tool reports "bridge port unavailable" or "extension not connected"

- **Bridge port unavailable**: another process is holding port 8765, most likely a previous ndltd-mcp that did not exit. The program deliberately does not share the port (on Windows sharing lets jobs go to the wrong process). End the old `ndltd-mcp` (Task Manager or `Get-Process`) and reconnect with `/mcp`.
- **Extension not connected**: check that Chrome is open, the extension is loaded, the token on the options page equals `%USERPROFILE%\.ndltd_mcp\token`, and the port matches; reload the extension after changing it.

## Status

**Verified on the real site (Chrome, November 2026)**

- Search: choosing fields (for example by advisor), page 2, `per_page` (50 rows), compact format and the count columns.
- Sorting (「微積分」, 185 hits): `year_desc` (114, 114, 114, 114, 113…), `year_asc` (69, 73, 74, 75…), `title_asc`, `school_asc`, `author_asc` (Latin names first, then by stroke count), `cited_desc` (28, 23, 19, 15, 14…), `views_desc` (3510, 1659, 1489…), `downloads_desc` (697, 545, 379…) and `views_asc` (14, 18, 18, 19, 46…) all work; the first 10 hits of `downloads_asc` are all 0, which agrees with ascending order but is all ties. The reverse directions work too: `author_desc` (by stroke count, descending: 蘇 22, 嚴 20, 羅 19, 魏 18, 謝 17…), `title_desc` (關 19, 雙 18, 藉 18, 應 17…, Latin titles last), `school_desc`, and `relevance_asc` (its first 5 hits are exactly the last 5 of the default order, hits 185 to 181, reversed). When a result set has more than 2000 hits the site offers no sorting (「人工智慧」, 7,190 hits, once showed no effect for that reason) and the output says so.
- Abstracts: by thesis ID, by query + serial number (theses without an ID), with table of contents and references; APA citation.
- Member export (login required): bibliography + both abstracts + table of contents + references came back as real content; ticking and clearing, no `cache=`, and the stop behaviour.
- Match modes: `synonym` for 「人工智慧」 gave more hits than `exact` (11,057 vs 7,190), the extra ones including unrelated theses; `homophone` for the advisor 「蔡炎龍」 equals `exact` (65 hits), tried once. So no non-exact mode is recommended as a default.
- Bridge: extension long-polling, captcha typed by the user, and after the keep-alive the first request after 10 idle minutes took about 5 seconds.

**Offline tests**: 114 (fake site, fake extension, parsers, citation formats, bridge security checks). RIS / BibTeX were additionally parsed with `rispy` and `bibtexparser` (`tests/test_formats.py`; development dependencies, skipped when not installed).

**Not verified, or only roughly observed**

- Session lifetime was not measured: one logged-in session lasted several hours and was gone overnight (rough observation). When it expires the tool asks you to type the captcha again.
- RIS / BibTeX were not actually imported into Zotero / EndNote.
- Member export: if the site leaves a block out when a thesis lacks a table of contents or a foreign-language abstract, content could be shifted; there is only a light check and no such thesis was tested.
- Firefox: the manifest is compatible, but it was not fully verified.
- Plain-HTTP mode (`NDLTD_MODE=http`): the site currently blocks it (blank page); its captcha form fields were not verified against a real page either. The browser-bridge mode needs neither.
