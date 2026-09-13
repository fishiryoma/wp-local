# WordPress 靜態網站遷移專案｜Project Overview

---

## 專案概述

將個人日文旅遊部落格從付費 WordPress 主機遷移為靜態網站，以自訂 Python 腳本取代不穩定的 WordPress 套件，搭配 Cloudflare 全家桶（Pages + R2 + Workers）實現零成本、全球 CDN 交付的部署架構。遷移過程中遭遇多個非預期技術障礙，均透過根本原因分析與對應工程解法逐一排除。

---

## 起點與背景

原架構為付費 WordPress 主機服務，維護成本持續產生。決定將部落格遷移至靜態網站，同時保留 WordPress 作為本地內容編輯環境。

---

## 遷移流程

1. 透過 WordPress UI 套件，將線上部落格完整匯出至本機
2. 在本機架設 Local WP，驗證功能與內容完整性
3. 評估後決定捨棄動態 DB 查詢架構，改以靜態網頁生成取代
4. 重新部署至雲端（Cloudflare Pages）

---

## 技術挑戰與解法

**1. 靜態化套件不可靠**
Simply Static 等套件在免費方案下有 2 GB 檔案限制，且 crawler 執行中途卡住時無任何明確錯誤訊息，難以診斷。
→ **解法：** 自訂 Python 爬蟲腳本，完整掌控 URL 收集邏輯、平行抓取與重試機制。

**2. 動態功能比預期多，需逐一處理**

- **(a) 全站搜尋功能：** 靜態網站無法執行即時搜尋查詢。
  → 透過主題設定與 CSS 覆蓋，移除前台搜尋按鈕（包含桌面版與手機版各自獨立處理）。

- **(b) 分類 / 標籤頁面未生成：** 初版腳本僅生成文章與首頁，未涵蓋分類、標籤、分頁等 archive 頁面，導致點擊連結後出現 404。
  → 改寫 URL 收集邏輯，動態計算每個 taxonomy term 的分頁數量，完整生成所有頁面 URL。

- **(c) 網頁流量統計失效：** 靜態網站無法即時寫入 MySQL，熱門文章排名資料因此中斷。
  → 設計非同步補償機制：Cloudflare Worker 每日透過排程（serverless cron）從 CF Analytics API 拉取資料並寫入 KV；每次手動 build 時，腳本自動將 KV 資料同步回 WordPress DB。雖然不夠即時，但確保每日流量資料完整保存、熱門文章排名持續更新。

**3. 媒體檔案過大，超出 Pages 部署限制**
圖片與媒體檔案體積龐大，無法直接納入靜態部署包。
→ 媒體檔案改存放於 Cloudflare R2，透過 Worker 將圖片請求分流導向 R2，網頁內容請求則導向 Pages，兩者各司其職。

**4. S3 與 R2 的行為差異導致圖片重複上傳**
使用 AWS CLI（`aws s3 sync`）同步媒體至 R2 時，發現每次執行均重複上傳所有檔案，無法正確比對已存在的物件。根本原因在於 R2 的 ETag 計算方式與 S3 不完全相容，導致 CLI 判斷遠端物件與本地不一致，觸發重傳。
→ 改以物件是否存在（`head-object`）作為比對基準，或補充 `--exact-timestamps` 旗標，避免不必要的重複上傳，降低 R2 寫入操作次數與時間成本。

**5. Windows 終端機編碼問題，腳本無法正確輸出日文**
Python 腳本在 Windows cmd / PowerShell 環境下執行時，因預設編碼為 cp950（Big5），日文字元（文章標題、路徑）輸出時拋出 `UnicodeEncodeError`，導致腳本中斷。
→ 在腳本進入點加入 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`，強制將標準輸出改為 UTF-8，確保跨平台執行一致性。

---

## 雲端技術架構

| 服務 | 用途 |
|------|------|
| **Cloudflare Pages** | 靜態網頁托管，全球 CDN 交付 |
| **Cloudflare R2** | 媒體圖片存放、MySQL 資料庫定期備份 |
| **Cloudflare Workers** | 圖片 / 網頁請求分流；排程每日抓取網頁流量資訊 |
| **Workers KV** | 暫存每日流量資料，供 build 時同步回 DB |

---

## 成果

- **效能提升：** 靜態 CDN 交付，消除 PHP 執行時間，全球 POP 節點加速。
- **零成本：** 完全使用 Cloudflare 免費方案，每月主機費用降為 $0。
- **自動化：** 一行指令依序完成 DB 備份確認 → Analytics 同步 → 靜態頁面生成 → 部署至 Cloudflare。
- **可移植性：** 任何人從 Git repo + 單一 `.env` 設定檔即可完整重建整個系統。
- **資安控管：** 機敏資訊（API Token、DB 密碼）集中於 `.env`，由 `.gitignore` 排除，不進入版本控制。
