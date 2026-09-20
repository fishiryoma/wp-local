# WordPress 靜態網站建置說明

設計並實作 WordPress 靜態化架構，將內容經 Python 自動化管線轉出，部署於 Cloudflare Pages/R2/Workers 邊緣網路，取代原生動態站台。

## 架構

| 服務                                  | 用途                                 |
| ------------------------------------- | ------------------------------------ |
| Cloudflare Pages                      | 靜態 HTML / CSS / JS                 |
| Cloudflare R2 (`your-uploads-bucket`) | 圖片和媒體檔案                       |
| Cloudflare Worker (`your-site-name`)  | 路由：圖片請求 → R2，其他 → Pages    |
| Local WP (`your-site-local.local`)    | 本地 WordPress，用於撰寫內容和 build |

所有站台專屬的網域、bucket、專案名稱都透過 `.env` 設定（見 `.env.example`），
下面範例裡的 `your-site-local.local`、`your-domain.com` 等請替換成你自己的值。

---

## 發文與更新流程

兩種情境：**你改的東西只影響一篇文章，還是會影響全站？**

|                            | 快速 build                      | 完整 build               |
| -------------------------- | ------------------------------- | ------------------------ |
| 指令                       | `python new-post.py <文章網址>` | `python build-static.py` |
| 重建範圍                   | 該文章 + 首頁 + 它的分類/標籤頁 | 全站所有頁面             |
| 圖片壓縮 + 同步 R2         | ✅                              | ✅                       |
| themes/plugins/wp-includes | ❌ 不複製                       | ✅ 複製                  |
| DB 備份檢查                | ✅                              | ✅                       |
| 自動部署                   | ✅                              | ✅                       |
| 耗時                       | 約 1 分鐘                       | 數分鐘～數十分鐘         |

兩者都會**自動部署**

---

### 情境 1：快速 build —— 改一篇文章（最常用）

**何時用：** 新增文章，或修改某一篇文章的內容／圖片。

1. 開啟 Local WP，在 WordPress 後台寫文章或編輯並更新

2. **只有「新增文章」時**要先刷新網址清單：瀏覽器開啟

    ```
    http://your-site-local.local/get-all-urls.php
    ```

    等待顯示 `"status": "done"`。編輯既有文章可跳過這步。

3. 執行：

    ```powershell
    cd "<你的專案根目錄>"
    python new-post.py http://your-site-local.local/你的文章網址/
    ```

[new-post.py](new-post.py) **會跑**：

1. **DB 備份檢查**（`backup-db.py`）——距上次備份 ≥ 30 天才真的備份，否則只比對日期、幾乎不花時間。備份失敗只警告，不會中止發文
2. **壓縮新圖片**（`compress-images.py`，細節見下方「圖片壓縮與上傳到 R2」）
3. **同步圖片到 Cloudflare R2**（`sync-images.py`，細節見下方「圖片壓縮與上傳到 R2」）
4. **Analytics 同步**（`sync-analytics.py`）——由 `quick-publish.py` 內部在建置 HTML 前自動呼叫，非獨立步驟
5. **Build HTML**（`quick-publish.py`）——只重建**該文章本身 + 首頁（第 1、2 頁）+ 該文章所屬的分類/標籤彙整頁**
6. **部署到 Cloudflare Pages**（`deploy-pages.py`）

**會跳過**：

- 全站其他頁面的 HTML（沒被這篇文章影響的頁面完全不動）
- themes / plugins / wp-includes 靜態檔案複製

---

### 情境 2：完整 build —— 改全站設定、主題、外掛，或第一次建置

**何時用：**

- 改的是**會出現在每一頁**的全站設定（網站標題、選單、小工具、footer 等），情境 1 只重建單篇相關頁面不夠涵蓋
- 換主題或外掛
- 第一次建置整個網站

1. 開啟 Local WP，在 WordPress 後台修改設定

2. **這段期間若有新增或刪除文章／頁面**，先刷新網址清單（只改設定可跳過）：

    ```
    http://your-site-local.local/get-all-urls.php
    ```

    等待 `"status": "done"`。這會查詢 WordPress 資料庫，把所有公開頁面的 URL 輸出到 `wp-content/uploads/all-urls.json`。

3. 執行：

    ```powershell
    cd "<你的專案根目錄>"

    # 完整重建（含 themes/plugins/wp-includes）
    python build-static.py

    # 只重抓 HTML，保留現有的 CSS/JS/字型（快約 3 倍）
    python build-static.py --html-only

    # 建完先不部署，想自己檢查產出時用
    python build-static.py --no-deploy
    ```

[build-static.py](build-static.py) **會跑**：

1. **DB 備份檢查**（同情境 1，≥ 30 天才真的備份）
2. **Analytics 同步**（`sync-analytics.py`）
3. **壓縮新圖片**（`compress-images.py`）
4. **同步圖片到 Cloudflare R2**（`sync-images.py`）
5. **取得全站 URL 清單**、清理 `deploy/`
6. **抓取全部頁面的 HTML**，並複製 themes / plugins / wp-includes
7. **部署到 Cloudflare Pages**（`deploy-pages.py`）

**會跳過**：加了 `--html-only` 時跳過第 6 步的靜態檔複製（只重抓 HTML）。

