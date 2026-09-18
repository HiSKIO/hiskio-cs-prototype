# KB Review — 知識庫審視與更新

當使用者說「更新知識庫」、「整理 KB」、「kb review」、「kb-review」或類似語意時，執行以下流程。

## 設計原則

- **兩類更新一次處理完**：原稿改動、FAQ 更新
- **使用者只做確認，不碰系統檔**：所有原稿分類、檔名、mapping、備份、索引、重啟都由 Claude 自動處理
- **單一文件確認**：所有變更整理進一份統籌文件，使用者一次回覆即可
- **任何取代必備份**：被覆蓋的舊檔自動搬進 `_archive/`，可隨時還原

---

## Step 0：輸入處理

無論使用者怎麼丟資料，先正規化成「待處理檔案清單」。

### 0-1 偵測輸入型態

| 使用者丟的東西 | 處理方式 |
|---|---|
| 資料夾路徑 | 掃裡面所有 `.md` / `.txt` / `.docx` / `.pdf` |
| `.zip` 檔 | 解壓到 `data/_inbox/` → 掃內容 |
| 單個 / 多個檔案 | 直接讀 |
| 網址 | 用 WebFetch 抓內容 → Haiku 萃取主要內容（去 nav / 廣告 / footer） → 轉 markdown |
| 「我今天更新了 X」這類口語 | 列出 `data/kb_source/` + `data/faq_source/` 最近 24 小時內 mtime 變動的檔案，跟使用者確認 |

暫存區：`data/_inbox/`，處理完自動清空。

### 0-2 自動分類為 KB / FAQ

判斷依據（信心由高到低）：

1. **檔名提示**：`faq_*.md` / `kb_*.md` → 直接歸類
2. **內容結構**：
   - 有「Q:」「A:」「常見問題」一問一答結構 → FAQ 候選
   - 完整段落 + 解決步驟 + 多場景排查 → KB 候選
3. **長度與顆粒度**：
   - 短（< 500 字）+ 一問一答 → FAQ
   - 長（> 500 字）+ 多步驟 → KB
4. **以上都不確定** → 用 Haiku 判斷

分類信心 < 0.7 的檔案 → 列在統籌文件「待你確認分類」區。

### 0-3 處理新舊版本混在一起的情況

**情境 A：使用者只丟新檔，但 source 已有同主題舊檔**
- 用 Haiku 比對標題 + 摘要相似度
- 找到疑似對應的舊檔 → 在統籌文件標註「將取代 `kb_source/<舊檔>`，舊檔備份至 `_archive/`」

**情境 B：新舊都丟進來**
- 看 `mtime`，最近 24 小時內視為「本次提交」
- 不確定的全列在統籌文件「待你確認」區

---

## Step 1：掃描所有來源

並行掃三個來源：

1. **Step 0 帶進來的新檔案**（最高優先）
2. **`data/kb_source/` vs `data/kb/` 比對**
   - 配合 `data/kb_mapping.md`
   - 找出：原稿改動 → 系統檔需更新；原稿新增 → 系統檔需新增；原稿刪除 → 系統檔需刪除
3. **`data/faq_source/` vs `data/faq.json` 比對**
   - 配合 `data/faq_mapping.md`
   - 同上邏輯
> **「最近問答歸納」這一步已於 2026-09-15 移除。** 原本它從 `data/prototype.db` 的
> `sessions.chat_history` 撈最近 30 天對話、歸納成 FAQ 候選。移除的兩個理由：
> ① 它讀的是 HiBot 自存的對話，違反「對話一律歸 HiSupport、HiBot 不自持久化」的拍板；
> ② 它產出的是 FAQ 候選，而 FAQ 已於 2026-07-11 退役。
>
> 這件事現在由**對話語料**接手，而且方向是相反的：由客服在 HiSupport 收件匣逐段勾選
> 「納入 HiBot 語料」，HiBot 透過 `/api/hibot/transcripts` 拉回來、自動生索引卡
> （`core/corpus_remote.py`），不需要人在這個 skill 裡審。語料是次級知識，
> 永遠不會蓋過說明中心文章。
>
> `sessions.chat_history` 本身保留——多輪對話的工作狀態靠它，但它不再是知識來源。

---

## Step 2：產出統籌確認文件

產生 `data/_pending_review_YYYY-MM-DD.md`，固定結構：

