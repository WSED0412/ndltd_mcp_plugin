// Runs inside the ndltd tab. Executes one job as a same-origin fetch under the tab's own session
// (the ccd=XXXX path segment), exactly like the page's own forms do. Never looks at images.
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || msg.type !== "job") return;
  const m = location.pathname.match(/^(\/cgi-bin\/gs32\/gsweb\.cgi\/ccd=[0-9A-Za-z]+)(?:\/|$)/);
  if (!m) {
    const captcha = (document.body && document.body.innerText || "").includes("驗證碼檢查機制");
    sendResponse({ ok: false, kind: captcha ? "captcha" : "nosession" });
    return;
  }
  const job = msg.job;
  const init = { method: job.method, credentials: "include", headers: {} };
  if (job.body != null) {
    init.body = job.body;
    init.headers["Content-Type"] = "application/x-www-form-urlencoded";
  }
  fetch(m[1] + job.path, init)
    .then(async (r) => {
      const html = await r.text();
      sendResponse({ ok: true, status: r.status, html });
      // an expired session answers with the captcha page: bring the human to it, once
      if (html.includes("驗證碼檢查機制")) setTimeout(() => { location.href = location.origin + "/"; }, 300);
    })
    .catch((e) => sendResponse({ ok: false, kind: "fetch", error: String(e) }));
  return true; // async response
});