**什麼時候用 `--html-only`：** 改的是 WordPress 設定，但主題／外掛的檔案本身沒有變動。

---

## 其他腳本

上面兩個情境已經涵蓋日常所有需求，這區是進階／救援用的單一步驟腳本。

### `deploy-pages.py` — 只做部署

```powershell
python deploy-pages.py
```

把現有的 `deploy/` 推上 Cloudflare Pages（production）。使用時機：

- `build-static.py --no-deploy` 之後要補部署
- `build-static.py` 因為有頁面抓取失敗而跳過部署，你確認過 `_failed_urls.txt` 並修正後補部署
- 單獨跑了 `quick-publish.py` 之後補部署

### `quick-publish.py` — 只重建單篇文章的 HTML

```powershell
python quick-publish.py http://your-site-local.local/你的文章網址/
python deploy-pages.py    # 記得自己部署
```

這支是情境 1 的第 4 步，也可以單獨執行。它**不壓縮圖片、不同步 R2、不做 DB 備份、也不部署**。

> ⚠️ 只有在**確定完全沒動過任何圖片**、想省下圖片比對時間時才用。判斷錯了（其實動了圖片卻用這支）→ 圖片永遠不會上傳到 R2，線上直接破圖。另外長期只用這支會讓 30 天 DB 備份失效。**日常請一律用情境 1。**

### `cloudflare-worker/` — Cloudflare Worker 程式碼

不屬於發文流程，只有改路由邏輯本身時才需要處理。部署設定請複製 `wrangler.toml.example` 為
`wrangler.toml` 並填入你自己的值。

| 檔案                    | 說明                                 |
| ----------------------- | ------------------------------------ |
| `worker.js`             | Worker 邏輯：圖片走 R2，其他走 Pages |
| `wrangler.toml.example` | Worker 設定範本（名稱、R2 binding）  |

**更新 Worker：**

```powershell
cd "<你的專案根目錄>\cloudflare-worker"
npx wrangler deploy
```

---

### Analytics 同步（人気記事排名）

靜態化後 Cocoon 停止追蹤瀏覽數，這組元件從 Cloudflare Analytics 補回數據，讓人気記事 widget 反映真實流量。分成兩個部分：

**1. `analytics-worker/` — 每天自動抓資料（不需手動介入）**

一支獨立部署的 Cloudflare Worker，用 cron 每天固定時間自動抓「昨天」的 CDN 頁面流量，累積寫入 Workers KV（保留最近 90 天）。這是資料的主要來源，平常不需要管它，只有在第一次部署或改動 worker 邏輯時才需要（部署設定請複製 `wrangler.toml.example` 為 `wrangler.toml` 並填入你自己的 Account ID / KV Namespace ID）：

```powershell
cd "<你的專案根目錄>\analytics-worker"
npx wrangler deploy
```

**2. `sync-analytics.py` — 把 KV 資料寫進 WordPress**

**每次 build 時會自動執行**（`quick-publish.py` 和 `build-static.py` 都會呼叫其預設模式），平常不需要手動介入：

```powershell
cd "<你的專案根目錄>"

# 預設：從 KV 讀取尚未套用的資料，寫入 wp_cocoon_accesses（build 時自動呼叫的模式）
python sync-analytics.py

# 補資料：KV 有缺口時（例如 analytics-worker 部署前的空窗期），手動從 Cloudflare
# GraphQL API 補最近 8 天到 KV。這不是全量回溯工具——只能抓最近 8 天，
# 超過 Cloudflare API 保留期的日期會被自動跳過
python sync-analytics.py --backfill
```

> 同步狀態存於 `.analytics-sync.json`（已加入 .gitignore），記錄目前套用到哪一天。

---

### 圖片壓縮與上傳到 R2 — 單獨執行時機

只想單獨重新壓縮/上傳圖片時使用，例如批次重壓所有既有圖片。

**步驟 1：壓縮圖片**

```powershell
cd "<你的專案根目錄>"

# 只處理新增/修改的圖片（一般使用）
python compress-images.py

# 重新處理所有圖片（第一次或需要全部重壓時）
python compress-images.py --all
```

> JPEG/WebP quality 85，PNG 無失真壓縮。小於 50KB 的檔案自動跳過。
> 增量判斷先比對 size + mtime，兩者都沒變就完全不讀檔；只有變動過的檔案才會算 MD5 確認。
> （MD5 是把檔案內容換算成一組固定長度字串的雜湊演算法，內容只要有任何差異算出來的結果就會不同，可用來精準判斷兩個檔案是否完全一樣，不受檔名或時間戳影響。）

**步驟 2：同步到 R2**

```powershell
cd "<你的專案根目錄>"

# 一般同步（只新增/更新，一般使用）
python sync-images.py

# 只列出差異，不實際上傳（確認要傳什麼時用）
python sync-images.py --dry-run

# 完全同步（本地已刪除的檔案也會從 R2 移除）
python sync-images.py --delete
```

> 比對方式：把 R2 全部物件與本地檔案各自讀成清單，比對 key 與檔案大小，只上傳新增或大小不同的檔案。
> 預設不加 `--delete`，只會新增/更新檔案，不會刪除 R2 上的任何東西。
