# 釐清預算：「搞不定就轉真人」改成「先試著搞懂，真的不行才轉」

> 建立：2026-09-14，Ethan 提出問題、本文提方案。
> **2026-09-15 已實作於分支 `feature/clarify-budget`**（見 §8 實作紀錄）。採方案 B ＋「候選閘門」
> 強化版，預算合一沿用 `max_unclear`，HiSupport 端零改動。**分診考卷尚未跑**（要真 API、要花錢）。
> 上位依據：`data/design-one-brain-2026-07-06.md` §4.1 轉真人三層總表。
> 對側契約：HiSupport `docs/2026-07-04-hibot-handoff-contract.md`（本案若拍板，需補一條 Amendment）。
> 一句話：現在「知識庫沒資料」第一輪就舉白旗，本案給它一個**釐清預算**（預設 3），
> 用完才轉真人；預算期間要問**有方向的問題**，不是「請您再描述清楚一點」。

---

## 1. 問題是什麼（先把現況講準）

Ethan 的原話：「只要一遇到找不到就直接轉真人客服，效果太差。」

實際跑的流程比「直接轉」再多一步，但不影響結論：

1. 分診腦判 `suggest_ticket` + `handoff_reason=no_kb_match`
2. → `_execute_suggest_ticket`（`core/orchestrator.py:453`）發「⋯⋯要幫您轉真人嗎？（回覆「好」或「不用」）」
3. → 用戶回「好」→ `_execute_handoff` 閉環退場

所以訪客看到的是**一句提議**，不是無聲轉走。但機器人**在第一輪就放棄了**——這才是體感差的原因。

### 1.1 根因：兩條失敗路徑，只有一條有預算

| 失敗型態 | 走哪個 executor | 有沒有計數器 | 結果 |
|---|---|---|---|
| 聽不懂用戶在講什麼 | `_execute_clarify` / `_execute_uncertainty`<br>（`orchestrator.py:284`／`:304`） | **有**：`consecutive_unclear_count`，門檻 `max_unclear` 預設 3 | 前 2 輪會追問，第 3 輪才提議轉真人 |
| 聽懂了，但 FAQ＋KB 沒東西 | `_execute_suggest_ticket`（`:453`） | **沒有** | 第 1 輪就提議轉真人 |

分診腦的硬規則 3 直接寫死：「問題明確、但 FAQ 和 KB 都沒有相關資料 → suggest_ticket」
（`prompts/brain_system.txt:24`）。沒有中間狀態。

### 1.2 還有一個放大器：suggest_ticket 會**清空**釐清計數

`_execute_suggest_ticket` 第一行就是 `consecutive_unclear_count = 0`（`orchestrator.py:455`）。

意思是：就算前面已經聽不懂 2 次（計數 2），只要這輪腦判成 `no_kb_match`，計數歸零、
同時當場提議轉真人。兩條路徑**永遠無法累積**，`max_unclear` 這顆旋鈕也就被繞過去了。

---

## 2. 要做成什麼樣（目標行為）

> 使用者發問 → 若 3 輪內既沒搞懂真正需求、也沒找到相關文章 → 才轉真人。

翻成規則：

- **沒進展的輪次**（聽不懂／找不到文章）累計，達門檻才提議轉真人。
- 預算沒用完時，機器人要**主動縮小範圍**，而且問題要有方向。
- 任何一輪真的答出東西 → 計數歸零，重新來過。

### 2.1 但有四種情況**絕對不能**拖（本案最重要的邊界）

這四條走原路、第一輪就提議轉，**不吃預算**：

| 情況 | 為什麼不能拖 |
|---|---|
| 用戶點名要真人（`user_request`） | 已經開口要人了還被追問 3 輪，比現況糟十倍 |
| 要查個人資料／訂單／退款進度（`needs_human`） | 機器人**結構上**辦不到，釐清再多輪也變不出資料 |
| 寫手舉手 `[SUGGEST_TICKET]`（`orchestrator.py:393`） | 觸發條件是「已答多次仍不滿／金流個案」，本來就是累積後的結論 |
| 用戶已拒絕過轉真人（`user_decision=declined`） | 現況就不再強逼，維持不變 |

