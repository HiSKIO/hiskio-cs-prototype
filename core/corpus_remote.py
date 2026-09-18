"""HiSupport 對話語料——機器人的**第二個、次級的**知識來源。

一句話說清楚它跟 KB 的差別:
  說明中心文章(kb_remote)是「我們現在願意講的話」;語料是「某個客服在某個時間點講過的話」。
  後者很可能是舊政策(客服時效 20/24 小時那次分岔事故就是這樣來的),**永遠不該蓋過前者**。
  所以兩者在分診腦的系統指令裡分區塊呈現,規則寫白話:衝突以說明中心為準,語料只在文章沒涵蓋時用。

為什麼另開一個模組、不併進 kb_remote:
  kb_remote.sync() 那段剪枝防呆(active_ids 欄位在不在、id 乾不乾淨、游標回退)綁死「一個 feed 一份索引」,
  硬塞第二種資料進同一個迴圈,是在防呆密度很高的程式上動刀。分開之後任一條壞掉不拖垮另一條,
  而且兩份索引在磁碟上就是分開的,優先級好表達也好稽核。

跟 kb_remote 刻意保持一致的地方(讓兩條線共用一套心智模型):
  純事件驅動(開機對齊 + HiSupport 門鈴 POST /api/kb/refresh),**無定時輪詢**(Adam 拍板);
  索引卡 summary/key_questions 由寫手 LLM 生成、失敗退化不中斷;HiSupport 失聯就沿用最後快取。

跟 kb_remote 刻意不同的地方:
  - **沒有 url**。語料沒有公開頁,也永遠不該出現在給訪客看的參考連結裡——orchestrator 的 sources
    只收有 url 的,所以這件事是靠資料形狀保證的,不是靠誰記得過濾。
  - **標題是 LLM 生的**(對話沒有天生的標題),類別固定「對話語料」——那同時也是次級身分的標記。
  - **verbatim 恆為 False**。「一字不改照答」是給人工審過的標準答案用的,語料沒有經過那道審查。

環境變數:
  - HISUPPORT_KB_URL / HISUPPORT_KB_KEY  與 kb_remote 共用(同一個 HiSupport、同一把金鑰)
  - CORPUS_INDEX_PATH / CORPUS_DIR / CORPUS_STATE_PATH  落地位置
    **正式機務必指向持久磁碟**(比照 KB_REMOTE_*):不設的話每次部署容器全新、索引卡全重建,
    期間機器人等於少了一整個知識源。
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

CORPUS_PREFIX = "hsc_"

CORPUS_CATEGORY = "對話語料"

_sync_lock = threading.Lock()

_INDEX_PROMPT = """以下是一段 HiSKIO 客服與客戶的真實對話紀錄（已去識別化）。
請把它整理成一張知識索引卡。

只回傳合法 JSON，格式：
{{"title": "15 字內，描述這段對話解決了什麼問題", "summary": "30-60 字，客服在這段對話裡給出的答案重點", "key_questions": ["...", "...", "..."]}}

key_questions 是 3-5 個「其他用戶可能會問、而這段對話答得出來」的具體問法。
summary 只寫**客服給的答案**，不要寫客戶的情緒或客服的寒暄。

# 對話紀錄
{transcript}

