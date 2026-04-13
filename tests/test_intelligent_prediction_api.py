"""智能预测 API 冒烟测试（依赖覆盖，不连真实 MySQL/Redis）。"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.intelligent_prediction.api.deps import get_prediction_db_session, get_prediction_service_dep
from app.intelligent_prediction.schemas.prediction import (
    BatchPredictionRequest,
    HorizonPreset,
    PredictionItem,
    PredictionRequest,
    PredictionResultSchema,
)
from app.intelligent_prediction.services.prediction_service import PredictionService
from main import app


class _DummySession:
    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    def add(self, *args: object, **kwargs: object) -> None:
        return None


async def _dummy_db():
    yield _DummySession()


class _FakePredictionService(PredictionService):
    async def predict_batch(self, body: BatchPredictionRequest) -> list[PredictionResultSchema]:
        _ = body
        return [
            PredictionResultSchema(
                warehouse="WH1",
                product_variety="VAR1",
                regional_manager=None,
                items=[
                    PredictionItem(
                        target_date=date(2026, 1, 1),
                        predicted_weight=Decimal("10.5"),
                        confidence="high",
                        warnings=[],
                    )
                ],
                provider_used="test",
                latency_ms=1.0,
                cost_usd=None,
            )
        ]

    async def persist_sync_results(self, session, rows, batch_id=None) -> None:
        _ = session, rows, batch_id


@pytest.fixture
def ip_client() -> TestClient:
    def _svc() -> _FakePredictionService:
        from app.intelligent_prediction.services.ai_client import get_ai_client
        from app.intelligent_prediction.services.cache_manager import get_cache_manager
        from app.intelligent_prediction.services.prompt_builder import PromptBuilder

        return _FakePredictionService(get_ai_client(), get_cache_manager(), PromptBuilder())

    app.dependency_overrides[get_prediction_db_session] = _dummy_db
    app.dependency_overrides[get_prediction_service_dep] = _svc
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_prediction_db_session, None)
    app.dependency_overrides.pop(get_prediction_service_dep, None)


def test_prediction_request_horizon_preset_overrides_days() -> None:
    req = PredictionRequest(
        warehouse="A",
        product_variety="B",
        horizon_days=7,
        horizon_preset=HorizonPreset.THREE_MONTHS,
    )
    assert req.horizon_days == 90


def test_predict_sync_with_override(ip_client: TestClient) -> None:
    body = {
        "items": [
            {
                "warehouse": "WH1",
                "product_variety": "VAR1",
                "horizon_days": 3,
                "history": [],
                "use_cache": False,
            }
        ]
    }
    r = ip_client.post("/api/v1/预测", json=body)
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    w = data[0]["items"][0]["predicted_weight"]
    assert float(w) == 10.5


def test_history_import_with_fake_service(ip_client: TestClient) -> None:
    from app.intelligent_prediction.api.deps import get_history_service_dep
    from app.intelligent_prediction.schemas.history import HistoryImportResponse
    from app.intelligent_prediction.services.history_service import HistoryService

    class _FakeHistory(HistoryService):
        async def import_excel(self, session, file_bytes: bytes, filename: str) -> HistoryImportResponse:
            _ = session, file_bytes, filename
            return HistoryImportResponse(inserted=1, skipped=0, errors=[])

    app.dependency_overrides[get_history_service_dep] = lambda: _FakeHistory()
    try:
        files = {
            "file": (
                "t.xlsx",
                b"x",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        }
        r = ip_client.post("/api/v1/送货历史/导入", files=files)
        assert r.status_code == 200
        assert r.json()["inserted"] == 1
    finally:
        app.dependency_overrides.pop(get_history_service_dep, None)


class _CountExecuteResult:
    def scalar_one(self) -> int:
        return 1


class _RowsExecuteResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def scalars(self) -> "_RowsExecuteResult":
        return self

    def all(self) -> list[object]:
        return self._rows


class _ListResultsDummySession:
    """模拟两次 execute：count + select。"""

    def __init__(self, rows: list[object]) -> None:
        self._rows = rows
        self._execute_calls = 0

    async def execute(self, _stmt: object) -> object:
        self._execute_calls += 1
        if self._execute_calls == 1:
            return _CountExecuteResult()
        return _RowsExecuteResult(self._rows)

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    def add(self, *args: object, **kwargs: object) -> None:
        return None


def test_predict_results_list_returns_schema(ip_client: TestClient) -> None:
    """GET /预测/结果 在假 Session 下应返回分页 JSON。"""
    row = SimpleNamespace(
        id=1,
        batch_id=None,
        regional_manager="张",
        warehouse="WH1",
        product_variety="VAR1",
        target_date=date(2026, 1, 2),
        predicted_weight=Decimal("3.25"),
        confidence="medium",
        warnings=["a"],
        provider_used="test",
        latency_ms=Decimal("1.5"),
        cost_usd=Decimal("0.001"),
        raw_response_excerpt=None,
        created_at=datetime(2026, 1, 1, 8, 0, 0),
    )

    async def _db():
        yield _ListResultsDummySession([row])

    app.dependency_overrides[get_prediction_db_session] = _db
    try:
        r = ip_client.get("/api/v1/预测/结果?page=1&page_size=10")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["warehouse"] == "WH1"
        assert float(data["items"][0]["predicted_weight"]) == 3.25
    finally:
        app.dependency_overrides[get_prediction_db_session] = _dummy_db


class _EnqueueFailSession:
    """predict_async：flush 后 batch 有 id，commit 可调用。"""

    def __init__(self) -> None:
        self._batch: object | None = None

    def add(self, obj: object) -> None:
        self._batch = obj

    async def flush(self) -> None:
        b = self._batch
        if b is not None and getattr(b, "id", None) in (None, ""):
            b.id = "33333333-3333-4333-8333-333333333333"

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


async def _enqueue_fail_db():
    yield _EnqueueFailSession()


def test_predict_async_enqueue_failure_returns_503() -> None:
    """Celery delay 失败时应返回 503 业务 JSON。"""
    body = {
        "items": [
            {
                "warehouse": "WH1",
                "product_variety": "VAR1",
                "horizon_days": 1,
                "history": [],
                "use_cache": False,
            }
        ]
    }
    app.dependency_overrides[get_prediction_db_session] = _enqueue_fail_db
    try:
        with patch(
            "app.intelligent_prediction.api.v1.predict.run_prediction_batch_task"
        ) as m_task:
            m_task.delay.side_effect = RuntimeError("broker_unreachable")
            with TestClient(app) as client:
                r = client.post("/api/v1/预测/异步", json=body)
    finally:
        app.dependency_overrides[get_prediction_db_session] = _dummy_db

    assert r.status_code == 503, r.text
    err = r.json()
    assert err.get("code") == "SERVICE_UNAVAILABLE"
    assert "入队" in err.get("message", "") or "Celery" in err.get("message", "")


def test_history_template_download_ok(ip_client: TestClient) -> None:
    """下载导入模板应返回 xlsx 流（Content-Disposition 须 latin-1 可编码）。"""
    r = ip_client.get("/api/v1/送货历史/模板")
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers.get("content-type", "")
    cd = r.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert "delivery_history_import_template.xlsx" in cd
