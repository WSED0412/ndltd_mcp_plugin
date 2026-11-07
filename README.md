# ndltd-mcp

臺灣博碩士論文知識加值系統（國圖 NDLTD，<https://ndltd.ncl.edu.tw/>）的**本機** MCP server：只查書目與摘要，不下載全文。

| Tool | 用途 |
|---|---|
| `ndltd_search(query, page=1)` | 搜尋論文名稱與關鍵詞。回傳總筆數＋本頁每筆（每頁 20 筆）的篇名、研究生、指導教授、學校系所、學年度、論文編號。 |
| `ndltd_abstract(thesis_id)` 或 `ndltd_abstract(query, index)` | 完整書目＋中英文摘要＋關鍵詞。列表沒有論文編號的論文（全文延後公開者），用原搜尋詞與搜尋結果的序號讀取。 |

## 為什麼要有 Chrome 擴充功能

站方對 Python 的 HTTP 客戶端回「Blank page」（限流／封鎖），但真人用 Chrome 可以進站（會出現圖形驗證碼）。
本專案**不偽裝瀏覽器、不辨識驗證碼、不換 IP**，而是讓請求真的由你自己的 Chrome 發出：

```
Claude ──stdio──> ndltd-mcp ──127.0.0.1 long-poll──> Chrome 擴充功能 ──同源 fetch──> ndltd 分頁
```

- 驗證碼由你本人在瀏覽器分頁輸入；程式不讀圖片、不 OCR、不用打碼服務。
- 回應的 HTML 只留在本機 MCP 程序內解析，對話只收到整理後的書目與摘要。
- 本機橋接口只綁 `127.0.0.1`，要求 token 標頭，檢查 `Host`／`Origin`，不送任何 CORS 標頭。

## 限制（刻意的）

- 請求間隔至少 3 秒；每次呼叫只抓一頁；不做全文下載、不做快取。
- 遇到「Blank page」或驗證碼就停，不自動重試。
- 僅供個人、非商業的研究參考。使用前請自行閱讀站方的使用規範；**使用風險自負**，這不是法律意見。

## 安裝

需要 [uv](https://docs.astral.sh/uv/) 與 Chrome。

```powershell
cd <本專案資料夾>
uv sync
uv run pytest        # 離線測試，不連網
claude mcp add ndltd -- uv --directory "<專案資料夾完整路徑>" run ndltd-mcp
```

### 載入擴充功能

1. Chrome 開 `chrome://extensions`，開啟「開發人員模式」。
2. 「載入未封裝項目」→ 選本專案的 `extension` 資料夾。
3. 先啟動一次 ndltd-mcp（呼叫任一工具即可），它會產生 `%USERPROFILE%\.ndltd_mcp\token`。
4. 開擴充功能的「選項」頁，貼上 token，儲存。（預設埠 8765；可用環境變數 `NDLTD_BRIDGE_PORT` 更改，選項頁要同步。）
5. 保持 Chrome 開著。第一次呼叫工具時會開出國圖分頁；若出現驗證碼，請你本人輸入，進到搜尋頁後再呼叫一次。

### 用 Firefox 載入

站方可能對特定瀏覽器設定檔限流（實測：同一個 IP，Firefox 正常、Chrome 被擋成空白頁）。擴充功能同時支援 Firefox：
開 `about:debugging#/runtime/this-firefox` →「載入暫時性附加元件」→ 選 `extension/manifest.json`。
token 與選項頁的步驟相同（Firefox 的暫時性附加元件在重開瀏覽器後需重新載入）。

### 被擋成空白頁時

不要重新整理、不要換 IP。先用瀏覽器手動確認自己能否進站；Chrome 被擋時可清除該網站的 cookie 與資料，或改用 Firefox。

## 狀態

- 解析器、橋接口、客戶端邏輯都有離線測試（假站台與假擴充功能）。
- 2026-11-08 在真實 Chrome 做過端到端驗證：搜尋（含第 2 頁）、用論文編號讀摘要、用「搜尋詞＋序號」讀沒有編號的論文，皆成功。
- 驗證碼表單欄位（純 HTTP 模式用）沒有用真實頁面驗證過；瀏覽器橋接模式不需要。
- `NDLTD_MODE=http` 可改用純 HTTP 客戶端，但目前站方會擋（空白頁）。
- Chrome 會對 manifest 的 `background.scripts` 印出無害的警告（該欄位是給 Firefox 用的）。