```markdown
# KB Review 統籌確認文件
產生時間：YYYY-MM-DD HH:MM
輸入來源：<使用者丟的東西>

## A. 自動分類結果

### A-1 已歸為 KB（信心高）
- [ ] <原檔名> → 歸入 `data/kb_source/<目標檔名>`
      預計切成：kb_0NN（主題）、kb_0NN（主題）
      或：預計取代 kb_source/<舊檔>，舊檔備份至 _archive/
      預計更新：kb_0NN

### A-2 已歸為 FAQ（信心高）
- [ ] <原檔名> → 歸入 `data/faq_source/<目標檔名>`
      預計新增 faq_0NN

### A-3 待你確認分類（信心 < 0.7）
- [ ] <檔名> ← 像 KB 又像 FAQ，請選 [KB] / [FAQ] / [跳過]

## B. KB 變更摘要
- 新增：kb_0NN ...
- 更新：kb_0NN ...
- 刪除：kb_0NN ...

## C. FAQ 變更摘要
- 新增：faq_0NN ...
- 更新：faq_0NN ...
- 刪除：faq_0NN ...

## D. 備份的舊檔
- `data/kb_source/<舊檔>.md` → `data/kb_source/_archive/<舊檔>_YYYY-MM-DD.md`

---

## 你的回應方式

在每個 [ ] 打勾、或寫「全部採納」。
要修改的項目直接在下方寫註記，例：
> kb_007 不要分這麼細，跟 kb_006 合併
> faq_009 採納但 core_steps 第 3 點刪掉
```

把這份文件路徑回報給使用者，等使用者回應。

---

## Step 3：依使用者回應一次執行所有變更

對每個確認的項目：

- **新檔分類** → 從 `_inbox/` 移到 `data/kb_source/` 或 `data/faq_source/`
- **KB 變更** → 產生 / 更新 `data/kb/kb_0NN.md`（含 front matter）
- **FAQ 變更** → 產生 / 更新 `data/faq.json` 對應 entry
- **取代舊檔** → 用 `mv`（不複製）把舊檔搬到 `_archive/<原檔名>_YYYY-MM-DD.md`
- **更新 mapping**：
  - `data/kb_mapping.md` 加 / 改 / 刪對應行
  - `data/faq_mapping.md` 加 / 改 / 刪對應行
  - 由問答歸納新增的 FAQ → mapping 標註來源「YYYY-MM 問答歸納」

---

## Step 4：跑索引 + 自動重啟 server

**KB 有任何變更時：**
```powershell
cd C:\Users\User\Desktop\Adam_lab\Chatbot
python scripts/build_kb_index.py
```

**FAQ 有任何變更時：** 不需跑索引（FAQ 沒索引層）。

**只要 KB 或 FAQ 任一有變更就重啟 server**（兩個 matcher 都有 `lru_cache`，不重啟讀不到新資料）：

```powershell
# 找出舊 uvicorn process（port 8765）並終止
Get-NetTCPConnection -LocalPort 8765 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
# 重啟（背景跑）
cd C:\Users\User\Desktop\Adam_lab\Chatbot
Start-Process -WindowStyle Hidden powershell -ArgumentList "-Command", "uvicorn app:app --reload --port 8765"
```

確認 server 起來：
```powershell
Start-Sleep -Seconds 3
Invoke-WebRequest http://localhost:8765/ -UseBasicParsing -TimeoutSec 5 | Select-Object -ExpandProperty StatusCode
```
回傳 200 即成功。

---

## Step 5：寫 HISTORY.md + 簡潔回報

把這次更新摘要追加進 `Chatbot/HISTORY.md`：

```markdown
## YYYY-MM-DD KB Review
- 輸入來源：<使用者丟的東西>
- KB：新增 X 篇、更新 Y 篇、刪除 Z 篇
- FAQ：新增 X 條、更新 Y 條、刪除 Z 條
- 從問答歸納新增 FAQ：X 條
- 備份檔案：N 個（在 _archive/）
- Server 已重啟，建議測試問句：
  - <從新 question_patterns / key_questions 挑 3-5 個>
```

回報給使用者：
- 處理了什麼（一行）
- 建議測試問句（3-5 個）
- 結束。**不要重述完整流程**

---

## 排錯知識點

- `kb_indexer.py:22` 與 `faq_matcher.py:25` 都有 `@lru_cache(maxsize=1)`，KB / FAQ 改完一定要重啟 server
- FAQ 的 `core_steps` 由 `faq_responder.py` **一字不漏照抄**，AI 歸納的草稿必須使用者人工審過才能寫入
- 索引腳本會用 Haiku 為每篇 KB 重新生 summary，會花 token；只更新部分 KB 時可考慮跳過全量重跑（手動編輯 `kb_index.json` 對應條目即可），但建議全量重跑以保一致
- `_inbox/` 與 `_archive/` 不會被 KB review 流程掃進去
- 若 server 重啟失敗（port 占用、import 錯誤），回報具體錯誤訊息給使用者，不要靜默
