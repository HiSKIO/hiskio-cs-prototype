"""分診腦(一顆腦改版核心,規格 data/design-one-brain-2026-07-06.md §3/§4)。

取代 v7 的「流水線四站+主管」:直接讀原件(全部 FAQ 問法表+全部 KB 索引卡),
一次呼叫產出「決定單」JSON。裁掉的站:入口分類/意圖判斷/FAQ 比對/KB 挑文/
事後評估/意圖選單(見規格 §6)。

防呆鐵則(對外流程不許壞):
- JSON 解析失敗 → fallback = acknowledge_uncertainty(禮貌請澄清),不丟例外。
- 幻覺編號(不存在的 faq_id/kb_id)→ 白名單剔除;剔完空手 → 降級 suggest_ticket
  (規格 §14-2:寧可轉真人,不硬答)。
"""
from __future__ import annotations

import logging
from functools import lru_cache

from core.llm_client import call_triage, load_prompt
from core.text_utils import extract_json_object, format_recent_history
from nodes import faq_matcher, kb_indexer

logger = logging.getLogger(__name__)

_SYSTEM_TPL = load_prompt("brain_system")
_USER_TPL = load_prompt("brain_user")

VALID_ACTIONS = {
    "greeting",
    "clarify",
    "answer_with_faq",
    "answer_with_kb",
    "acknowledge_out_of_scope",
    "acknowledge_uncertainty",
    "acknowledge_confirmation",
    "suggest_ticket",
    "continue_intent",
}
_VALID_ROLES = {"primary", "secondary", "context"}
# 轉真人精確原因白名單(交接資料要完整,Adam 2026-07-06 拍板):
# no_kb_match=知識庫沒資料(=該補 KB 的訊號)/needs_human=查個資等機器人辦不到的事/
# user_request=用戶點名要真人(2026-07-16 細分:「需要真人協助」對真人客服是零資訊廢話)
_VALID_HANDOFF_REASONS = {"no_kb_match", "needs_human", "user_request"}

FALLBACK_CLARIFY_MSG = "抱歉，我不太確定您想問的內容，能否再多描述一下您遇到的狀況？"


# ── 靜態資料表(FAQ 問法表+KB 索引卡;KB/FAQ 更新後需重啟,同既有慣例)──


@lru_cache(maxsize=1)
def _faq_table() -> str:
    faqs = faq_matcher._load_faq()
    if not faqs:
        return "（FAQ 清單為空）"
    lines = []
    for item in faqs:
        patterns = "、".join(item.get("question_patterns", []))
        lines.append(f"- {item['id']}（{item.get('category', '')}）：{patterns}")
    return "\n".join(lines)


@lru_cache(maxsize=1)
def _kb_cards() -> str:
    index = kb_indexer._load_kb_index()
    if not index:
        return "（KB 索引為空，請先跑 tools/_hibot_build_indexes.py）"
    lines = []
    for item in index:
        kqs = "、".join(item.get("key_questions", []))
        lines.append(
            f"- {item['id']}｜{item.get('title', '')}｜{item.get('category', '')}\n"
            f"  摘要：{item.get('summary', '')}\n"
            f"  常見問法：{kqs}"
        )
    return "\n".join(lines)


@lru_cache(maxsize=1)
def _corpus_cards() -> str:
    """對話語料的索引卡(**次級**知識根)。沒有語料＝明講一句,不要讓腦以為這區塊壞了。

    跟 KB 卡分開兩個區塊、不混在一起,是這整件事的關鍵:混在一起的話,
    「某個客服去年講過的舊政策」就會跟「現行的說明中心文章」平起平坐,
    而分診腦沒有任何依據能分辨哪張卡比較可信。"""
    index = kb_indexer._load_corpus_index()
    if not index:
        return "（目前沒有對話語料）"
    lines = []
    for item in index:
        kqs = "、".join(item.get("key_questions", []))
        lines.append(
            f"- {item['id']}｜{item.get('title', '')}\n"
            f"  摘要：{item.get('summary', '')}\n"
            f"  常見問法：{kqs}"
        )
    return "\n".join(lines)


@lru_cache(maxsize=1)
def _system_prompt() -> str:
    """規則+資料表烤成單一 system 字串(靜態 → 可吃 prompt cache)。"""
    return _SYSTEM_TPL.format(
        faq_table=_faq_table(), kb_cards=_kb_cards(), corpus_cards=_corpus_cards())


def _format_intent_log(intent_log: list[dict]) -> str:
    if not intent_log:
        return "（無）"
    lines = []
    for i, item in enumerate(intent_log):
        lines.append(
            f"  [{i}] {item['text']}"
            f"（status={item['status']}, role={item.get('role', 'primary')}, "
            f"in_scope={item.get('in_scope', True)}）"
        )
    return "\n".join(lines)


def _fallback_decision(reason: str) -> dict:
    return {
        "recommended_action": "acknowledge_uncertainty",
        "faq_id": None,
        "kb_article_ids": [],
        "clarify_message": FALLBACK_CLARIFY_MSG,
        "reason_to_user": None,
        "handoff_reason": "needs_human",
        "user_satisfied": False,
        "issue": {},
        "new_intents_to_log": [],
        "target_intent_index": None,
        "reason": reason,
    }


