// Long-polls the local ndltd-mcp bridge, hands each job to the ndltd tab, posts the answer back.
// Only this service worker talks to 127.0.0.1 (content scripts must not: Private Network Access).
const HOME = "https://ndltd.ncl.edu.tw/";
const POLL_GAP_MS = 1500;
let running = false;

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

async function runJob(job) {
  const tabs = await chrome.tabs.query({ url: "https://ndltd.ncl.edu.tw/*" });
  if (!tabs.length) {
    await chrome.tabs.create({ url: HOME, active: true });
    return { ok: false, kind: "nosession", error: "opened ndltd tab" };
  }
  const tab = tabs.find((t) => t.active) || tabs[0];
  await waitComplete(tab.id);
  try {
    const res = await chrome.tabs.sendMessage(tab.id, { type: "job", job });
    if (res && res.kind === "nosession") {
      await chrome.tabs.update(tab.id, { url: HOME, active: true }); // the human sees the captcha here
    }
    return res || { ok: false, kind: "nocontent", error: "no reply from page" };
  } catch (e) {
    // content script gone (page was navigating, or the tab was open before the extension loaded)
    await chrome.tabs.update(tab.id, { url: HOME, active: true });
    return { ok: false, kind: "nosession", error: String(e) };
  }
}

async function loop() {
  if (running) return;
  running = true;
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
  }
}

chrome.runtime.onInstalled.addListener(() => { chrome.alarms.create("keepalive", { periodInMinutes: 0.5 }); loop(); });
chrome.runtime.onStartup.addListener(() => { chrome.alarms.create("keepalive", { periodInMinutes: 0.5 }); loop(); });
chrome.alarms.onAlarm.addListener(() => loop());
chrome.storage.onChanged.addListener(() => loop());
loop();
