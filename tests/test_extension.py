"""The extension's own decisions (which tab a job goes to, what counts as a captcha page), run in Node against a
stubbed chrome API. Skipped when Node is not installed."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const noop = () => {};
const state = { tabs: [], created: [], updated: [], ran: [] };
const chrome = {
  storage: { local: { get: async () => ({ token: "", port: 8765 }) }, onChanged: { addListener: noop } },
  runtime: { onInstalled: { addListener: noop }, onStartup: { addListener: noop }, getPlatformInfo: async () => ({}) },
  alarms: { create: noop, onAlarm: { addListener: noop } },
  tabs: {
    query: async () => state.tabs,
    get: async (id) => state.tabs.find((t) => t.id === id),
    create: async (o) => { state.created.push(o.url); },
    update: async (id, o) => { state.updated.push([id, o.url]); const t = state.tabs.find((x) => x.id === id); if (t) t.url = o.url; },
    onUpdated: { addListener: noop, removeListener: noop },
  },
  downloads: { onDeterminingFilename: { addListener: (f) => { state.onDownload = f; } } },
  // runs the REAL pageJob against a stand-in page built from the tab: its URL, the session links on the page
  // (tab.refs), its visible text (tab.text) and the site's answer to the job's fetch (tab.html)
  scripting: { executeScript: async ({ target, args, func }) => {
    const tab = state.tabs.find((t) => t.id === target.tabId), job = args && args[0];
    if (func && func.name === "pageMeta") return [{ result: tab.meta || { title: "", author: "" } }];
    state.ran.push([tab.id, job.path]);
    ctx.location = { pathname: new URL(tab.url).pathname };  // pageJob runs inside the vm context
    ctx.document = {
      body: { innerText: tab.text || "" },
      documentElement: { innerHTML: tab.text || "" },
      querySelectorAll: () => (tab.refs || []).map((r) => ({ getAttribute: (n) => (n === "href" ? r : null) })),
    };
    ctx.fetch = async () => ({ status: 200, text: async () => tab.html || "<p>ok</p>" });
    return [{ result: await func(job) }];
  } },
};
const ctx = vm.createContext({ chrome, state, URL, setTimeout, clearTimeout, setInterval, clearInterval, console, fetch: noop });
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), ctx);
vm.runInContext("(async () => {" + process.argv[3] + "})().then((v) => console.log(JSON.stringify(v)))", ctx);
"""
S = "https://ndltd.ncl.edu.tw/cgi-bin/gs32/gsweb.cgi/ccd={}/search"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")


