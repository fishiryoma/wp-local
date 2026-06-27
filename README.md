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

## 腳本說明

### `build-static.py` — 完整重建 / HTML 重建

**何時使用：**

- 完整重建：更換主題、外掛、第一次建置
- `--html-only`：只改了 WordPress 設定，不需要重新複製主題/外掛檔案（速度快約 3 倍）

```powershell
# 完整重建（在專案根目錄）
cd "C:\Users\User\Local Sites\tesstaiwan-local"
python build-static.py

# 只重建 HTML（設定有改但主題/外掛沒動）
python build-static.py --html-only
```

完成後部署（切換到 deploy 資料夾）：

```powershell
cd "C:\Users\User\Local Sites\tesstaiwan-local"
npx wrangler pages deploy deploy --project-name tesstaiwan
```

---

### `quick-publish.py` — 快速發布新文章

只重建受影響的頁面（新文章 + 首頁 + 分類/標籤頁），數秒完成。

**何時使用：** 每次在 WordPress 發布或更新文章時。

**前提：** Local WP 必須正在執行。

```powershell
# 在專案根目錄執行
cd "C:\Users\User\Local Sites\tesstaiwan-local"
python quick-publish.py http://tesstaiwan-local.local/你的文章網址/
```

完成後部署（切換到 deploy 資料夾）：

```powershell
cd "C:\Users\User\Local Sites\tesstaiwan-local"
npx wrangler pages deploy deploy --project-name tesstaiwan
```

---

### `cloudflare-worker/` — Cloudflare Worker 程式碼

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

### `app/public/get-all-urls.php` — 產生 URL 清單

查詢 WordPress 資料庫，輸出所有公開頁面的 URL，存成 `wp-content/uploads/all-urls.json`，供 `build-static.py` 使用。

**何時使用：** 執行 `build-static.py` 之前，或懷疑 URL 清單不完整時。

在瀏覽器打開：

```
http://tesstaiwan-local.local/get-all-urls.php
```

顯示 `"status": "done"` 即完成。

---

## 一般發文流程

1. 開啟 Local WP
2. 在 WordPress 後台寫文章並發布
3. `cd "C:\Users\User\Local Sites\tesstaiwan-local"` → `python quick-publish.py http://tesstaiwan-local.local/文章網址/`
4. `npx wrangler pages deploy deploy --project-name tesstaiwan`

## 只改設定（不換主題/外掛）

1. 開啟 Local WP，在 WordPress 後台修改設定
2. `cd "C:\Users\User\Local Sites\tesstaiwan-local"` → `python build-static.py --html-only`
3. `npx wrangler pages deploy deploy --project-name tesstaiwan`

## 完整重建流程（換主題、外掛或首次建置）

1. 開啟 Local WP
2. 瀏覽器打開 `http://tesstaiwan-local.local/get-all-urls.php`，等待完成
3. `cd "C:\Users\User\Local Sites\tesstaiwan-local"` → `python build-static.py`
4. `npx wrangler pages deploy deploy --project-name tesstaiwan`

---

## 圖片壓縮與上傳到 R2

新增圖片後，先壓縮再同步到 R2：

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
aws s3 sync "C:\Users\User\Local Sites\tesstaiwan-local\app\public\wp-content\uploads" s3://tesstaiwan-uploads --endpoint-url https://<<ACCOUNTID>.r2.cloudflarestorage.com
```

> 預設不加 `--delete`，只會新增/更新檔案，不會刪除 R2 上的任何東西。
> 若未來 R2 容量接近 10GB 上限，可加上 `--delete` 讓 R2 與本地完全同步（本地已刪的檔案會一併從 R2 移除）。
