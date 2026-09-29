# Chatbot HISTORY

每次重要更新（KB Review、規格升級、架構調整）追加在最上方。

## 2026-09-29 — /api/billing 加每期重置欄位（HIS-909，給 HiSupport「本月剩餘」面板）
- 查詢網址改 OpenRouter `/api/v1/key`（官方文件現在只列這個）；既有欄位不動，新增原封轉傳 `limit_reset`／`limit_remaining_usd`／`usage_daily_usd`／`usage_weekly_usd`／`usage_monthly_usd` 與 `fetched_at`（查詢當下 UTC，快取命中回當時值）。HiBot 不換算，本期用量／剩餘／重置時間全由 HiSupport 算。
- `limit_reset` OpenRouter 有回才帶（null＝永不重置照帶）；沒回不帶，免得 HiSupport 誤判成「永不重置」。OpenRouter 回 200 但沒 `usage` → 502，不再冒充「累計 $0」。
- 規格：HiSupport `docs/2026-09-29-hibot-billing-monthly-design.md` §3。驗證：pytest 新增 14 顆（假 OpenRouter 回應），`pytest tests` 168 綠（**要指定 `tests`**：不指定會把 `scripts/run_multiintent_test.py` 收進來、它改寫 stdout 讓 pytest 崩）；本機端到端（真 HiBot 只換掉 OpenRouter 連線 → 本機 HiSupport PR #32 面板）顯示「本月剩餘 $1.80」，OpenRouter 沒回 `limit_reset` 時退回「累計」。**真實回應格式待 Adam 合併前用正式金鑰確認。**
- 順帶查明（這次未改）：模型呼叫失敗（含額度用完 402）時 `/api/chat` 回 200＋「聽不懂」、第 3 則才問轉真人。修法提案見 HiSupport `DECISIONS_PENDING.md` D13。

## 2026-07-17 — 圖片三件套：讀圖員（vision role）＋sources_all（測試間）
- **讀圖員**（契約 2026-07-17b）：`/api/chat` 新收 `image_urls[]`；`nodes/vision.py` 自抓圖（3 張/輪、6MB、8 秒逾時）轉 base64 → 新 role `vision`（google/gemini-2.5-flash-lite，OpenRouter 同一把金鑰）出文字描述 → 併進 user_message 交原分診腦——**決策與寫手完全不動，讀圖員只當翻譯**。讀圖失敗＝附註「無法讀取」照常走文字流程（本機實撞驗證：URL 壞掉時機器人正常轉真人並提及截圖）。訊息可空（有圖即可）。
- **sources_all**：回應新增本輪實際用到的全部文章（id/title/hidden，含隱藏篇無網址），給 HiSupport 後台測試間顯示引用；訪客端由 HiSupport 中介不外流。
- 模型層：providers 加 `images` 參數（openai_compat 組 content 陣列；anthropic 原廠記警告忽略）；`call_vision()` 門面。
- 驗證：pytest 131 綠（新增 5 顆：描述併入/空訊息佔位/失敗退化/閉環不花讀圖錢/sources_all 隱藏旗標）；本機真瀏覽器 E2E——canvas 畫「付款失敗 E102」貼上送出，分診腦判斷理由出現「KB 無 E102 代碼資料」＝讀圖員真的看懂了圖。brain/寫手 prompt 零改動（考卷不受影響，未重跑）。

## 2026-07-17 — 金鑰用量接口 /api/billing（花費面板）
- 給 HiSupport 後台「連線狀態」面板顯示花費:查 OpenRouter /api/v1/auth/key=**這支金鑰**的 limit/usage/remaining(非整帳戶——帳戶下還有其他專案金鑰,各算各的,Adam 拍板);5 分鐘記憶體快取,面板開頁不會變成對 OpenRouter 的輪詢;查詢失敗回 502、面板顯示「——」不擋頁。

