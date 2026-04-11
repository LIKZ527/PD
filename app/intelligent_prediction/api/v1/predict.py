"""预测 HTTP 接口。"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessException, ServiceUnavailableBusinessException
from app.core.logging import get_logger
from app.intelligent_prediction.api.deps import get_prediction_db_session, get_prediction_service_dep
from app.intelligent_prediction.models import PredictionBatch
from app.intelligent_prediction.models import PredictionResult as PredictionResultRow
from app.intelligent_prediction.schemas.prediction import (
    AsyncPredictionAccepted,
    BatchPredictionRequest,
    BatchStatusResponse,
    PredictionResultSchema,
    StoredPredictionResultItem,
    StoredPredictionResultListResponse,
)
from app.intelligent_prediction.services.prediction_service import PredictionService
from app.intelligent_prediction.tasks.export_tasks import run_prediction_batch_task

logger = get_logger(__name__)
router = APIRouter()


@router.post("", response_model=list[PredictionResultSchema])
async def predict_sync(
    body: BatchPredictionRequest,
    session: AsyncSession = Depends(get_prediction_db_session),
    svc: PredictionService = Depends(get_prediction_service_dep),
) -> list[PredictionResultSchema]:
    """同步批量预测并写库。"""
    try:
        results = await svc.predict_batch(body)
        await svc.persist_sync_results(session, results, batch_id=None)
        return results
    except BusinessException:
        raise
    except Exception as e:
        logger.exception("predict_sync failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/async", response_model=AsyncPredictionAccepted)
async def predict_async(
    body: BatchPredictionRequest,
    session: AsyncSession = Depends(get_prediction_db_session),
) -> AsyncPredictionAccepted:
    """异步预测：入队 Celery。"""
    batch: PredictionBatch | None = None
    try:
        batch = PredictionBatch(
            status="pending",
            meta=body.model_dump(mode="json"),
        )
        session.add(batch)
        await session.flush()
        predict_id_str = batch.id
        try:
            async_result = run_prediction_batch_task.delay(predict_id_str)
        except Exception as enqueue_err:
            logger.exception("predict_async celery enqueue failed batch_id=%s", predict_id_str)
            batch.status = "failed"
            batch.error_message = f"enqueue_failed: {enqueue_err}"[:2000]
            batch.completed_at = datetime.now(timezone.utc)
            await session.commit()
            raise ServiceUnavailableBusinessException(
                "异步预测任务无法入队，请检查 Celery Broker（如 CELERY_BROKER_URL）与 Worker 是否已启动",
            ) from enqueue_err
        batch.celery_task_id = async_result.id
        await session.flush()
        return AsyncPredictionAccepted(
            task_id=async_result.id,
            predict_id=uuid.UUID(predict_id_str),
            status="pending",
        )
    except BusinessException:
        raise
    except Exception as e:
        logger.exception("predict_async failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/results", response_model=StoredPredictionResultListResponse)
async def list_stored_prediction_results(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    warehouse: str | None = Query(None, description="仓库（精确匹配）"),
    product_variety: str | None = Query(None, description="品种（精确匹配）"),
    regional_manager: str | None = Query(None),
    batch_id: uuid.UUID | None = Query(None, description="异步批次 UUID"),
    target_date_from: date | None = Query(None),
    target_date_to: date | None = Query(None),
    session: AsyncSession = Depends(get_prediction_db_session),
) -> StoredPredictionResultListResponse:
    """分页查询已写入数据库的预测明细（含同步预测 batch_id 为空）。"""
    filters = []
    if warehouse and warehouse.strip():
        filters.append(PredictionResultRow.warehouse == warehouse.strip())
    if product_variety and product_variety.strip():
        filters.append(PredictionResultRow.product_variety == product_variety.strip())
    if regional_manager and regional_manager.strip():
        filters.append(PredictionResultRow.regional_manager == regional_manager.strip())
    if batch_id is not None:
        filters.append(PredictionResultRow.batch_id == str(batch_id))
    if target_date_from is not None:
        filters.append(PredictionResultRow.target_date >= target_date_from)
    if target_date_to is not None:
        filters.append(PredictionResultRow.target_date <= target_date_to)

    count_stmt = select(func.count()).select_from(PredictionResultRow)
    stmt = select(PredictionResultRow)
    for f in filters:
        count_stmt = count_stmt.where(f)
        stmt = stmt.where(f)

    try:
        total_res = await session.execute(count_stmt)
        total = int(total_res.scalar_one())
        offset = (page - 1) * page_size
        stmt = stmt.order_by(PredictionResultRow.created_at.desc(), PredictionResultRow.id.desc())
        stmt = stmt.offset(offset).limit(page_size)
        res = await session.execute(stmt)
        rows = res.scalars().all()
        items = [StoredPredictionResultItem.model_validate(r) for r in rows]
        return StoredPredictionResultListResponse(
            total=total, page=page, page_size=page_size, items=items
        )
    except BusinessException:
        raise
    except Exception as e:
        logger.exception("list_stored_prediction_results failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/batch/{predict_id}", response_model=BatchStatusResponse)
async def get_batch_status(
    predict_id: uuid.UUID,
    session: AsyncSession = Depends(get_prediction_db_session),
) -> BatchStatusResponse:
    predict_id_str = str(predict_id)
    stmt = select(PredictionBatch).where(PredictionBatch.id == predict_id_str)
    res = await session.execute(stmt)
    batch = res.scalar_one_or_none()
    if batch is None:
        raise HTTPException(status_code=404, detail="batch_not_found")
    cnt_stmt = select(func.count()).select_from(PredictionResultRow).where(
        PredictionResultRow.batch_id == predict_id_str
    )
    cnt_res = await session.execute(cnt_stmt)
    result_count = int(cnt_res.scalar_one())
    export_ready = bool(batch.export_file_path and Path(batch.export_file_path).is_file())
    return BatchStatusResponse(
        predict_id=predict_id,
        status=batch.status,
        celery_task_id=batch.celery_task_id,
        error_message=batch.error_message,
        created_at=batch.created_at,
        completed_at=batch.completed_at,
        result_count=result_count,
        export_ready=export_ready,
    )


@router.get("/batch/{predict_id}/download")
async def download_batch_excel(
    predict_id: uuid.UUID,
    session: AsyncSession = Depends(get_prediction_db_session),
):
    from fastapi.responses import FileResponse

    predict_id_str = str(predict_id)
    stmt = select(PredictionBatch).where(PredictionBatch.id == predict_id_str)
    res = await session.execute(stmt)
    batch = res.scalar_one_or_none()
    if batch is None:
        raise HTTPException(status_code=404, detail="batch_not_found")
    path = batch.export_file_path
    if not path or not Path(path).is_file():
        raise HTTPException(status_code=404, detail="export_not_ready")
    return FileResponse(
        path,
        filename=f"prediction_{predict_id_str}.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