**如果這條邊界沒守住，這個改動會讓體驗變差而不是變好。** 拖延的價值只存在於
「再問一下可能就找得到」的情況；上面四種都不屬於。

### 2.2 建議加碼：情緒踩煞車

分診腦每輪已經免費輸出 `issue.user_emotion`（中性／焦急／不滿／友善，`brain.py` 決定單）。
建議：**偵測到「不滿」時預算立即失效、直接提議轉真人**。

理由：對一個已經在不爽的人多追問兩輪，是把小抱怨養成大客訴。這顆訊號現在只寫進交接摘要、
沒有人拿來做決策，加這條幾乎零成本。

---

## 3. 方案比較

### 方案 A：程式面加一個 no_kb 計數器（最小改動）

在 `_execute_suggest_ticket` 裡，`handoff_reason == "no_kb_match"` 時計數 +1，未達門檻就不提議轉、
改發一句追問。

- 優點：只動 orchestrator，不碰 prompt，不用重跑考卷。
- **致命缺點：那句追問沒人寫得出來。** 決定單這時填的是 `reason_to_user`
  （＝「為什麼建議轉真人」），不是澄清問句。只能退回罐頭
  `DEFAULT_UNCERTAINTY_MSG`＝「抱歉，我不太確定您想問的內容，能否再多描述一下您遇到的狀況？」
- 結果：使用者連吃 2 次罐頭廢話再被轉走。**比現況更煩。不建議。**

### 方案 B：讓分診腦自己管預算（建議）

分診腦是**唯一看得到全部 KB 索引卡**的地方（`brain.py:_kb_cards`，整份烤進 system prompt）。
只有它知道「沒有完全命中，但最接近的是文 12 和文 27」。所以追問要它來寫：

> 「您說的『看不了』，是指**影片播不出來**，還是**課程期限到了進不去**？」

這種問句才有機會在第 2 輪收斂。方案 A 生不出來，方案 B 可以。

- 新增 action `narrow_down`：FAQ＋KB 沒有直接命中、但問題屬業務範圍、且預算還有剩 → 選它，
  並依**最接近的 2–3 張索引卡**寫一句二選一／三選一的追問。
- 預算用完 → 照舊 `suggest_ticket`。
- 成本：動 `brain_system.txt`、`brain.py` 白名單、orchestrator 新 executor、state 新欄位，
  **且必須重跑 30 題分診考卷**（CLAUDE.md：≥96% 且紅線零失誤）。

### 方案 C：放寬「不准硬塞最接近的文章」

**明確不建議，也不要順手做。** 硬規則 4 與 §14-2 防捏造防線是刻意設的——
拿沾邊的文章硬答，比誠實說沒有更傷（用戶照著錯資訊操作）。本案不碰這條線。

---

## 4. 建議實作（方案 B ＋ 程式面兜底）

### 4.1 預算要合一，還是兩個計數器？（**待拍板，最重要的一題**）

Ethan 的敘述是「3 輪內**無法了解需求**，**且**無法找到文章」——是**一個**預算涵蓋兩種失敗。

**建議：合一。** 把 `consecutive_unclear_count` 的語意從「連續聽不懂」擴成
「**連續沒進展**」，`narrow_down` 也計入，`_execute_suggest_ticket` 不再無條件歸零。

合一的好處很實際：

- **沿用現有的 `max_unclear` 旋鈕，HiSupport 端零改動**——不必新增 `/api/config` 白名單鍵。
  這份契約史上已經被「推了被靜默忽略的死旋鈕」咬過兩次
  （`max_turns_per_session`、頂層 `handoff_message` 拖了 12 天才發現）。能不新增就不新增。
- 順手修掉 §1.2 那個「suggest_ticket 清空計數」的繞道漏洞。
- 兩顆獨立計數器會出現「聽不懂 2 次 ＋ 找不到 2 次 ＝ 都沒到門檻，永遠不轉」的鬼打牆。

代價：不能把「聽不懂」和「找不到」調成不同容忍度。如果要分開調，就走 4.1b。

**4.1b（備案）**：新增 `max_no_kb` 門檻鍵 → HiBot `runtime_config._THRESHOLD_KEYS` 加一顆
（`core/runtime_config.py:29`）、HiSupport 後台加旋鈕（照 `max_unclear` 抄一遍：
`BotResponder` 常數、`AiAgentController.php:39/88/188/215/231`、`AiAgentSettings.vue:25/96/129/381`）。
**若走這條，上線後務必到後台「HiBot 端目前生效設定」面板回讀確認，別只看儲存成功。**