## 2026-07-16 — 交接摘要重整＋提議句消毒＋期待管理訊息水管勘誤（Adam 口述批次）
- **期待管理訊息從未生效的兩截水管（實抓）**：①HiSupport `pushConfig` 送頂層 `handoff_message`，但本端 `/api/config` 白名單只認巢狀 `messages.handoff_message` → 自 7/4 一直被靜默忽略，靠「兩邊內建預設同字」遮掩，Adam 自訂文字後才露餡（HiSupport 端已改巢狀）。②正式機 `RUNTIME_CONFIG_PATH` 被 Git Bash 路徑轉換寫成 `C:/Program Files/Git/data/...`＝容器暫存區、部署即清空 → 已改 `/data/runtime_config.json`（持久磁碟，與 7/11 kb_remote 同病同醫）。
- **交接摘要重整**（`build_handoff_summary`，目的＝真人 10 秒抓重點、不寫廢話）：「客戶想解決」改「待處理問題」＝intent_log 未結案項清單（舊版拿 issue.summary＝最後一輪字面，轉真人當下永遠是「用戶要求轉真人」＝零資訊）；「問題類別」拿掉；情緒只在非中性才列；handoff_reason 細分 `user_request`（用戶點名要真人）並改標籤（needs_human＝「需要查詢用戶個人帳務／資料」、no_kb_match＝「可考慮補說明文章」）。
- **提議句消毒**：brain 偶爾把 reason_to_user 寫成「好的，我幫你轉接…」再接系統確認句「要幫您轉真人嗎？」＝自相矛盾（正式機實撞）。程式面兜底（`_HANDOFF_ANNOUNCE_RE` 中招換中性理由）＋prompt 禁令加例。
- **確認轉真人＝本來就是全路徑統一**：所有轉真人（提議／強制／超額）都經「要幫您轉真人嗎？」關卡，無直轉路徑——Adam 看到的「直接轉」是 HiSupport「交還 HiBot」未重開 session 的殘留閉環（HiSupport 端已修：交還時清 `bot_session_id`）。
- 驗證：pytest 124 綠；30 題分診考卷 **30/30＝100%、紅線零失誤、寫作查核乾淨**。

## 2026-07-11 — FAQ 層退役：補完 7/7「收斂成文章一種」原案（單一知識根）
- 動機:Adam 核對出 7/7 已拍板「FAQ/KB 不分開管、收斂成說明中心文章一種、一次性搬家」,但 #7 實作只做了 KB 半套;FAQ 22 條留在程式裡成第二個根,釀出「客服時效 20/24」分岔事故。Adam 指令「把它做完」。
- 搬家核對:22 條逐一與說明中心 33 篇比對(關鍵事實逐詞檢查),**20 條本就被文章涵蓋**;唯二缺口(觀看期限、客服服務時間與回覆時效)建成 HiSupport「隱藏＋機器人可用」文章(文35/文36,學員看不到、不動 Adam 整理的版面)。
- 退役方式:`data/faq.json` → `[]`(資料退役);機制程式保留(brain `_faq_table()` 空表顯示「清單為空」、幻覺 faq_id 剔除降級 KB,原有容錯,零程式改動);測試本就走 `FAQ_PATH` fixture。
- 驗證:pytest 124 綠;30 題分診考卷 **29/30=97%、紅線零失誤、寫作查核乾淨**(原 19 題 FAQ 相關題全數正確改走 KB 文章;唯一錯題=已知「vibe coding」離題判定抖動,與本次無關);正式機部署後 E2E 實測兩篇隱藏文章。
- 對照文件:faq_mapping.md/kb_backfill_todo.md 已加退役/收案註記;CLAUDE.md 知識來源地圖改「單一根」版。

