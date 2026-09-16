# Chatbot — HiSKIO AI 客服雛形

> **繼承**：全域 `~/.claude/CLAUDE.md` ＋ `Adam_lab/CLAUDE.md` ＋ `Adam_lab/CONVENTIONS.md`。通用規則（白話文、第一性原理、開發紀律、UI／體驗慣例）一律沿用上層，**本檔不重抄**。
> **棧別**：小工具／原型（Python FastAPI + SQLite + 可插拔模型層）
> 本檔只放本專案特有的穩定規則；**不列資料夾現有內容**（看現況）、**不堆歷史進度**（進規格文件）。

## 專案說明
本地可跑的 AI 客服引擎(v8「一顆腦」架構):程式守衛 → 分診腦(直讀全部 FAQ+KB 索引卡,一次決定)→ 寫手。搞不定=轉真人交接(訊號給 HiSupport,不建工單)。
- 架構單一真理:`data/design-one-brain-2026-07-06.md`(含決定單契約/轉真人三層/防捏造防線/驗收)
- 舊規格書:`data/hiskio_cs_prototype_spec_v6.md`(v7 流水線時代,僅供考古)
- 歷史紀錄:`HISTORY.md`

## 技術選型
Python 3.11 + FastAPI + SQLite + **模型無關 LLM 層**（可插拔：直連 Anthropic 原廠、或走 OpenRouter 接各家模型）。
- 呼叫收斂：`core/llm_client.py`（門面）→ `core/model_config.py`（等級解析）→ `core/llm_providers.py`（各家 provider）。
- 「哪個等級用哪個模型」在 `config/models.toml` 設定；金鑰放 `.env`。改設定檔即可換模型，程式碼不動。
- 等級（roles）：
  - **triage**（分診腦）— 決策/挑文/轉真人判斷/好·不用語意備援(現用 DeepSeek V4-Pro 關思考)
  - **fast 已改名 writer**（寫手）— KB 寫回覆、FAQ 潤飾、問候、確認回應(現用 DeepSeek V4-Flash 關思考)
- 節點呼叫 `call_triage()` / `call_writer()`（call_reasoning/call_fast 已移除）。
- `reasoning_enabled=false` 是事故根治,不可拿掉(DeepSeek 自動思考會偷吃回話額度→靜默失敗)。
- 寫手安全守則在 `prompts/cs_response_guard.txt`,**永遠附加、不受後台人設注入覆蓋**。
- 設計文件：`data/design-model-agnostic-llm-2026-07-03.md`(模型層)、`data/design-one-brain-2026-07-06.md`(架構)。

## 驗收工具(打真 API,勿進 CI 常跑)
- `scripts/run_routing_exam.py`:30 題分診考卷+寫作查核(換模型/改 prompt 後必跑,≥96% 且紅線零失誤)
- `scripts/run_replay_sample.py`:真實對話回放抽測(人工抽查)

## 知識庫更新

用 `/kb-review` skill 啟動完整流程（KB / FAQ 原稿審視，互動式更新）。
使用者只需丟資料 + 確認統籌文件，其他全自動。

**遠端知識來源（#7，2026-07-08 起）**：設 `HISUPPORT_KB_URL`（＋`HISUPPORT_KB_KEY`）後，KB 從 HiSupport 說明中心「啟用中」文章合併進來（`core/kb_remote.py`，`hs_` 前綴、落地 `data/kb_remote*`）；未設＝純本地、行為不變。更新全靠事件（開機對齊＋HiSupport 門鈴 `POST /api/kb/refresh`），**禁止加定時輪詢**（Adam 拍板）。
**對話語料（次級知識根，2026-09-15 起）**：同一個 `HISUPPORT_KB_URL`／金鑰、同一顆門鈴 `POST /api/kb/refresh`，但走自己的 sync 與索引（`core/corpus_remote.py`，落地 `data/corpus*`）。語料同步失敗不擋文章同步（文章是權威知識）。**正式機同樣要設持久磁碟**：`CORPUS_INDEX_PATH`／`CORPUS_DIR`／`CORPUS_STATE_PATH` 指向 `/data`，理由同下。
**正式機落地檔要放持久磁碟**（2026-07-11 實抓根治）：Railway 已設 `KB_REMOTE_INDEX_PATH=/data/kb_remote_index.json`、`KB_REMOTE_DIR=/data/kb_remote`、`KB_REMOTE_STATE_PATH=/data/kb_remote_state.json`——否則每次部署容器全新、索引卡全重建（34 篇×LLM 約 1~2 分鐘），期間機器人「零知識」亂轉真人。放 /data 後重部署秒回暖（已實測：部署完成下一秒即答對）。

### 知識來源地圖（2026-09-15 更新：知識根從一個變兩個，見 HiSupport `docs/adr/0003-hibot-two-knowledge-roots.md`）
機器人對外講的每句話，來源只有**三**種，衝突時**以 Adam 最新的話為準**：
1. **說明中心文章（KB，權威知識根）**——Adam 在 HiSupport 後台管理，門鈴自動同步，改後台=機器人跟著變。含「隱藏＋機器人可用」的營運文章（學員看不到、機器人答得出，如觀看期限 文35、客服時效 文36）。
2. **對話語料（次級知識根，2026-09-15 起）**——客服在 HiSupport 收件匣逐段勾選「納入 HiBot 語料」的**已結案**對話逐字紀錄（`core/corpus_remote.py`，`hsc_` 前綴、落地 `data/corpus*`）。它是「某個客服在某個時間點講過的話」，**永遠次於第 1 項**：同一個問題文章答得出來就用文章，衝突一律以文章為準。這條規則寫在 `prompts/brain_system.txt` 的語料區塊裡——v8 一顆腦一次決定，程式端沒有降權的餘地，**改分診腦 prompt 時記得它在那裡**。語料的 `url` 恆為 None，所以天生進不了給訪客看的 `sources`。
3. **後台可調設定**——人設、機器人名字、轉真人固定訊息、歡迎語等，Adam 在 HiSupport 後台直接改。

歷史沿革：`data/faq.json` 原為人工維護的 22 條獨立標準答案（曾與後台設定分岔出「客服時效 20/24 小時」事故）。**2026-07-11 Adam 拍板補完 7/7 原案「收斂成文章一種」**：22 條逐一核對（20 條文章本就涵蓋、2 條缺口建成隱藏文章）後清空。FAQ 機制程式（faq_matcher／faq_responder／brain 空表容錯）保留備用不拆；測試用 fixture（`FAQ_PATH`）。同一資訊若同時存在文章與後台設定（如客服時效），改任一邊都要檢查另一邊有沒有跟上。

### 重要約束
- KB 兩層分離：`data/kb_source/`（原稿）與 `data/kb/`（系統檔）
- FAQ 兩層分離：`data/faq_source/`（原稿）與 `data/faq.json`（系統檔）
- 追溯表：`data/kb_mapping.md` / `data/faq_mapping.md`（由 skill 自動維護）
- KB / FAQ 變更後必重啟 server（`kb_indexer.py`/`faq_matcher.py`/`brain.py` 都有 `lru_cache`,分診腦的系統指令含整份索引卡）

## 開發習慣

- **改動分批做**：KB 跟 FAQ 不要同批改，出問題才分得清
- **Prompt 模板放 `prompts/`，不寫死在 code 裡**
- **LLM 呼叫一律 try/except + log + fallback**

## 進行中事項
（用一兩行寫目前在做什麼，做完清掉）
