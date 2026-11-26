// Long-polls the local ndltd-mcp bridge, hands each job to the ndltd tab, posts the answer back.
// Only this service worker talks to 127.0.0.1 (content scripts must not: Private Network Access).
const HOME = "https://ndltd.ncl.edu.tw/";
const POLL_GAP_MS = 1500;
let running = false;
let keepAlive = null;

async function settings() {
  const s = await chrome.storage.local.get({ token: "", port: 8765 });
  return { token: (s.token || "").trim(), port: Number(s.port) || 8765 };
}

async function post(url, token, obj) {
  await fetch(url, {
    method: "POST",
    headers: { "X-Ndltd-Token": token, "Content-Type": "application/json" },
    body: JSON.stringify(obj),
  });
}

function waitComplete(tabId, ms = 15000) {
  return new Promise((resolve) => {
    const t = setTimeout(done, ms);
    function done() { clearTimeout(t); chrome.tabs.onUpdated.removeListener(on); resolve(); }
    function on(id, info) { if (id === tabId && info.status === "complete") done(); }
    chrome.tabs.onUpdated.addListener(on);
    chrome.tabs.get(tabId).then((tab) => { if (tab.status === "complete") done(); }).catch(done);
  });
}

// Injected into the ndltd tab ONLY while a job runs (no resident content script, so ordinary browsing of the
// site is untouched). Must be self-contained: it is serialized into the page. Runs a same-origin fetch under
// the tab's own session (the ccd=XXXX path segment), exactly like the page's own forms. Never reads images.
async function pageJob(job) {
  const m = location.pathname.match(/^(\/cgi-bin\/gs32\/gsweb\.cgi\/ccd=[0-9A-Za-z._~-]+)(?:\/|$)/);
  if (!m) {
    const captcha = ((document.body && document.body.innerText) || "").includes("驗證碼檢查機制");
    return { ok: false, kind: captcha ? "captcha" : "nosession" };
  }
  // a follow-up request must run under the session that holds the operation's result set
  if (job.expect && m[1] !== job.expect) return { ok: false, kind: "tab_lost" };
  // no-store: identical URLs (e.g. the getchecker calls) must always reach the site, never the HTTP cache
  const init = { method: job.method, credentials: "include", cache: "no-store", headers: {} };
  if (job.body != null) {
    init.body = job.body;
    init.headers["Content-Type"] = "application/x-www-form-urlencoded";
  }
  try {
    const r = await fetch(m[1] + job.path, init);
    const html = await r.text();
    return { ok: true, status: r.status, html, session: m[1] };
  } catch (e) {
    return { ok: false, kind: "fetch", error: String(e) };
  }
}

const SESSION_PATH = /\/cgi-bin\/gs32\/gsweb\.cgi\/ccd=[0-9A-Za-z._~-]+(\/|$)/;
// The chained requests of one operation (search -> jump -> record) must share a tab AND a session: the result set
// lives in the session. Both are remembered from the last job that worked (lost when the worker is recycled).
let lastTabId = null;
let lastSession = null;

// Mirrors page_kind() in src/ndltd_mcp/parsers.py: the captcha phrase only counts on a page that is not a result
// list, a record page or an export preview (those echo the query and show abstracts, which may contain the phrase).
function isCaptchaPage(html) {
  if (!html.includes("驗證碼檢查機制")) return false;
  const gap = "(?:\\s|&nbsp;|<[^>]*>)*";
  if (new RegExp("檢索結果共" + gap + "[\\d,]+" + gap + "筆").test(html)) return false;
  return !(html.includes("gs32_levelrecord") || html.includes("tableoutfmt2"));
}

function hasSession(tab) {
  try { return SESSION_PATH.test(new URL(tab.url).pathname); } catch (e) { return false; }
}

function isEntryPage(tab) {
  // the home page, or the login/captcha flow: the human is on their way in, do not interrupt
  try { const p = new URL(tab.url).pathname; return p === "/" || p.includes("/login"); } catch (e) { return false; }
}

