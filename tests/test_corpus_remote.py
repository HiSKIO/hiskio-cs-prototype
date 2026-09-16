"""對話語料（次級知識根）測試。

釘死的都是「破掉就會出事」的:
①未接 HiSupport＝完全停用(現行行為零影響) ②同步落成 corpus_index.json + corpus/hsc_<id>.md
③active_ids 剪枝(客服取消勾選／對話被重開 → 語料要消失) ④LLM 索引失敗退化不擋同步
⑤語料 id 進分診腦白名單(挑得到) ⑥**語料的 url 恆為 None** → 天生進不了給訪客看的 sources
⑦分診腦系統指令裡語料自成區塊、且帶著「以 KB 為準」的規則 ⑧同步後快取確實被清。
用假 HTTP 與假 LLM,不連線、不花錢。
"""
import json

import pytest

from core import corpus_remote
from nodes import brain, faq_matcher, kb_indexer


FEED = {
    "transcripts": [
        {
            "id": 31,
            "channel": "web",
            "resolved_at": "2026-09-14T10:00:00+08:00",
            "updated_at": "2026-09-14T10:00:00+08:00",
            "transcript": [
                {"speaker": "客戶", "text": "電子書可以下載離線看嗎？"},
                {"speaker": "客服", "text": "電子書只能線上閱讀，不提供下載。"},
            ],
        },
        {
            "id": 44,
            "channel": "mail",
            "resolved_at": "2026-09-14T11:00:00+08:00",
            "updated_at": "2026-09-14T11:00:00+08:00",
            "transcript": [
                {"speaker": "客戶", "text": "發票可以改開公司嗎？"},
                {"speaker": "客服", "text": "開立後七天內可改，來信附統編即可。"},
            ],
        },
    ],
    "active_ids": [31, 44],
    "generated_at": "2026-09-14T12:00:00+08:00",
}


def _bust():
    faq_matcher._load_faq.cache_clear()
    kb_indexer._load_kb_index.cache_clear()
    kb_indexer._load_corpus_index.cache_clear()
    brain.reset_caches()


@pytest.fixture
def corpus_env(tmp_path, monkeypatch):
    """啟用語料來源、資料落在臨時資料夾;本地 KB/FAQ 為空白名單。"""
    (tmp_path / "kb_index.json").write_text("[]", encoding="utf-8")
    (tmp_path / "faq.json").write_text("[]", encoding="utf-8")
    monkeypatch.setenv("KB_INDEX_PATH", str(tmp_path / "kb_index.json"))
    monkeypatch.setenv("FAQ_PATH", str(tmp_path / "faq.json"))
    monkeypatch.setenv("KB_DIR", str(tmp_path / "kb"))
    monkeypatch.setenv("HISUPPORT_KB_URL", "http://hs.test")
    monkeypatch.setenv("HISUPPORT_KB_KEY", "kb-secret")
    # KB 那條線也要指到臨時目錄:不隔離的話會讀到 repo 裡真實的 35 篇遠端索引,
    # 讓「語料不進 KB 索引」這個斷言看起來像壞的、其實是測試自己汙染。
    monkeypatch.setenv("KB_REMOTE_DIR", str(tmp_path / "kb_remote"))
    monkeypatch.setenv("KB_REMOTE_INDEX_PATH", str(tmp_path / "kb_remote_index.json"))
    monkeypatch.setenv("KB_REMOTE_STATE_PATH", str(tmp_path / "kb_remote_state.json"))
    monkeypatch.setenv("CORPUS_DIR", str(tmp_path / "corpus"))
    monkeypatch.setenv("CORPUS_INDEX_PATH", str(tmp_path / "corpus_index.json"))
    monkeypatch.setenv("CORPUS_STATE_PATH", str(tmp_path / "corpus_state.json"))
    monkeypatch.setattr(
        corpus_remote, "_llm_index_card",
        lambda transcript: {
            "title": "語料標題", "summary": "語料摘要", "key_questions": ["這題怎麼處理？"],
        },
    )
    _bust()
    yield tmp_path
    _bust()


def _fake_fetch(monkeypatch, payload):
    calls = []

    def fetch(params):
        calls.append(params)
        return payload

    monkeypatch.setattr(corpus_remote, "_fetch_feed", fetch)
    return calls