def run(tmp_path, script):
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS, encoding="utf-8")
    out = subprocess.run(["node", str(harness), str(ROOT / "extension" / "background.js"), script],
                         capture_output=True, text=True, encoding="utf-8", timeout=60, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def tabs(*pairs):
    return json.dumps([{"id": i, "url": u, "status": "complete", "active": False} for i, u in pairs])


def test_captcha_page_detection_matches_the_python_rule(tmp_path):
    pages = [
        "<form>驗證碼檢查機制</form>",
        "檢索結果共<span>&nbsp;12&nbsp;</span>筆 驗證碼檢查機制",
        '<div id="gs32_levelrecord">驗證碼檢查機制</div>',
        '<table class="tableoutfmt2">驗證碼檢查機制</table>',
        "<p>nothing special</p>",
    ]
    got = run(tmp_path, f"return {json.dumps(pages, ensure_ascii=False)}.map(isCaptchaPage);")
    assert got == [True, False, False, False, False]
    from ndltd_mcp import parsers
    assert got == [parsers.page_kind(p) == "captcha" for p in pages]


def test_follow_up_job_stays_in_the_tab_and_session_of_its_operation(tmp_path):
    got = run(tmp_path, f"""
        state.tabs = {tabs((1, S.format("AAA")), (2, S.format("BBB")))};
        const a = await runJob({{ path: "/search", cont: false }});
        const b = await runJob({{ path: "/record?r1=1", cont: true }});
        return [a.ok, b.ok, state.ran, "session" in a];""")
    assert got == [True, True, [[1, "/search"], [1, "/record?r1=1"]], False]


@pytest.mark.parametrize("after", [
    tabs((2, S.format("BBB"))),                                 # the tab was closed; another session is open
    tabs((1, "https://ndltd.ncl.edu.tw/"), (2, S.format("BBB"))),  # the tab went back to the home page
    tabs((1, S.format("NEW")), (2, S.format("BBB"))),           # same tab, new session (the human came in again)
    "[]",                                                       # no ndltd tab at all
])
def test_follow_up_job_is_refused_when_its_tab_or_session_is_gone(tmp_path, after):
    got = run(tmp_path, f"""
        state.tabs = {tabs((1, S.format("AAA")), (2, S.format("BBB")))};
        await runJob({{ path: "/search", cont: false }});
        state.tabs = {after};
        const b = await runJob({{ path: "/record?r1=1", cont: true }});
        return [b.ok, b.kind, state.created, state.updated];""")
    assert got == [False, "tab_lost", [], []]  # refused, and no tab is opened or sent anywhere


def test_a_new_search_may_use_another_tab_and_old_servers_still_work(tmp_path):
    got = run(tmp_path, f"""
        state.tabs = {tabs((1, S.format("AAA")), (2, S.format("BBB")))};
        await runJob({{ path: "/search", cont: false }});
        state.tabs = {tabs((2, S.format("BBB")))};
        const a = await runJob({{ path: "/search", cont: false }});
        const b = await runJob({{ path: "/record?r1=1" }});  // a server without the cont flag
        return [a.ok, b.ok, state.ran.slice(1)];""")
    assert got == [True, True, [[2, "/search"], [2, "/record?r1=1"]]]


def test_follow_up_job_with_nothing_remembered_falls_back_to_the_usual_choice(tmp_path):
    got = run(tmp_path, f"""
        state.tabs = {tabs((2, S.format("BBB")))};
        const b = await runJob({{ path: "/record?r1=1", cont: true }});  // e.g. right after the worker restarted
        return [b.ok, state.ran];""")
    assert got == [True, [[2, "/record?r1=1"]]]


def test_result_page_quoting_the_captcha_phrase_does_not_send_the_tab_home(tmp_path):
    got = run(tmp_path, f"""
        state.tabs = {tabs((1, S.format("AAA")))};
        state.tabs[0].html = "檢索結果共 3 筆 驗證碼檢查機制";
        await runJob({{ path: "/search", cont: false }});
        state.tabs[0].html = "<form>驗證碼檢查機制</form>";
        await runJob({{ path: "/search", cont: false }});
        return state.updated;""")
    assert got == [[1, "https://ndltd.ncl.edu.tw/"]]  # only the real captcha page did


# ---------------------------------------------------------------- download renaming

DL = """
const suggestions = [];
const dl = (item) => new Promise((ok) => { state.onDownload(item, (s) => { suggestions.push(s || null); ok(); }); });
"""


def test_download_name_is_title_underscore_author_and_filesystem_safe(tmp_path):
    got = run(tmp_path, r"""return [
        downloadName({ title: "大一學生微積分學習策略之研究", author: "楊嘉勝" }, "fb261117094629.pdf"),
        downloadName({ title: 'A/B: "x"?*<>|', author: "甲、乙" }, "C:" + String.fromCharCode(92) + "x" + String.fromCharCode(92) + "f.PDF"),
        downloadName({ title: "題目", author: "" }, "noext"),
        downloadName({ title: "x".repeat(200), author: "y" }, "a.pdf").length,
        downloadName({ title: "", author: "楊" }, "a.pdf"),
        downloadName(null, "a.pdf"),
    ];""")
    assert got == ["大一學生微積分學習策略之研究_楊嘉勝.pdf", "A B x_甲、乙.pdf", "題目.pdf", 80 + 2 + 4, "", ""]


def test_download_from_ndltd_is_renamed_from_the_record_page_being_viewed(tmp_path):
    got = run(tmp_path, DL + f"""
        state.tabs = {tabs((1, S.format("AAA")), (2, S.format("BBB")))};
        state.tabs[0].meta = {{ title: "別篇", author: "別人" }};
        state.tabs[1].meta = {{ title: "正在看的論文", author: "研究者" }};
        await dl({{ url: "https://ndltd.ncl.edu.tw/x", referrer: "{S.format("BBB")}#top", filename: "fb261117094629.pdf" }});
        return suggestions;""")
    assert got == [{"filename": "正在看的論文_研究者.pdf", "conflictAction": "uniquify"}]


def test_other_downloads_and_pages_without_a_record_keep_their_name(tmp_path):
    got = run(tmp_path, DL + f"""
        state.tabs = {tabs((1, S.format("AAA")))};
        state.tabs[0].meta = {{ title: "不該用", author: "x" }};
        await dl({{ url: "https://example.com/f.pdf", referrer: "https://example.com/", filename: "f.pdf" }});
        state.tabs[0].meta = {{ title: "", author: "" }};  // a result list / home page: no 論文名稱 row
        await dl({{ url: "https://ndltd.ncl.edu.tw/x", referrer: "", filename: "fb261117094629.pdf" }});
        return suggestions;""")
    assert got == [None, None]


# ---------------------------------------------------------------- the page job itself (session id from the page)

PAGE_JOB = """
const page = (path, refs, text) => {
  globalThis.location = { pathname: path };
  globalThis.document = {
    body: { innerText: text || "" },
    documentElement: { innerHTML: text || "" },
    querySelectorAll: () => refs.map((r, i) => ({ getAttribute: (n) => (n === (i % 2 ? "href" : "action") ? r : null) })),
  };
};
globalThis.fetch = async (u) => ({ status: 200, text: async () => "fetched " + u });
"""


def test_page_job_takes_the_session_id_from_the_page_when_the_url_has_none(tmp_path):
    # after the captcha the site shows the search page at gsweb.cgi?...&check=確定, with ccd= only in its forms/links
    got = run(tmp_path, PAGE_JOB + """
        const out = [];
        page("/cgi-bin/gs32/gsweb.cgi", ["/cgi-bin/gs32/gsweb.cgi/ccd=Zy61c8/search", "https://ndltd.ncl.edu.tw/cgi-bin/gs32/gsweb.cgi/ccd=Zy61c8/?o=x"]);
        out.push(await pageJob({ method: "GET", path: "/record?r1=1" }));
        page("/cgi-bin/gs32/gsweb.cgi", ["/cgi-bin/gs32/gsweb.cgi/ccd=AAA/search", "/cgi-bin/gs32/gsweb.cgi/ccd=BBB/x"]);
        out.push((await pageJob({ method: "GET", path: "/x" })).kind);  // two ids: do not guess
        page("/cgi-bin/gs32/gsweb.cgi/login", ["/cgi-bin/gs32/gsweb.cgi/ccd=AAA/x"], "驗證碼檢查機制");
        out.push((await pageJob({ method: "GET", path: "/x" })).kind);  // the captcha page is still a captcha
        page("/cgi-bin/gs32/gsweb.cgi", []);
        out.push((await pageJob({ method: "GET", path: "/x", expect: "/cgi-bin/gs32/gsweb.cgi/ccd=AAA" })).kind);
        return out;""")
    assert got[0] == {"ok": True, "status": 200, "html": "fetched /cgi-bin/gs32/gsweb.cgi/ccd=Zy61c8/record?r1=1",
                      "session": "/cgi-bin/gs32/gsweb.cgi/ccd=Zy61c8"}
    assert got[1:] == ["nosession", "captcha", "tab_lost"]


AFTER_CAPTCHA = "https://ndltd.ncl.edu.tw/cgi-bin/gs32/gsweb.cgi?randomimg=x&check=1"
REFS = '["/cgi-bin/gs32/gsweb.cgi/ccd=Zy61c8/search", "https://ndltd.ncl.edu.tw/cgi-bin/gs32/gsweb.cgi/ccd=Zy61c8/x"]'


def test_a_site_page_without_ccd_in_its_url_is_used_not_sent_home(tmp_path):
    got = run(tmp_path, f"""
        state.tabs = {tabs((1, AFTER_CAPTCHA))};
        state.tabs[0].refs = {REFS};
        const a = await runJob({{ path: "/search", cont: false }});
        const b = await runJob({{ path: "/record?r1=1", cont: true }});  // the follow-up stays there too
        return [a.ok, b.ok, state.ran, state.created, state.updated];""")
    assert got == [True, True, [[1, "/search"], [1, "/record?r1=1"]], [], []]


def test_an_expired_tab_is_dropped_for_the_working_one_without_ccd_in_its_url(tmp_path):
    # tab 1 still shows ccd= in its URL but its session expired; tab 2 is where the human just passed the captcha
    got = run(tmp_path, f"""
        state.tabs = {tabs((1, S.format("OLD")), (2, AFTER_CAPTCHA))};
        state.tabs[1].refs = {REFS};
        state.tabs[0].html = "<form>驗證碼檢查機制</form>";
        await runJob({{ path: "/search", cont: false }});   // tab 1 answers with the captcha page: sent home
        const b = await runJob({{ path: "/search", cont: false }});
        return [state.ran, state.updated, b.ok];""")
    assert got == [[[1, "/search"], [2, "/search"]], [[1, "https://ndltd.ncl.edu.tw/"]], True]


def test_a_working_tab_without_ccd_in_its_url_is_preferred_once_it_has_worked(tmp_path):
    got = run(tmp_path, f"""
        state.tabs = {tabs((2, AFTER_CAPTCHA))};
        state.tabs[0].refs = {REFS};
        await runJob({{ path: "/search", cont: false }});    // tab 2 works and is remembered
        state.tabs = {tabs((1, S.format("OTHER")), (2, AFTER_CAPTCHA))};
        state.tabs[1].refs = {REFS};
        await runJob({{ path: "/search", cont: false }});    // a tab with ccd= in its URL appears: tab 2 is kept
        return state.ran;""")
    assert got == [[2, "/search"], [2, "/search"]]


def test_an_entry_page_is_left_alone_and_said_so(tmp_path):
    got = run(tmp_path, f"""
        state.tabs = {tabs((1, "https://ndltd.ncl.edu.tw/cgi-bin/gs32/gsweb.cgi/login?o=dwebmge"))};
        const a = await runJob({{ path: "/search", cont: false }});
        state.tabs = {tabs((1, "https://ndltd.ncl.edu.tw/somewhere/else"))};
        const b = await runJob({{ path: "/search", cont: false }});
        return [a.kind, !!a.home, b.kind, !!b.home, state.updated];""")
    assert got == ["nosession", False, "nosession", True, [[1, "https://ndltd.ncl.edu.tw/"]]]


def test_a_blank_page_is_reported_and_the_tab_left_alone(tmp_path):
    got = run(tmp_path, f"""
        state.tabs = {tabs((1, AFTER_CAPTCHA))};
        state.tabs[0].text = "<!-- this is a Blank page -->";
        const a = await runJob({{ path: "/search", cont: false }});
        return [a.kind, state.updated];""")
    assert got == ["blank", []]


def test_expired_page_detection_matches_the_python_rule(tmp_path):
    from ndltd_mcp import parsers
    pages = [(ROOT / "tests" / "fixtures" / "expired_session.html").read_text(encoding="utf-8"),
             "檢索結果共<span>&nbsp;3&nbsp;</span>筆 <h1>Error</h1>", "<p>ok</p>"]
    got = run(tmp_path, f"return {json.dumps(pages, ensure_ascii=False)}.map(isExpiredPage);")
    assert got == [True, False, False]
    assert got == [parsers.page_kind(p) == "expired" for p in pages]


def test_a_tab_whose_session_expired_is_sent_home_and_forgotten(tmp_path):
    page = json.dumps((ROOT / "tests" / "fixtures" / "expired_session.html").read_text(encoding="utf-8"))
    got = run(tmp_path, f"""
        state.tabs = {tabs((1, AFTER_CAPTCHA), (2, S.format("BBB")))};
        state.tabs[0].refs = {REFS};
        state.tabs[0].active = true;
        await runJob({{ path: "/search", cont: false }});    // tab 2 has ccd= in its URL, so it goes first and works
        state.tabs[1].html = {page};                         // overnight, tab 2's session expires
        await runJob({{ path: "/search", cont: false }});    // ... it answers with the Error page: sent home
        const c = await runJob({{ path: "/search", cont: false }});
        return [state.ran, state.updated, c.ok];""")
    assert got == [[[2, "/search"], [2, "/search"], [1, "/search"]], [[2, "https://ndltd.ncl.edu.tw/"]], True]