### 4.2 改動清單（方案 B ＋ 4.1 合一）

**HiBot 端**

| 檔案 | 改什麼 |
|---|---|
| `prompts/brain_system.txt` | 新增 action `narrow_down` 說明；硬規則 3 改成「沒資料時：預算有剩 → `narrow_down`（依最接近索引卡寫二選一追問）；預算用完 → `suggest_ticket`」；user prompt 補「目前沒進展輪次／上限」讓腦看得到預算 |
| `prompts/brain_user.txt` | 帶入 `no_progress_count` / `max_no_progress` |
| `nodes/brain.py` | `VALID_ACTIONS` 加 `narrow_down`；決定單新欄位 `narrow_down_message`；**幻覺降級路徑（`action=suggest_ticket` 那兩處）也要吃預算**，否則腦挑錯編號時會繞過預算 |
| `core/orchestrator.py` | 新 `_execute_narrow_down`（計數 +1、達門檻改走 `_execute_force_escalation`）；`_execute_suggest_ticket` 拿掉無條件歸零（`:455`）、改成只有 §2.1 四種豁免情況才直接提議；`_execute_answer_with_faq/kb`、`acknowledge_confirmation` 維持歸零 |
| `core/state.py` | `intent_state` 欄位語意更新；**讀取一律 `.get(key, 0)`**——正式機有活著的舊 session，缺欄位不能炸 |
| `scripts/run_routing_exam.py` | 補考題：①沒資料但可收斂 → 該 `narrow_down` ②收斂後答得出 → 該 `answer_with_kb` ③預算用完 → 該 `suggest_ticket` ④**紅線**：點名要真人／要查訂單 → 必須第一輪就 `suggest_ticket`，不得被拖 |

**HiSupport 端**

| 檔案 | 改什麼 | 必要性 |
|---|---|---|
| `app/Services/BotResponder.php:186` | `escalate()` 收了 `$reason` 卻**整個方法沒用到、沒落地**——目前無法回答「多少比例的轉真人是 no_kb_match」 | **建議先做**，見 §6 |
| `docs/2026-07-04-hibot-handoff-contract.md` | 補 Amendment：§4.1 三層表更新、`handoff_reason` 值域說明 | 拍板後補 |
| 旋鈕相關 | 走 4.1 合一＝**零改動**；走 4.1b 才要加 | 視拍板 |

### 4.3 交接摘要要帶上釐清歷程

`build_handoff_summary`（`core/state.py`）補一行，例如：

```
• 釐清歷程：已追問 3 輪仍無法定位（用戶提過「影片看不了」「手機上」），KB 無對應文章
```

兩個用途：真人接手不用從頭問一遍；以及這是**最乾淨的「該補哪篇文章」訊號**，
`/kb-review` 可以直接吃。現在的 `no_kb_match` 只知道「沒命中」，
改完之後還會知道「使用者真正想問的是什麼」——這其實是本案的附帶收穫。

---

## 5. 風險與取捨（不修飾）

1. **追問品質決定成敗。** 如果 `narrow_down` 寫出來的是「請您再描述清楚一點」，那就是讓使用者
   多受兩輪折磨再轉走，**比現況更差**。驗收標準必須明確要求「問句要含具體選項、且選項來自真實
   索引卡」，不是「有追問就算過」。
2. **首次真人回應時間變長。** 原本第 1 輪就進真人待辦，現在最多第 3 輪。量小（一天幾十則）影響
   有限，但客服端要知道這件事。**不建議**在追問期間先發 Slack 軟通知——會變成雙重打擾。
3. **成本。** 每次未命中多燒 2 次分診＋寫手。以目前量級可忽略。
4. **可能只是把問題往後挪。** 最壞情況是「追問 3 輪還是轉走」＝純粹多花錢多惹人。
   §6 的量測就是為了 4 週後能誠實回答這件事。
5. **改 prompt 必須重跑考卷。** 分診腦的 system prompt 是整個機器人的行為中樞，
   動它等於動全部行為。30 題 ≥96%、紅線零失誤，沒過不上線。