只輸出 JSON。
"""

_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


def enabled() -> bool:
    """與 kb_remote 同一個開關:接了 HiSupport 才有語料可言。"""
    return bool((os.getenv("HISUPPORT_KB_URL") or "").strip())


def _paths() -> tuple[Path, Path, Path]:
    return (
        Path(os.getenv("CORPUS_INDEX_PATH", "data/corpus_index.json")),
        Path(os.getenv("CORPUS_DIR", "data/corpus")),
        Path(os.getenv("CORPUS_STATE_PATH", "data/corpus_state.json")),
    )


def load_corpus_index() -> list[dict]:
    """讀語料索引卡。停用＝空(舊快取檔不外漏)。壞檔不擋服務,重同步會蓋回。"""
    if not enabled():
        return []
    index_path, _, _ = _paths()
    if not index_path.exists():
        return []
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        logger.exception("corpus index 讀取失敗:%s", index_path)
        return []
    if not isinstance(data, list):
        logger.warning("corpus index 結構非預期(非 list),當空清單:%s", index_path)
        return []
    return data


def sync(full: bool = False) -> dict:
    """向 HiSupport 拉已勾選的語料,落地+剪枝+清快取。回統計 dict(絕不丟例外)。"""
    if not enabled():
        return {"skipped": "disabled"}

    with _sync_lock:
        index_path, corpus_dir, state_path = _paths()
        params: dict = {}
        if not full:
            cursor = _read_state(state_path).get("last_generated_at")
            if cursor:
                params["updated_since"] = cursor

        try:
            feed = _fetch_feed(params)
        except Exception as exc:  # noqa: BLE001 — 失聯 fallback:保留最後一次成功資料
            logger.warning("corpus 同步失敗(沿用最後快取):%s", exc)
            return {"error": str(exc)}

        if not isinstance(feed, dict):
            logger.warning("corpus 同步:回應結構非物件(%s),沿用最後快取", type(feed).__name__)
            return {"error": "unexpected feed shape"}

        raw_items = feed.get("transcripts")
        items = raw_items if isinstance(raw_items, list) else []
        raw_active = feed.get("active_ids")
        # 判「回應有沒有給 active_ids 這個欄位」而不是「它是不是空的」:空陣列＝客服真的把全部語料
        # 取消勾選了(該照清);欄位缺席/型別不對＝回應壞掉(才保守不剪)。與 kb_remote 同一套判準。
        active_ids_present = isinstance(raw_active, list)
        active = {f"{CORPUS_PREFIX}{i}" for i in (raw_active or []) if _valid_id(i)}

        index = {e["id"]: e for e in load_corpus_index()}

        corpus_dir.mkdir(parents=True, exist_ok=True)
        indexed = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            raw_id = item.get("id")
            if not _valid_id(raw_id):
                logger.warning("corpus 略過無效 id 的語料:%r", raw_id)
                continue
            rid = f"{CORPUS_PREFIX}{raw_id}"
            # active_ids 有給、但這段不在其中＝反正稍後會被剪 → 不寫檔、不花寫手 LLM 建索引卡
            if active_ids_present and rid not in active:
                continue

            body = render_transcript(item.get("transcript"))
            if not body:
                # 空語料不建卡:HiSupport 端已經擋過一層,這裡是第二道——
                # 生一張沒有內容的索引卡,只會讓分診腦多一個永遠挑不到東西的選項。
                logger.warning("corpus 略過空語料:%s", rid)
                continue

            _write_transcript_md(corpus_dir / f"{rid}.md", body)
            index[rid] = {
                "id": rid,
                "category": CORPUS_CATEGORY,
                # 語料沒有公開頁。url=None 讓它天生無法進入 orchestrator 給訪客看的 sources——
                # 那是「別人的對話」,外流出去就是資料外洩,所以不靠過濾、靠形狀保證。
                "url": None,
                # 「一字不改照答」只給人工審過的標準答案用;語料沒經過那道審查,恆為 False。
                "verbatim": False,
                "channel": item.get("channel"),
                "updated_at": item.get("updated_at"),
                **_index_card(body),
            }
            indexed += 1

        # 剪枝:取消勾選/對話被重開/被軟刪 → 索引與內文一起移除。
        pruned = 0
        suspicious_wipe = (not active_ids_present) and bool(index) and not full
        if suspicious_wipe:
            logger.warning(
                "corpus:回應缺 active_ids 欄位但本地有 %d 段,疑似回應異常,跳過剪枝(可手動全量同步歸零)",
                len(index),
            )
        else:
            for rid in list(index.keys()):
                if rid not in active:
                    index.pop(rid)
                    (corpus_dir / f"{rid}.md").unlink(missing_ok=True)
                    pruned += 1

        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(
            json.dumps(list(index.values()), ensure_ascii=False, indent=2), encoding="utf-8")
        if not suspicious_wipe:
            _write_state(state_path, {"last_generated_at": _rewind_cursor(feed.get("generated_at"))})

        _bust_caches()
        stats = {"indexed": indexed, "pruned": pruned, "active": len(active)}
        logger.info("corpus 同步完成:%s", stats)
        return stats


def render_transcript(transcript) -> str:
    """把 [{speaker, text}] 攤成「客戶：…／客服：…」的逐字文字。結構壞掉＝回空字串(呼叫端跳過)。"""
    if not isinstance(transcript, list):
        return ""
    lines = []
    for row in transcript:
        if not isinstance(row, dict):
            continue
        speaker = str(row.get("speaker") or "").strip()
        text = str(row.get("text") or "").strip()
        if speaker and text:
            lines.append(f"{speaker}：{text}")
    return "\n".join(lines)


def corpus_meta(rid: str) -> dict:
    """語料的權威中繼資料(來自 JSON 索引)。找不到回空 dict。"""
    for e in load_corpus_index():
        if e.get("id") == rid:
            return e
    return {}


def _fetch_feed(params: dict) -> dict:
    """GET {HISUPPORT_KB_URL}/api/hibot/transcripts(Bearer 金鑰)。用 stdlib,零新相依。"""
    base = (os.getenv("HISUPPORT_KB_URL") or "").strip().rstrip("/")
    key = (os.getenv("HISUPPORT_KB_KEY") or os.getenv("HIBOT_API_KEY") or "").strip()
    url = base + "/api/hibot/transcripts"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "Authorization": f"Bearer {key}",
    })
    with urllib.request.urlopen(req, timeout=15) as res:  # noqa: S310 — url 來自環境設定
        return json.loads(res.read().decode("utf-8"))


def _index_card(transcript_text: str) -> dict:
    """索引卡(title+summary+key_questions):LLM 生成,失敗退化不擋同步。

    退化版刻意不猜標題:拿對話第一句當摘要、標題標成「客服對話」,寧可難挑中,
    也不要生一個看起來煞有其事、其實是瞎掰的標題騙分診腦去挑它。
    """
    try:
        card = _llm_index_card(transcript_text)
        title = str(card.get("title") or "").strip()
        summary = str(card.get("summary") or "").strip()
        questions = [str(q).strip() for q in (card.get("key_questions") or []) if str(q).strip()]
        if title and summary and questions:
            return {"title": title, "summary": summary, "key_questions": questions}
        raise ValueError("LLM 索引卡欄位不完整")
    except Exception as exc:  # noqa: BLE001
        logger.warning("語料索引卡 LLM 生成失敗(退化):%s", exc)
        first = transcript_text.splitlines()[0] if transcript_text else ""
        return {"title": "客服對話", "summary": first[:60], "key_questions": [first[:40] or "客服對話"]}


def _llm_index_card(transcript_text: str) -> dict:
    """呼叫寫手 LLM 產語料索引卡。測試時整顆換掉。"""
    from core.llm_client import call_writer

    raw = call_writer(_INDEX_PROMPT.format(transcript=transcript_text[:4000]))
    match = _JSON_OBJ_RE.search(raw or "")
    if not match:
        raise ValueError(f"LLM 回傳非 JSON:{(raw or '')[:80]}")
    return json.loads(match.group(0))


def _valid_id(raw) -> bool:
    """語料 id 必須是乾淨的正整數(HiSupport 的 conversation id)。防路徑跳脫、防髒資料。"""
    return raw is not None and re.fullmatch(r"\d+", str(raw)) is not None


def _rewind_cursor(generated_at: str | None, seconds: int = 5) -> str | None:
    """游標往回退幾秒,避開「查詢完成→產 generated_at」空檔與同秒編輯漏抓(同 kb_remote)。"""
    if not generated_at:
        return generated_at
    try:
        from datetime import datetime, timedelta
        return (datetime.fromisoformat(generated_at) - timedelta(seconds=seconds)).isoformat()
    except (ValueError, TypeError):
        return generated_at


def _write_transcript_md(path: Path, body: str) -> None:
    """只寫逐字本體,不寫 front matter(中繼資料一律以 corpus_index.json 為權威,同 kb_remote 的教訓)。"""
    path.write_text(body + "\n", encoding="utf-8")


def _read_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _write_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def _bust_caches() -> None:
    """語料變了 → 清掉吃它的快取(分診腦系統指令含整份索引卡,必清)。"""
    from nodes import brain, kb_indexer

    kb_indexer._load_corpus_index.cache_clear()
    brain.reset_caches()