# ===== 停用時零影響 =====

def test_disabled_without_url(monkeypatch):
    monkeypatch.delenv("HISUPPORT_KB_URL", raising=False)
    assert corpus_remote.enabled() is False
    assert corpus_remote.sync()["skipped"] == "disabled"
    assert corpus_remote.load_corpus_index() == []


# ===== 落地 =====

def test_sync_writes_index_and_transcripts(corpus_env, monkeypatch):
    _fake_fetch(monkeypatch, FEED)

    stats = corpus_remote.sync()

    assert stats["indexed"] == 2
    index = json.loads((corpus_env / "corpus_index.json").read_text(encoding="utf-8"))
    assert {e["id"] for e in index} == {"hsc_31", "hsc_44"}

    entry = next(e for e in index if e["id"] == "hsc_31")
    assert entry["title"] == "語料標題"
    assert entry["category"] == corpus_remote.CORPUS_CATEGORY
    assert entry["verbatim"] is False

    body = (corpus_env / "corpus" / "hsc_31.md").read_text(encoding="utf-8")
    assert "客戶：電子書可以下載離線看嗎？" in body
    assert "客服：電子書只能線上閱讀，不提供下載。" in body


def test_corpus_ids_are_selectable_by_the_brain(corpus_env, monkeypatch):
    _fake_fetch(monkeypatch, FEED)
    corpus_remote.sync()

    # 語料不進 KB 索引(那是權威知識根,要保持乾淨)……
    assert {e["id"] for e in kb_indexer._load_kb_index()} == set()
    # ……但要進分診腦的合法編號白名單,否則腦挑了語料會被當幻覺剔掉
    assert {"hsc_31", "hsc_44"} <= kb_indexer.all_valid_ids()

    loaded = kb_indexer.load_kb_article("hsc_31")
    assert loaded and "電子書只能線上閱讀" in loaded["content"]


# ===== 資料外洩防線 =====

def test_corpus_has_no_url_so_it_can_never_reach_visitor_sources(corpus_env, monkeypatch):
    """orchestrator 的 sources 只收有 url 的文章。語料 url 恆為 None＝天生外洩不了。

    這件事靠資料形狀保證,不是靠誰記得在某處加過濾——語料是別人的對話,
    出現在訪客看得到的「參考來源」裡就是資料外洩。
    """
    _fake_fetch(monkeypatch, FEED)
    corpus_remote.sync()

    for entry in corpus_remote.load_corpus_index():
        assert entry["url"] is None

    assert kb_indexer.load_kb_article("hsc_31")["url"] is None


def test_corpus_is_never_verbatim(corpus_env, monkeypatch):
    """「一字不改照答」只給人工審過的標準答案用;語料沒經過那道審查。"""
    _fake_fetch(monkeypatch, FEED)
    corpus_remote.sync()

    assert kb_indexer.load_kb_article("hsc_31")["verbatim"] is False


# ===== 剪枝:收得回來 =====

def test_unticked_conversation_is_pruned(corpus_env, monkeypatch):
    _fake_fetch(monkeypatch, FEED)
    corpus_remote.sync()

    # 客服取消勾選 31（或那段對話被重開）→ HiSupport 的 active_ids 不再有它
    _fake_fetch(monkeypatch, {"transcripts": [], "active_ids": [44], "generated_at": "2026-09-14T13:00:00+08:00"})
    stats = corpus_remote.sync()

    assert stats["pruned"] == 1
    assert {e["id"] for e in corpus_remote.load_corpus_index()} == {"hsc_44"}
    assert not (corpus_env / "corpus" / "hsc_31.md").exists()
    assert kb_indexer.load_kb_article("hsc_31") is None


def test_missing_active_ids_field_does_not_wipe_local_corpus(corpus_env, monkeypatch):
    """回應壞掉(缺 active_ids 欄位)≠ 全部取消勾選。保守不剪,沿用最後快取。"""
    _fake_fetch(monkeypatch, FEED)
    corpus_remote.sync()

    _fake_fetch(monkeypatch, {"transcripts": [], "generated_at": "2026-09-14T13:00:00+08:00"})
    stats = corpus_remote.sync()

    assert stats["pruned"] == 0
    assert len(corpus_remote.load_corpus_index()) == 2