## 2026-07-10 — 客服時效統一 24 小時＋「知識來源地圖」定案
- 動機:客服時效在兩處各落一次數字(FAQ=20 小時,Adam 7/9 口述;HiSupport 轉真人固定訊息=24 小時,Adam 7/10 自填),學員問機器人與被轉真人看到的不一致。Adam 追問「FAQ 不是根據文章產的嗎、為什麼會有兩份」,暴露來源邏輯沒有文件化。
- 作法:①`data/faq.json` 三處 20→24(faq_001 備註/faq_021 兩句),124 測試綠、已推 main。②CLAUDE.md「知識庫更新」段新增**知識來源地圖**(Adam 定案:FAQ 是獨立人工維護資料、文章只是來源之一,另含真實客服對話與 Adam 口述營運規則;衝突以 Adam 最新的話為準;口述規則進 FAQ 要明講「後台看不到」;同一資訊跨 FAQ/後台設定要兩邊對齊)。

## 2026-07-09 — 知識單一真理:遠端啟用=本地 KB 全數退場
- 動機:#7 接上 HiSupport 說明中心後,`_load_kb_index()` 是「本地(7/4 凍結拷貝 32 篇)＋遠端(說明中心現行版 32 篇)」合併——同內容兩份並存,說明中心編輯只動遠端,分診腦可能引到過期拷貝(Adam 拍板:以說明中心為準,本機測的不算數)。
- 作法:`kb_indexer._load_kb_index()` 改「遠端啟用(HISUPPORT_KB_URL 有設)→只回遠端 hs_*;停用→純本地」。斷線韌性(沿用最後快取/防誤清)本來就在 kb_remote 內部,不重複兜底。FAQ 22 條自包含(question_patterns/core_steps/fallback_message),不受影響。
- 測試:新增 2 顆釘死(遠端啟用=本地退場/停用=純本地);test_brain 的 kb_env fixture 補「明確關遠端」(開發機 .env 有 HISUPPORT_KB_URL 會讓本地白名單隱形);全套 124 綠。
- 注意:`data/kb/`+`data/kb_index.json` 檔案保留(本機純本地開發還在用);雲端(遠端啟用)等於它們退役。

## 2026-07-06 — v8「一顆腦」架構收斂(P0~P4 完整落地)
- 動機:①7/4 換 DeepSeek V4 後「查不到 KB/時好時壞/20~40 秒」——根因=V4 全系列自動思考,
  思考 token 吃掉小額度呼叫(faq_matcher/kb_indexer max_tokens=100)→ 空答靜默失敗;
  ②結構性病:每句 5~7 次串行 LLM(骰子相乘),主管只看站台紙條不看原件。
  實測定案(26 題考卷 2×2+寫手盲測,Adam 親評):一顆腦×關思考完勝。
- 作法(單一真理=`data/design-one-brain-2026-07-06.md`):
  - **P0 接線**:provider 支援 `reasoning_enabled=false`(OpenRouter reasoning.enabled;
    關了卻回思考 token 會記警告);等級改名 **triage/writer**(call_triage/call_writer);
    triage=DeepSeek V4-Pro 關思考(考卷 26/26)、writer=V4-Flash 關思考(盲測冠軍)。
  - **P1 分診腦**:新 `nodes/brain.py` 直讀全部 FAQ 問法表+KB 索引卡,一次輸出決定單
    (含 user_satisfied「好吧」誤結案閘門、issue 同源輸出、幻覺編號白名單剔除→空手降級轉真人);
    裁六站+話術站(entry_classifier/intent_clarity/faq_matcher 比對/kb_indexer 挑文/evaluator/
    intent_selector/clarification/no_kb/off_topic/pipeline);state 殭屍欄位大掃除;
    新增**每日訊息配額**(預設 30 句/日,超額=固定話術+提議轉真人、零 LLM,防洗版燒錢)。
  - **P2 寫手**:防捏造鐵則+「先給解法再追問」+禁粗體;全節點掛新等級;拆過渡別名。
  - **P3 驗收**:30 題考卷(含多輪/注入/好吧)29/30=97%、紅線零失誤;真實回放 10 筆全合理;
    live E2E(HiSupport→HiBot)整輪 2.4~10.8s(原 20~40s)。
    **live 抓到真漏洞並根治**:後台人設注入=整份蓋掉 system prompt → 改為
    「人設(可覆寫)+`prompts/cs_response_guard.txt` 守則(永遠附加、不可蓋)」。
