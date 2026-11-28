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
  const RE = /^(?:https:\/\/ndltd\.ncl\.edu\.tw)?(\/cgi-bin\/gs32\/gsweb\.cgi\/ccd=[0-9A-Za-z._~-]+)(?:[\/?#]|$)/;
  let base = (location.pathname.match(RE) || [])[1];
  if (!base) {
    const captcha = ((document.body && document.body.innerText) || "").includes("驗證碼檢查機制");
    if (!captcha) {
      // After the captcha (and some redirects) the site serves a working page at a URL without ccd= (seen
      // 2026-11-28: the search page at gsweb.cgi?...&check=確定). Its own forms and links still carry the session
      // id, so use that when they all name the same one.
      const ids = new Set();
      for (const el of document.querySelectorAll("form[action], a[href]")) {
        const m = (el.getAttribute("action") || el.getAttribute("href") || "").match(RE);
        if (m) ids.add(m[1]);
      }
      if (ids.size === 1) base = ids.values().next().value;
    }
    if (!base) return { ok: false, kind: job.expect ? "tab_lost" : captcha ? "captcha" : "nosession" };
  }
  // a follow-up request must run under the session that holds the operation's result set
  if (job.expect && base !== job.expect) return { ok: false, kind: "tab_lost" };
  // no-store: identical URLs (e.g. the getchecker calls) must always reach the site, never the HTTP cache
  const init = { method: job.method, credentials: "include", cache: "no-store", headers: {} };
  if (job.body != null) {
    init.body = job.body;
    init.headers["Content-Type"] = "application/x-www-form-urlencoded";
  }
  try {
    const r = await fetch(base + job.path, init);
    const html = await r.text();
    return { ok: true, status: r.status, html, session: base };
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

function isSitePage(tab) {
  // a page of the site's program whose URL lost the ccd= (e.g. right after the captcha): pageJob reads the id from it
  try { return new URL(tab.url).pathname.startsWith("/cgi-bin/gs32/gsweb.cgi"); } catch (e) { return false; }
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
    tabs.find((t) => isSitePage(t) && !isEntryPage(t) && t.active) ||
    tabs.find((t) => isSitePage(t) && !isEntryPage(t)) ||
    tabs.find(isEntryPage) ||
    tabs.find((t) => t.active) ||
    tabs[0]
  );
}

async function runJob(job) {
  const tabs = await chrome.tabs.query({ url: "https://ndltd.ncl.edu.tw/*" });
  if (!tabs.length && !job.cont) {
    await chrome.tabs.create({ url: HOME, active: true });
    return { ok: false, kind: "nosession", opened: true, error: "opened ndltd tab" };
  }
  // A follow-up request (job.cont) goes to the tab its operation started in, or nowhere: another tab has another
  // session and another result set. When nothing is remembered (worker just restarted) the usual choice applies.
  const follow = job.cont && lastTabId !== null && lastSession !== null;
  const usable = (t) => hasSession(t) || (isSitePage(t) && !isEntryPage(t));  // pageJob checks the session id itself
  let tab = follow ? tabs.filter(usable).find((t) => t.id === lastTabId) : pickTab(tabs);
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

// ---------------------------------------------------------------- download renaming
// Files the user downloads from ndltd by hand arrive with a generated name (e.g. fb261117094629.pdf). Rename them to
// 論文名稱_研究者.<ext>, reading the two fields from the record page the user is looking at. No request is sent to
// the site: the download itself is the user's own, only its local filename changes.

// Injected into a tab to read the record page's own table. Self-contained (serialized into the page).
function pageMeta() {
  const cell = (label) => {
    for (const th of document.querySelectorAll("th.std1")) {
      if (th.textContent.replace(/[:：\s]+$/, "").trim() !== label) continue;
      const td = th.parentElement && th.parentElement.querySelector("td");
      if (!td) return "";
      return Array.from(td.querySelectorAll("a.slink")).map((a) => a.textContent.trim()).filter(Boolean).join("、")
        || td.textContent.replace(/\s+/g, " ").trim();
    }
    return "";
  };
  return { title: cell("論文名稱"), author: cell("研究生") };
}

function cleanPart(s, max) {
  s = (s || "").replace(/[\\/:*?"<>|\u0000-\u001f]/g, " ").replace(/\s+/g, " ").trim();
  if (s.length > max) s = s.slice(0, max).trim();
  return s.replace(/[. ]+$/, "");
}

// 論文名稱_研究者.ext ; "" when there is no title (then the original name is kept)
function downloadName(meta, original) {
  const title = cleanPart(meta && meta.title, 80);
  if (!title) return "";
  const author = cleanPart(meta && meta.author, 40);
  const m = /\.([0-9A-Za-z]{1,8})$/.exec((original || "").split(/[\\/]/).pop());
  return title + (author ? "_" + author : "") + "." + (m ? m[1].toLowerCase() : "pdf");
}

function isNdltdDownload(item) {
  const hosts = [item.url, item.finalUrl, item.referrer].map((u) => {
    try { return new URL(u).hostname; } catch (e) { return ""; }
  });
  if (hosts.some((h) => h === "ndltd.ncl.edu.tw")) return true;
  return /^fb\d{12}\.\w+$/.test((item.filename || "").split(/[\\/]/).pop()); // the generated name seen in practice
}

const stripHash = (u) => (u || "").split("#")[0];

async function metaForDownload(item) {
  const tabs = await chrome.tabs.query({ url: "https://ndltd.ncl.edu.tw/*" });
  const ref = stripHash(item.referrer);
  const ordered = [
    ...tabs.filter((t) => ref && stripHash(t.url) === ref),
    ...tabs.filter((t) => t.id === lastTabId),
    ...tabs.filter((t) => t.active),
    ...tabs,
  ].filter((t, i, a) => a.findIndex((x) => x.id === t.id) === i);
  for (const t of ordered) {
    try {
      const out = await chrome.scripting.executeScript({ target: { tabId: t.id }, func: pageMeta });
      const meta = out && out[0] && out[0].result;
      if (meta && meta.title) return meta; // only a record page has a 論文名稱 row
    } catch (e) { /* tab not scriptable: try the next */ }
  }
  return null;
}

if (chrome.downloads && chrome.downloads.onDeterminingFilename) { // not available on Firefox
  chrome.downloads.onDeterminingFilename.addListener((item, suggest) => {
    (async () => {
      try {
        if (!isNdltdDownload(item)) return suggest();
        const name = downloadName(await metaForDownload(item), item.filename);
        if (!name) return suggest();
        suggest({ filename: name, conflictAction: "uniquify" });
      } catch (e) { suggest(); }
    })();
    return true; // suggest() is called asynchronously
  });
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