def test_empty_active_ids_means_everything_was_unticked(corpus_env, monkeypatch):
    """空陣列是「客服真的把全部取消勾選了」——欄位有給就信任它,照清。"""
    _fake_fetch(monkeypatch, FEED)
    corpus_remote.sync()

    _fake_fetch(monkeypatch, {"transcripts": [], "active_ids": [], "generated_at": "2026-09-14T13:00:00+08:00"})
    corpus_remote.sync()

    assert corpus_remote.load_corpus_index() == []


# ===== 韌性 =====

def test_llm_failure_degrades_without_blocking_sync(corpus_env, monkeypatch):
    _fake_fetch(monkeypatch, FEED)

    def boom(transcript):
        raise RuntimeError("LLM 掛了")

    monkeypatch.setattr(corpus_remote, "_llm_index_card", boom)
    stats = corpus_remote.sync()

    assert stats["indexed"] == 2
    entry = next(e for e in corpus_remote.load_corpus_index() if e["id"] == "hsc_31")
    # 退化版不瞎掰標題:寧可難挑中,也不要生一個煞有其事的假標題騙分診腦去挑它
    assert entry["title"] == "客服對話"
    assert entry["summary"]


def test_upstream_failure_keeps_last_cache(corpus_env, monkeypatch):
    _fake_fetch(monkeypatch, FEED)
    corpus_remote.sync()

    def boom(params):
        raise RuntimeError("連不上 HiSupport")

    monkeypatch.setattr(corpus_remote, "_fetch_feed", boom)
    stats = corpus_remote.sync()

    assert "error" in stats
    assert len(corpus_remote.load_corpus_index()) == 2


def test_invalid_ids_and_empty_transcripts_are_skipped(corpus_env, monkeypatch):
    """髒 id 防路徑跳脫;空語料不建卡(生一張沒內容的索引卡只會讓腦多一個挑不到東西的選項)。"""
    _fake_fetch(monkeypatch, {
        "transcripts": [
            {"id": "../../etc/passwd", "transcript": [{"speaker": "客戶", "text": "壞東西"}]},
            {"id": 77, "transcript": []},
            {"id": 78, "transcript": "不是陣列"},
        ],
        "active_ids": [77, 78],
        "generated_at": "2026-09-14T13:00:00+08:00",
    })

    stats = corpus_remote.sync()

    assert stats["indexed"] == 0
    assert corpus_remote.load_corpus_index() == []


def test_incremental_cursor_is_sent_on_the_next_sync(corpus_env, monkeypatch):
    calls = _fake_fetch(monkeypatch, FEED)
    corpus_remote.sync()
    corpus_remote.sync()

    assert calls[0] == {}                      # 第一次:全量
    assert "updated_since" in calls[1]          # 第二次:帶游標增量


# ===== 分診腦看得到、而且知道它是次級的 =====

def test_brain_prompt_keeps_corpus_in_its_own_section_with_the_priority_rule(corpus_env, monkeypatch):
    _fake_fetch(monkeypatch, FEED)
    corpus_remote.sync()

    prompt = brain._system_prompt()

    assert "hsc_31" in prompt
    assert "語料標題" in prompt
    # 次級身分與優先級規則必須在指令裡講白話——v8 是一顆腦一次決定,
    # 挑完就進寫手,程式端沒有可以插手降權的名次,優先級只能寫在這裡。
    assert "次級知識" in prompt
    assert "一律以 KB 文章為準" in prompt


def test_sync_busts_the_brain_cache(corpus_env, monkeypatch):
    _fake_fetch(monkeypatch, FEED)
    corpus_remote.sync()
    assert "hsc_31" in brain._system_prompt()

    _fake_fetch(monkeypatch, {"transcripts": [], "active_ids": [], "generated_at": "2026-09-14T13:00:00+08:00"})
    corpus_remote.sync()

    # 沒清快取的話,分診腦會繼續拿一段已經被取消勾選的對話當知識講
    assert "hsc_31" not in brain._system_prompt()
    assert "目前沒有對話語料" in brain._system_prompt()