- 影響:core/{orchestrator,state,llm_client,llm_providers,model_config,runtime_config}、
  nodes/*、prompts/*、config/models.toml;測試 66→93 全綠;
  驗收工具 scripts/run_routing_exam.py、run_replay_sample.py 入庫。
- 對外契約(/api/chat handoff 訊號、/api/config 注入鍵)一字未動,HiSupport 端零改動。
- 規格偏差備註:決定單 action 集拿掉 list_pending_intents(多意圖由腦直接處理,
  「等待用戶選擇意圖」phase 作廢);continue_intent 帶有效編號=等效回答。

## 2026-07-04 — 工單流程 → 轉真人交接（配合 HiSupport 整合）
- 動機：HiSupport 已定案「工單系統永久不做」，HiBot 搞不定 → 轉真人交接。HiBot 只負責「判斷＋摘要＋訊號＋閉環」，其餘（訊息、Email、通知真人）由 HiSupport 處理。並要求「單機 HiBot 體驗＝正式 HiSupport 體驗、只差介面」。
- 作法（甲案：HiBot 講安撫話、HiSupport 推字）：
  - `/api/chat` 回應新增 `handoff{requested,reason,summary}` 訊號（`core/state.py:build_handoff`）。
  - `/api/config` 注入白名單新增 `handoff_message`（`core/runtime_config.py`）；轉真人時用注入字、沒有就用內建預設（與 HiSupport 預設一致）。
  - 轉真人分支瘦身：**移除** 收 Email／格式重試／工單編號／`phase=已結束` 死路／前端「建立工單」按鈕／`/api/ticket/create` 端點；`_execute_handoff` 只講一句安撫話＋設 `handed_off`＋退場。
  - 新增 `handed_off` 旗標＋閉環（`_handed_off_holding`）：已交接後不再重問、不打 LLM。
  - phase 正名 `等待工單確認`→`等待轉真人確認`；`ticket_state` 移除舊工單欄位（封存見交接約定 §7）；`core/ticket.py` 不再被引用（保留存查）。
- 影響：`core/{orchestrator,state,runtime_config}.py`、`app.py`、`nodes/ticket_handler.py`、`static/index.html`；新增 `tests/test_handoff_flow.py`（7 測試）；全 60 測試過。
- 單一真理文件：`../HiSupport/docs/2026-07-04-hibot-handoff-contract.md`（兩邊 to-do、訊號規格、摘要格式、封存記錄）。
- 待辦：`static/guide.html` 產品說明仍有舊工單字眼待更新；Railway 端 `OPENROUTER_API_KEY`／`handoff_message` 尚未設。

## 2026-07-04 — LLM 層模型無關化（Phase 1）
- 動機：不綁死單一模型／供應商；未來可接 OpenRouter／任何模型，並可自訂「哪個等級用哪個模型」。
- 作法：模型呼叫收斂點改為可插拔 provider 層（AnthropicNative／OpenAICompat）＋ `config/models.toml` 設定 ＋ 等級化命名（`call_reasoning`／`call_fast`，移除 `call_sonnet`／`call_haiku`）。**預設設定＝原行為**（reasoning→Sonnet、fast→Haiku、走 Anthropic 原廠）。
- 影響：新增 `core/llm_providers.py`、`core/model_config.py`、`config/models.toml`、`tests/`（21 測試全過）；15 個呼叫點命名遷移；`manager` `MANAGER_MODEL`→`MANAGER_ROLE`；`requirements.txt` 加 `openai`；`.env` 加 `OPENROUTER_API_KEY`。
- 用量統計改為多模型：成本優先取供應商回傳費用、次查價目表、都沒有標「金額待補」。
- 設計文件：`data/design-model-agnostic-llm-2026-07-03.md`。
- 註：專案更名 Chatbot→HiBot 因資料夾被佔用暫緩，未做整份字串替換。
