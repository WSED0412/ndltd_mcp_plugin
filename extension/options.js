const $ = (id) => document.getElementById(id);
chrome.storage.local.get({ token: "", port: 8765 }).then((s) => { $("token").value = s.token; $("port").value = s.port; });
$("save").addEventListener("click", async () => {
  await chrome.storage.local.set({ token: $("token").value.trim(), port: Number($("port").value) || 8765 });
  $("msg").textContent = "已儲存";
});