function pickTab(tabs) {
  const withSession = tabs.filter(hasSession);
  return (
    withSession.find((t) => t.id === lastTabId) ||
    withSession.find((t) => t.active) ||
    withSession[0] ||
    tabs.find(isEntryPage) ||
    tabs.find((t) => t.active) ||
    tabs[0]
  );
}

async function runJob(job) {
  const tabs = await chrome.tabs.query({ url: "https://ndltd.ncl.edu.tw/*" });
  if (!tabs.length && !job.cont) {
    await chrome.tabs.create({ url: HOME, active: true });
    return { ok: false, kind: "nosession", error: "opened ndltd tab" };
  }
  // A follow-up request (job.cont) goes to the tab its operation started in, or nowhere: another tab has another
  // session and another result set. When nothing is remembered (worker just restarted) the usual choice applies.
  const follow = job.cont && lastTabId !== null && lastSession !== null;
  let tab = follow ? tabs.filter(hasSession).find((t) => t.id === lastTabId) : pickTab(tabs);
  if (!tab) return { ok: false, kind: "tab_lost" };
  if (follow) job = { ...job, expect: lastSession };
  await waitComplete(tab.id);
  tab = await chrome.tabs.get(tab.id);
  let res;
  try {
    const out = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: pageJob, args: [job] });
    res = out && out[0] && out[0].result;
  } catch (e) {
    // the injection itself failed (tab navigating, discarded, ...): that says nothing about the session, so report it
    // as an error and leave the tab alone instead of sending the human back to the home page
    res = { ok: false, kind: "script_error", error: String(e) };
  }
  if (!res) res = { ok: false, kind: "nosession", error: "no result from page" };
  if (res.ok) { lastTabId = tab.id; lastSession = res.session; }
  delete res.session; // stays in the browser
  // A tab already on the entry/captcha flow is left alone (the human may be typing). A tab with no session that
  // is somewhere else, or one whose expired session answered with the captcha page, goes to the entry page once.
  const expired = res.ok && isCaptchaPage(res.html);
  if ((res.kind === "nosession" && !isEntryPage(tab)) || expired) {
    await chrome.tabs.update(tab.id, { url: HOME, active: true });
  }
  return res;
}

async function loop() {
  if (running) return;
  running = true;
  // MV3 recycles an idle service worker after ~30 s, and only the 30 s alarm wakes it again (measured: a job
  // after a quiet spell waited ~55 s). Any extension API call resets the idle timer, so touch one regularly.
  keepAlive = setInterval(() => { chrome.runtime.getPlatformInfo().catch(() => {}); }, 20000);
  try {
    for (;;) {
      const { token, port } = await settings();
      if (!token) return; // not configured yet; the alarm retries
      const base = `http://127.0.0.1:${port}`;
      let r;
      try {
        r = await fetch(`${base}/poll`, { headers: { "X-Ndltd-Token": token } });
      } catch (e) {
        await new Promise((ok) => setTimeout(ok, 5000)); // bridge not running (MCP not started): wait quietly
        continue;
      }
      if (r.status === 403) return; // wrong token: stop until the user fixes the options
      if (r.status === 200) {
        const job = await r.json();
        let res;
        try { res = await runJob(job); } catch (e) { res = { ok: false, kind: "error", error: String(e) }; }
        try { await post(`${base}/result`, token, { id: job.id, ...res }); } catch (e) { /* bridge went away */ }
      }
      await new Promise((ok) => setTimeout(ok, POLL_GAP_MS));
    }
  } finally {
    running = false;
    clearInterval(keepAlive);
  }
}

chrome.runtime.onInstalled.addListener(() => { chrome.alarms.create("keepalive", { periodInMinutes: 0.5 }); loop(); });
chrome.runtime.onStartup.addListener(() => { chrome.alarms.create("keepalive", { periodInMinutes: 0.5 }); loop(); });
chrome.alarms.onAlarm.addListener(() => loop());
chrome.storage.onChanged.addListener(() => loop());
loop();