def decide(state: dict, user_message: str) -> dict:
    """一次呼叫產出決定單。任何失敗都回安全的 fallback,不丟例外。"""
    user = state["user_info"]
    sl = state["service_limits"]
    intent_log = state["intent_state"].get("intent_log") or []

    user_prompt = _USER_TPL.format(
        is_logged_in="是" if user["is_logged_in"] else "否",
        user_name=user.get("user_name") or "（訪客）",
        is_returning_customer="是" if user.get("purchase_history") else "否",
        turn_count=state["turn_count"],
        consecutive_unclear_count=state["intent_state"].get("consecutive_unclear_count", 0),
        off_topic_count=sl.get("off_topic_count", 0),
        max_off_topic_count=sl.get("max_off_topic_count", 3),
        ticket_suggested=state["ticket_state"].get("ticket_suggested", False),
        user_declined_ticket=(state["ticket_state"].get("user_decision") == "declined"),
        intent_log_str=_format_intent_log(intent_log),
        recent_history=format_recent_history(state["chat_history"], turns=10, empty="（首次對話）"),
        user_message=user_message,
    )

    raw = call_triage(
        user_prompt,
        max_tokens=600,
        temperature=0.0,
        system=_system_prompt(),
        cache_system=True,
        fallback="",
    )
    parsed = extract_json_object(raw)
    if parsed is None:
        logger.warning("brain 決定單解析失敗,fallback。raw=%r", raw[:300])
        return _fallback_decision("brain 解析失敗 fallback")

    action = parsed.get("recommended_action")
    if action not in VALID_ACTIONS:
        logger.warning("brain 回傳未知 action=%r,fallback", action)
        return _fallback_decision("未知 action fallback")

    # ── 幻覺編號白名單驗證(規格 §14-2)──
    valid_faq_ids = {f["id"] for f in faq_matcher._load_faq()}
    # 兩個知識根的編號都算合法(語料卡的 hsc_ 也要能挑),否則腦挑了語料會被當幻覺剔掉
    valid_kb_ids = kb_indexer.all_valid_ids()

    faq_id = parsed.get("faq_id")
    if faq_id is not None and faq_id not in valid_faq_ids:
        logger.warning("brain 給了不存在的 faq_id=%r,剔除", faq_id)
        faq_id = None

    kb_ids_raw = parsed.get("kb_article_ids") or []
    if not isinstance(kb_ids_raw, list):
        kb_ids_raw = []
    kb_ids = [str(x) for x in kb_ids_raw if isinstance(x, str) and x in valid_kb_ids][:3]
    dropped = [x for x in kb_ids_raw if x not in valid_kb_ids]
    if dropped:
        logger.warning("brain 給了不存在的 kb ids=%r,剔除", dropped)

    # 剔完空手 → 降級轉真人,不硬答(規格 §14-2)
    if action == "answer_with_faq" and faq_id is None:
        if kb_ids:
            action = "answer_with_kb"
        else:
            action = "suggest_ticket"
            parsed.setdefault("reason_to_user", "這個問題交給真人客服處理會更準確")
    if action in ("answer_with_kb", "continue_intent") and not kb_ids and faq_id is None:
        if action == "answer_with_kb":
            action = "suggest_ticket"
            parsed.setdefault("reason_to_user", "這個問題交給真人客服處理會更準確")

    # ── 清洗意圖清單 ──
    new_intents: list[dict] = []
    seen: set[str] = set()
    raw_intents = parsed.get("new_intents_to_log", []) or []
    if isinstance(raw_intents, list):
        for it in raw_intents:
            if not isinstance(it, dict):
                continue
            text = str(it.get("text", "")).strip()
            role = str(it.get("role", "primary")).lower().strip()
            if role not in _VALID_ROLES:
                role = "primary"
            if text and text not in seen:
                seen.add(text)
                new_intents.append({
                    "text": text, "role": role,
                    "in_scope": bool(it.get("in_scope", True)),
                })

    target_idx = parsed.get("target_intent_index")
    if target_idx is not None:
        try:
            target_idx = int(target_idx)
            if not (0 <= target_idx < len(intent_log)):
                target_idx = None
        except (TypeError, ValueError):
            target_idx = None

    issue = parsed.get("issue")
    if not isinstance(issue, dict):
        issue = {}

    handoff_reason = parsed.get("handoff_reason")
    if handoff_reason not in _VALID_HANDOFF_REASONS:
        handoff_reason = "needs_human"

    return {
        "recommended_action": action,
        "faq_id": faq_id,
        "kb_article_ids": kb_ids,
        "clarify_message": parsed.get("clarify_message"),
        "reason_to_user": parsed.get("reason_to_user"),
        "handoff_reason": handoff_reason,
        "user_satisfied": bool(parsed.get("user_satisfied", False)),
        "issue": {
            "category": str(issue.get("category") or "").strip() or None,
            "summary": str(issue.get("summary") or "").strip() or None,
            "user_emotion": str(issue.get("user_emotion") or "").strip() or None,
        },
        "new_intents_to_log": new_intents,
        "target_intent_index": target_idx,
        "reason": parsed.get("reason", ""),
    }


def reset_caches() -> None:
    """KB/FAQ/語料 更新後(或測試)清快取。"""
    _faq_table.cache_clear()
    _kb_cards.cache_clear()
    _corpus_cards.cache_clear()
    _system_prompt.cache_clear()