---

## 6. 沒有這個就無法驗收（建議排在功能之前）

**現在量不到任何東西。** `escalate()` 的 `$reason` 沒落地（`BotResponder.php:186`），
HiSupport 資料庫裡沒有任何欄位記著「這通為什麼轉真人」。

所以現在無法回答最基本的問題：**到底有多少比例的轉真人是 `no_kb_match`？**
如果實際上只佔 15%，這整個改動的天花板就只有 15%，不值得動分診腦的 prompt。

建議順序：

1. **先做**：`handoff_reason` 落地（存欄位或寫進交接摘要皆可）→ 收 2 週基線。
2. **看數字再決定**：`no_kb_match` 佔比夠高（憑感覺應該不低，但要有數字）才動 §4。
3. **上線後對照**：no_kb 轉出率是否下降、`narrow_down` 後成功答出的比例（＝預算真的有用），
   以及轉真人總量有沒有反而上升（＝追問把人問煩了）。

第 3 點那個「narrow_down 後成功答出的比例」是這個功能唯一誠實的成績單。
如果它低於 3 成，代表 KB 覆蓋率才是真問題，追問只是在裝飾——那時候該做的是補文章，不是改流程。

---

## 7. 待拍板清單（2026-09-15 實作時採用的決定）

> 下表「本文建議」欄即實作採用值。Adam 要推翻任一項都還來得及——程式面都是常數或旋鈕。


| # | 題目 | 本文建議 |
|---|---|---|
| 1 | 預算合一，還是 unclear／no_kb 兩顆分開調？ | **合一**（§4.1）——沿用 `max_unclear`，HiSupport 零改動 |
| 2 | 門檻幾輪？ | 3（沿用現行 `max_unclear` 預設，不另設值） |
| 3 | 中途換新題目要不要重置計數？ | **要**——`target_intent_index` 變動＝新問題，重新給預算 |
| 4 | §2.1 四種豁免（點名要真人／查個資／寫手舉手／已拒絕過）確認不吃預算？ | 確認。這條沒守住，改動會變負分 |
| 5 | 加「不滿情緒 → 預算失效直接轉」嗎？ | **建議加**（§2.2），訊號現成、零成本 |
| 6 | 先做 §6 量測收基線，還是直接動功能？ | **先量測**——否則無從判斷值不值得做、做完有沒有效 |

---

## 8. 實作紀錄（2026-09-15，分支 `feature/clarify-budget`）

### 8.1 與提案的差異：多了「候選閘門」

提案 §3 方案 B 只講「讓分診腦寫追問句」。實作時補上**本案真正的核心機制**——
決定單新增 `narrow_candidates`，走與 `kb_article_ids` 同一套幻覺白名單驗證
（`nodes/brain.py`），**驗證後不足 2 篇真實文章就當場降級 `suggest_ticket`**。

為什麼要這條：它把「這輪追問值不值得」從模型的自由心證，變成程式可驗證的事實。

| 情況 | 判定 | 為什麼對 |
|---|---|---|
| 湊得出 ≥2 篇沾邊文章 | `narrow_down` | KB 真有東西、只是不知道用戶要哪個 → 追問能收斂 |
| 湊不出 2 篇 | 立刻 `suggest_ticket` | KB 根本沒這塊 → 再問三輪也生不出文章 |

副作用是**追問句的品質變成結構逼出來的**：腦手上握著兩張具體的卡，寫出來的自然是
「您是指影片播不出來，還是課程觀看期限到了？」，不必靠 prompt 拜託它別問廢話。

### 8.2 收斂狀態（提案沒寫到、但沒有它就不會收斂）

硬規則 5「判斷要新鮮」會讓每輪的腦從零重新解讀 → 3 輪＝3 次獨立亂槍。新增
`state["narrowing"]`，餵進分診腦的 user prompt：

| 欄位 | 用途 |
|---|---|
| `rounds` | 本條收斂線追問幾輪（換新題目歸零） |
| `total_rounds` | **整場累計、永不歸零**——無限追問的唯一防線 |
| `original_question` | 用戶最初那句，別被後續追問稀釋 |
| `offered` | 已提過的候選 kb_id，**下一輪不得重複** |
| `ruled_out` | 用戶明確否定的方向 |

