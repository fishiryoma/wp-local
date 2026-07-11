# 日文校對工具（style-editor）

讀取 Local WP 資料庫裡的文章，用 AI（Gemini）依照你的寫作風格校正日文、檢查/建議標題，網頁上逐條標記修改處，
全部審核完後才會覆蓋回 WordPress 資料庫的原文章。

## 這個工具做什麼 / 不做什麼

- 只挑「真正的錯誤」修正：錯字、活用錯誤（例如「楽しいでした」→「楽しかったです」）、看不懂意思的助詞問題等。
- **不會**把你的日文改成教科書式的標準日文。台灣人寫日文特有的語感、口語、顏文字、「テス」簽名段落等都會保留。
  這份判斷邏輯寫在 [`style_profile.md`](./style_profile.md)，是根據你現有文章分析出來的，**可以直接編輯這個檔案**來調整 AI 的判斷標準（也包含標題風格）。
- 讀完全文後會檢查標題文法是否有問題；如果標題本身沒問題、也想不到更好的寫法，就不會硬湊 3 個建議。
  只要文法有問題，或有更符合你風格的寫法，才會給你剛好 3 個標題建議，你可以選要用哪一個（或維持原標題）。
- 覆蓋原文前一定會自動備份（標題+內文）到 `style-editor/backups/`，可以隨時找回原始內容。
- 覆蓋前會檢查文章的內容或標題是否在 WordPress 後台被改過，如果有改過會提醒你重新校對，不會誤蓋掉你手動的修改。

## 安裝

前提：Local WP 要開著（MySQL 才會啟動）。

```powershell
cd "C:\Users\User\Local Sites\tesstaiwan-local\style-editor"
pip install -r requirements.txt --break-system-packages
```

如果你的環境不需要 `--break-system-packages`，拿掉即可。

## 設定 API Key（Gemini，免費）

到 https://aistudio.google.com/apikey 用 Google 帳號登入，建立一組免費的 Gemini API Key。

打開專案根目錄的 `.env`（`C:\Users\User\Local Sites\tesstaiwan-local\.env`），把這行改成你的 Key：

```
GEMINI_API_KEY=AIzaSy........
```

**目前使用的模型**：`gemini-3.5-flash`（2026 年 7 月查證，是目前免費帳號可用、品質最好的模型）。
如果你之後發現免費額度用完跳錯誤，可以把 `.env` 裡的 `GEMINI_MODEL` 改成 `gemini-3.1-flash-lite`——品質稍低一點，但免費額度（每日請求數）通常高很多。
免費帳號的實際請求數/分鐘限制可能會隨時間調整，可以到 https://aistudio.google.com/rate-limit 查看目前你帳號的即時額度。

DB 連線資訊會直接沿用同一個 `.env`（跟 `sync-analytics.py` 共用），不用重新設定。

## 執行

```powershell
cd "C:\Users\User\Local Sites\tesstaiwan-local\style-editor"
python app.py
```

瀏覽器打開 http://localhost:5055

## 使用流程

1. 左側清單選一篇文章。
2. 按「開始 / 重新 AI 校對」，AI 會同時做兩件事（文章越長越久）：
    - 校正內文文字
    - 讀完全文後檢查標題，視情況給出標題建議
3. **標題區**：文字下方會顯示標題文法檢查結果。如果有建議，會列出「維持原標題」+ 最多 3 個新標題，用單選鈕選你想用的那個（預設維持原標題）。
4. **內文區**：綠色底線是建議的修改，紅色刪除線是原文。滑鼠移到修改處會出現三個小按鈕：
    - ✓ 採用這個修改
    - ✕ 維持原文，不採用
    - ✎ 不滿意，輸入意見要求 AI 重新調整這一段（例如「這太生硬了」「這不是錯字」）
    - 每個修改預設是「採用」狀態，你只需要處理你不同意的地方即可。
5. 都確認好之後，按右上角「套用並覆蓋原文」。文章內容（以及你選的標題，如果不是原標題）會被覆蓋，原始版本自動備份在 `style-editor/backups/`。
6. 你可以隨時關掉瀏覽器，之後重新打開網頁、選同一篇文章，進度會留著（存在 `style-editor/sessions/`，未套用前不會影響 WordPress）。

## 找回備份

`style-editor/backups/{文章ID}-{網址代稱}-{時間戳記}.json` 是覆蓋前的原始 `{title, content}`。
如果想復原，打開 Local WP 的 phpMyAdmin / Adminer，把該文章 `wp_posts.post_title` / `post_content` 欄位貼回備份檔對應的內容即可。

## 已知限制

- 「✎ 重新調整」會把整個段落（同一個文字節點）重新校正一次，不是只改單一個字詞，如果段落內有多處修改，會一起重新產生。
- 目前只處理內文文字節點和標題，不會校對 SEO meta、分類名稱等其他欄位。
- 沒有實作使用者登入，僅供你自己在本機使用，不要對外開放這個網頁（例如不要用 ngrok 之類的工具公開）。
- Gemini 免費額度有限（依模型而異，請求數/分鐘、請求數/天都有上限），長文章或短時間內連續校對多篇可能會撞到限制，出現錯誤訊息時稍等一下再試即可。
