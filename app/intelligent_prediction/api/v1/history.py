"""送货历史 HTTP 接口。"""

from __future__ import annotations

import io
from datetime import date
from typing import Optional

import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessException, ValidationBusinessException
from app.core.logging import get_logger
from app.intelligent_prediction.api.audit_deps import AuditActor, get_audit_actor
from app.intelligent_prediction.api.deps import get_history_service_dep, get_prediction_db_session
from app.intelligent_prediction.schemas.history import (
    HistoryBatchDeleteRequest,
    HistoryImportResponse,
    HistoryListResponse,
    HistoryQueryParams,
)
from app.intelligent_prediction.services.audit_service import append_audit, write_audit_standalone
from app.intelligent_prediction.services.history_service import HistoryService

logger = get_logger(__name__)
router = APIRouter()


@router.get("/template")
async def download_history_template() -> StreamingResponse:
    """标准导入模板（表头与 PRD 一致）。"""
    cols = list(HistoryService.REQUIRED_COLUMNS_CANONICAL.keys())
    df = pd.DataFrame(columns=cols)
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": (
                'attachment; filename="delivery_history_import_template.xlsx"; '
                "filename*=UTF-8''%E9%80%81%E8%B4%A7%E5%8E%86%E5%8F%B2%E5%AF%BC%E5%85%A5%E6%A8%A1%E6%9D%BF.xlsx"
            )
        },
    )


@router.post("/import", response_model=HistoryImportResponse)
async def import_history_excel(
    request: Request,
    file: UploadFile = File(..., description="Excel 文件（.xlsx）"),
    session: AsyncSession = Depends(get_prediction_db_session),
    svc: HistoryService = Depends(get_history_service_dep),
    actor: AuditActor = Depends(get_audit_actor),
) -> HistoryImportResponse:
    fn = file.filename or "upload.xlsx"
    try:
        raw = await file.read()
        result = await svc.import_excel(session, raw, fn)
        await append_audit(
            session,
            "history_import",
            resource=fn,
            detail={"inserted": result.inserted},
            actor=actor,
        )
        return result
    except ValidationBusinessException as e:
        await write_audit_standalone(
            "history_import_failed",
            resource=fn,
            detail={"message": e.message, **(e.details or {})},
            actor=actor,
        )
        raise
    except BusinessException:
        raise
    except Exception as e:
        logger.exception("import failed")
        await write_audit_standalone(
            "history_import_failed",
            resource=fn,
            detail={"error": str(e)},
            actor=actor,
        )
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("", response_model=HistoryListResponse)
async def list_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    regional_manager: Optional[str] = Query(None),
    regional_managers: list[str] = Query(default=[]),
    warehouse: Optional[str] = Query(None),
    warehouses: list[str] = Query(default=[]),
    product_variety: Optional[str] = Query(None),
    product_varieties: list[str] = Query(default=[]),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    session: AsyncSession = Depends(get_prediction_db_session),
    svc: HistoryService = Depends(get_history_service_dep),
) -> HistoryListResponse:
    q = HistoryQueryParams(
        page=page,
        page_size=page_size,
        regional_manager=regional_manager,
        warehouse=warehouse,
        product_variety=product_variety,
        regional_managers=regional_managers,
        warehouses=warehouses,
        product_varieties=product_varieties,
        date_from=date_from,
        date_to=date_to,
    )
    try:
        return await svc.list_records(session, q)
    except BusinessException:
        raise
    except Exception as e:
        logger.exception("list_history failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/batch")
async def batch_delete_history(
    body: HistoryBatchDeleteRequest,
    session: AsyncSession = Depends(get_prediction_db_session),
    svc: HistoryService = Depends(get_history_service_dep),
    actor: AuditActor = Depends(get_audit_actor),
) -> dict[str, int]:
    try:
        deleted = await svc.batch_delete(session, body.ids)
        await append_audit(
            session,
            "history_batch_delete",
            detail={"deleted": deleted, "ids_sample": body.ids[:50]},
            actor=actor,
        )
        return {"deleted": deleted}
    except BusinessException:
        raise
    except Exception as e:
        logger.exception("batch_delete failed")
        raise HTTPException(status_code=500, detail=str(e)) from e
