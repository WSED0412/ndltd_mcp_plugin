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
    update: async (id, o) => { state.updated.push([id, o.url]); },
    onUpdated: { addListener: noop, removeListener: noop },
  },
  // stands in for the page: answers like pageJob would for the tab's URL
  scripting: { executeScript: async ({ target, args }) => {
    const tab = state.tabs.find((t) => t.id === target.tabId), job = args[0];
    const m = new URL(tab.url).pathname.match(/^(\/cgi-bin\/gs32\/gsweb\.cgi\/ccd=[0-9A-Za-z._~-]+)(?:\/|$)/);
    state.ran.push([tab.id, job.path]);
    if (!m) return [{ result: { ok: false, kind: "nosession" } }];
    if (job.expect && m[1] !== job.expect) return [{ result: { ok: false, kind: "tab_lost" } }];
    return [{ result: { ok: true, status: 200, html: tab.html || "<p>ok</p>", session: m[1] } }];
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
