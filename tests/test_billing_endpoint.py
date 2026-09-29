"""GET /api/billing：原封轉傳 OpenRouter /api/v1/key 的每期重置欄位（HIS-909）。

規格：HiSupport docs/2026-09-29-hibot-billing-monthly-design.md §3。
HiBot 不換算——本期用量、剩餘、重置時間全由 HiSupport 算；這裡只釘「怎麼轉傳」。
對外連線一律換成假回應（照 OpenRouter 官方文件格式），不打真 API。
"""
import io
import json
import os
import tempfile
from datetime import datetime, timedelta

import pytest

# 測試用臨時 DB，避免污染 data/prototype.db；並確保預設無金鑰（/api/* 開放）
os.environ["DB_PATH"] = os.path.join(tempfile.gettempdir(), "hibot_billing_test.db")
os.environ.pop("HIBOT_API_KEY", None)

from fastapi.testclient import TestClient  # noqa: E402
import app as app_module  # noqa: E402

client = TestClient(app_module.app)

# OpenRouter GET /api/v1/key 的 data（官方文件欄位；金鑰設「上限 $5、每月重置」）
KEY_DATA = {
    "label": "hibot",
    "limit": 5.0,
    "limit_reset": "monthly",
    "limit_remaining": 1.8,
    "usage": 37.2,
    "usage_daily": 0.12,
    "usage_weekly": 0.95,
    "usage_monthly": 3.2,
    "is_free_tier": False,
}


class _FakeResponse(io.BytesIO):
    """urlopen 回傳值的替身：支援 with 與 read()。"""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    """每顆測試都從冷快取開始、有設 OpenRouter 金鑰、/api/* 不鎖。"""
    monkeypatch.setitem(app_module._billing_cache, "at", 0.0)
    monkeypatch.setitem(app_module._billing_cache, "data", None)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.delenv("HIBOT_API_KEY", raising=False)


def _fake_openrouter(monkeypatch, body):
    """把對 OpenRouter 的連線換成假回應；body 是例外就丟出。回傳打過的 Request 清單。"""
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req)
        if isinstance(body, Exception):
            raise body
        return _FakeResponse(json.dumps(body).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return calls


def test_passes_through_period_fields(monkeypatch):
    _fake_openrouter(monkeypatch, {"data": KEY_DATA})
    r = client.get("/api/billing")
    assert r.status_code == 200
    body = r.json()
    assert body["limit_reset"] == "monthly"
    assert body["limit_remaining_usd"] == 1.8
    assert body["usage_daily_usd"] == 0.12
    assert body["usage_weekly_usd"] == 0.95
    assert body["usage_monthly_usd"] == 3.2


def test_existing_fields_unchanged(monkeypatch):
    """只加不改：舊版 HiSupport 讀的欄位一字不動。"""
    _fake_openrouter(monkeypatch, {"data": KEY_DATA})
    body = client.get("/api/billing").json()
    assert body["provider"] == "openrouter"
    assert body["scope"] == "key"
    assert body["limit_usd"] == 5.0
    assert body["usage_usd"] == 37.2
    assert body["remaining_usd"] == 1.8


def test_queries_v1_key_endpoint(monkeypatch):
    calls = _fake_openrouter(monkeypatch, {"data": KEY_DATA})
    client.get("/api/billing")
    assert len(calls) == 1
    assert calls[0].full_url == "https://openrouter.ai/api/v1/key"
    assert calls[0].get_header("Authorization") == "Bearer sk-or-test"


def test_limit_reset_null_is_carried(monkeypatch):
    """null＝上限永不重置，是有意義的值：鍵要帶、值照傳 null。"""
    _fake_openrouter(monkeypatch, {"data": {**KEY_DATA, "limit_reset": None}})
    body = client.get("/api/billing").json()
    assert "limit_reset" in body
    assert body["limit_reset"] is None


def test_limit_reset_missing_is_omitted(monkeypatch):
    """OpenRouter 沒回就不帶：補成 null 會被 HiSupport 當成「永不重置」而誤判。"""
    data = {k: v for k, v in KEY_DATA.items() if k != "limit_reset"}
    _fake_openrouter(monkeypatch, {"data": data})
    body = client.get("/api/billing").json()
    assert "limit_reset" not in body
    assert body["usage_monthly_usd"] == 3.2  # 其他欄位照轉


def test_fetched_at_is_utc_with_timezone(monkeypatch):
    """不帶時區的話瀏覽器會當本地時間、差 8 小時。"""
    _fake_openrouter(monkeypatch, {"data": KEY_DATA})
    fetched = datetime.fromisoformat(client.get("/api/billing").json()["fetched_at"])
    assert fetched.tzinfo is not None
    assert fetched.utcoffset() == timedelta(0)


def test_cache_hit_keeps_original_fetched_at(monkeypatch):
    """快取命中回快取當時的時間，不換成現在——面板「資料時間」才誠實。"""
    calls = _fake_openrouter(monkeypatch, {"data": KEY_DATA})
    first = client.get("/api/billing").json()["fetched_at"]
    second = client.get("/api/billing").json()["fetched_at"]
    assert len(calls) == 1
    assert second == first


@pytest.mark.parametrize("body", [
    {"data": {}},
    {"data": None},
    {},
    {"data": {"limit": 5.0}},                    # 有東西但沒有 usage
    {"data": {**KEY_DATA, "usage": None}},
])
def test_no_usage_returns_502(monkeypatch, body):
    """200 但沒有用量內容＝查不到：回 502，不拿 0 冒充「累計 $0」。"""
    _fake_openrouter(monkeypatch, body)
    assert client.get("/api/billing").status_code == 502


def test_network_failure_returns_502(monkeypatch):
    _fake_openrouter(monkeypatch, OSError("connection reset"))
    assert client.get("/api/billing").status_code == 502


def test_missing_openrouter_key_returns_409(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert client.get("/api/billing").status_code == 409