`offered` 是讓第 2 輪比第 1 輪聰明的唯一依據。

### 8.3 順手修掉的兩個既有漏洞

1. **§1.2 的繞道**：`_execute_suggest_ticket` 開頭那行無條件歸零已移除。它讓 `no_kb_match`
   每次都抹掉「聽不懂」的累積，兩條失敗路徑永遠合不起來，`max_unclear` 旋鈕等於被繞過去。
2. **`_execute_continue_intent` 的預算回血**（本次才發現）：舊版先歸零、再可能 fall through 到
   `_execute_uncertainty`。連續 `continue_intent` 但給不出編號時，預算每輪回滿＝永遠轉不出去。
   歸零改由下游真的答出東西時做。

### 8.4 不吃預算的豁免（§2.1 的程式面落實）

點名要真人／要查個資／寫手舉手／已拒絕過轉真人 → 走原路，第一輪就提議轉。
另加 §2.2 的**情緒煞車**：`user_emotion == "不滿"` 時跳過收斂直接提議轉真人。
prompt 端也寫成硬規則 4b，兩層都擋。

### 8.5 改了什麼

| 檔案 | 內容 |
|---|---|
| `core/state.py` | `narrowing` 區塊；`consecutive_unclear_count` 語意擴成「沒進展」；交接摘要加「釐清歷程」；刪殭屍欄位 `max_unclear_count`；新 reason label `narrow_exhausted` |
| `nodes/brain.py` | `narrow_down` action、`narrow_candidates` 候選閘門（`MIN_NARROW_CANDIDATES=2`）、收斂狀態進 prompt、`max_tokens` 600→800 |
| `core/orchestrator.py` | `_execute_narrow_down`、`_reset_progress`／`_budget_exhausted` 共用預算、硬上限 `MAX_NARROW_ROUNDS_HARD=6`、情緒煞車、換新題目重置 |
| `nodes/ticket_handler.py` | 拒絕轉真人時清收斂狀態（`total_rounds` 保留） |
| `prompts/brain_system.txt` | `narrow_down` action 說明、硬規則 3 改寫、新增硬規則 4b（不得拖延）、JSON schema 兩欄 |
| `prompts/brain_user.txt` | 收斂狀態區塊、預算顯示成 `n/上限` |
| `tests/test_clarify_budget.py` | **新檔，18 個測試**，釘死候選閘門／預算合一／豁免／硬上限／舊漏洞／舊 session 相容 |
| `tests/routing_exam.json` | 30 → 34 題（旗艦收斂題、收斂成功題、零候選不得拖、不得重複已提方向）；22/23 補 `narrow_down` 為合法解 |
| `scripts/run_routing_exam.py` | 支援 `narrowing` 預設狀態、`expect_min_candidates`／`forbid`／`forbid_candidates`；紅線擴為 `{16,17,18,28,30,33,34}` |

### 8.6 驗證狀態

- ✅ `pytest tests/` — **158 passed**（既有 140 ＋ 新 18）
- ✅ 端到端假對話：追問 2 輪 → 第 3 次沒進展轉真人，摘要帶釐清歷程
- ❌ **`scripts/run_routing_exam.py` 未跑** — 要真 API、要花錢。CLAUDE.md 規定改 prompt 後必跑
  （≥96% 且紅線零失誤），**這是上線前的必要關卡，尚未通過**。
- ❌ §6 的基線量測（HiSupport `handoff_reason` 落地）未做——沒有它，上線後無法判斷這個改動
  到底有沒有效，只能憑感覺。

### 8.7 上線前還缺的

1. 跑分診考卷，紅線零失誤才算過。特別注意新加的 Q31（旗艦收斂題）與 Q33/34（不得拖延、不得重複）。
2. HiSupport 端補 `handoff_reason` 落地（`BotResponder.php:186` 的 `$reason` 目前沒用到）。
3. HiSupport `docs/2026-07-04-hibot-handoff-contract.md` 補 Amendment：§4.1 三層表加 `narrow_down`
   這一層、`handoff_reason` 值域加 `narrow_exhausted`。HiSupport 只是原樣轉傳 reason，不會壞，
   但文件列舉了值域，要同步。
