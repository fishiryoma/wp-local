# tesstaiwan.com 靜態網站建置說明

WordPress 內容透過 Python 腳本轉成靜態檔案，部署在 Cloudflare 上。

## 架構

| 服務                                 | 用途                                 |
| ------------------------------------ | ------------------------------------ |
| Cloudflare Pages                     | 靜態 HTML / CSS / JS                 |
| Cloudflare R2 (`tesstaiwan-uploads`) | 圖片和媒體檔案                       |
| Cloudflare Worker (`tesstaiwan`)     | 路由：圖片請求 → R2，其他 → Pages    |
| Local WP (`tesstaiwan-local.local`)  | 本地 WordPress，用於撰寫內容和 build |

---

## 發文與更新流程

### 情境 1：新增文章 / 編輯既有文章（有動到圖片）

1. 開啟 Local WP，在 WordPress 後台寫文章並發布
2. 手動刷新網址清單(編輯既有文章可跳過此步驟)：瀏覽器開啟

    ```
    http://tesstaiwan-local.local/get-all-urls.php
    ```

    等待顯示 `"status": "done"`後代表完成。

3. 執行一鍵發布腳本：

    ```powershell
    cd "C:\Users\User\Local Sites\tesstaiwan-local"
    python new-post.py http://tesstaiwan-local.local/你的文章網址/
    ```

    [new-post.py](new-post.py) 會依序完成：
    1. **壓縮新圖片**（呼叫 `compress-images.py`）——只處理新增/修改的圖片，JPEG/WebP quality 85、PNG 無失真壓縮，小於 50KB 的檔案自動跳過
    2. **同步圖片到 Cloudflare R2**（`aws s3 sync`）——用 `--size-only` 比對大小而非 checksum，只上傳有變動的檔案，不會刪除 R2 上既有的檔案
    3. **Build 受影響的 HTML 頁面**（呼叫 `quick-publish.py`，見下方說明）
    4. **部署到 Cloudflare Pages**（`npx wrangler pages deploy`）

### 情境 2：編輯既有文章（沒有動到圖片）

只重新抓取：**文章本身 + 首頁（第 1、2 頁）+ 該文章所屬的分類/標籤彙整頁**，數秒完成。建置前會先自動同步一次 Cloudflare Analytics 數據（見「Analytics 同步」段落）

1. 開啟 Local WP，在 WordPress 後台編輯文章並更新
2. 執行：

    ```powershell
    cd "C:\Users\User\Local Sites\tesstaiwan-local"
    python quick-publish.py http://tesstaiwan-local.local/你的文章網址/
    ```

3. 部署：

    ```powershell
    npx wrangler pages deploy deploy --project-name tesstaiwan --branch production
    ```

---

### 情境 3：只改 WordPress 設定 / 只想更新文章排名紀錄

**何時用：** 改的是全站性設定（網站標題、選單、小工具、footer 等），這類改動會出現在**每一頁**，`quick-publish.py` 只重建單篇文章相關頁面不夠涵蓋。

1. 開啟 Local WP，在 WordPress 後台修改設定
2. 執行：

    ```powershell
    cd "C:\Users\User\Local Sites\tesstaiwan-local"
    python build-static.py --html-only
    ```

    重新抓取 `all-urls.json` 內所有頁面的 HTML，但保留現有的主題/外掛/wp-includes 檔案不重新複製（比完整重建快約 3 倍）。同樣會在開始前自動同步一次 Cloudflare Analytics。

3. 部署：

    ```powershell
    npx wrangler pages deploy deploy --project-name tesstaiwan --branch production
    ```

### 情境 4：完整重建（換主題、外掛或第一次建置）

**何時用：** 主題、外掛或其他靜態資源檔案本身有變動，或第一次建置整個網站。

1. 開啟 Local WP
2. 瀏覽器開啟 `http://tesstaiwan-local.local/get-all-urls.php`，等待顯示 `"status": "done"`（查詢 WordPress 資料庫，輸出所有公開頁面的 URL 到 `wp-content/uploads/all-urls.json`）
3. 執行：

    ```powershell
    cd "C:\Users\User\Local Sites\tesstaiwan-local"
    python build-static.py
    ```

    重新抓取全部頁面的 HTML，並重新複製 themes/plugins/wp-includes。

4. 部署：

    ```powershell
    npx wrangler pages deploy deploy --project-name tesstaiwan --branch production
    ```

---

## 其他腳本

### `cloudflare-worker/` — Cloudflare Worker 程式碼

不屬於發文流程，只有改路由邏輯本身時才需要處理。

| 檔案            | 說明                                 |
| --------------- | ------------------------------------ |
| `worker.js`     | Worker 邏輯：圖片走 R2，其他走 Pages |
| `wrangler.toml` | Worker 設定（名稱、R2 binding）      |

**更新 Worker：**

```powershell
cd "C:\Users\User\Local Sites\tesstaiwan-local\cloudflare-worker"
npx wrangler deploy
```

---

### Analytics 同步（人気記事排名）

靜態化後 Cocoon 停止追蹤瀏覽數，這組元件從 Cloudflare Analytics 補回數據，讓人気記事 widget 反映真實流量。分成兩個部分：

**1. `analytics-worker/` — 每天自動抓資料（不需手動介入）**

一支獨立部署的 Cloudflare Worker，用 cron 每天台灣時間凌晨 2 點自動抓「昨天」的 CDN 頁面流量，累積寫入 Workers KV（保留最近 90 天）。這是資料的主要來源，平常不需要管它，只有在第一次部署或改動 worker 邏輯時才需要：

```powershell
cd "C:\Users\User\Local Sites\tesstaiwan-local\analytics-worker"
npx wrangler deploy
```

**2. `sync-analytics.py` — 把 KV 資料寫進 WordPress**

**每次 build 時會自動執行**（`quick-publish.py` 和 `build-static.py` 都會呼叫其預設模式），平常不需要手動介入：

```powershell
cd "C:\Users\User\Local Sites\tesstaiwan-local"

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
cd "C:\Users\User\Local Sites\tesstaiwan-local"

# 只處理新增/修改的圖片（一般使用）
python compress-images.py

# 重新處理所有圖片（第一次或需要全部重壓時）
python compress-images.py --all
```

> JPEG/WebP quality 85，PNG 無失真壓縮。小於 50KB 的檔案自動跳過。

**步驟 2：同步到 R2**

```powershell
cd "C:\Users\User\Local Sites\tesstaiwan-local"

# 一般同步（只新增/更新，一般使用）
python sync-images.py

# 完全同步（本地已刪除的檔案也會從 R2 移除）
python sync-images.py --delete
```

> `--size-only`：用檔案大小比對（而非 MD5 checksum）。R2 的 ETag 計算與 S3 不同，不加此旗標會導致每次都重新上傳所有檔案。
> 預設不加 `--delete`，只會新增/更新檔案，不會刪除 R2 上的任何東西。
> 若未來 R2 容量接近 10GB 上限，可加上 `--delete` 讓 R2 與本地完全同步。
