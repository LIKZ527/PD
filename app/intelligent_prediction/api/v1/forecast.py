"""PRD 规则预测：图表、明细分页、导出。"""

from __future__ import annotations

import io
from datetime import date, datetime, timedelta

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessException, ValidationBusinessException
from app.core.logging import get_logger
from app.intelligent_prediction.api.audit_deps import AuditActor, get_audit_actor
from app.intelligent_prediction.api.deps import get_prediction_db_session
from app.intelligent_prediction.schemas.forecast import PrdForecastChartResponse, PrdForecastDetailResponse, PrdForecastQuery
from app.intelligent_prediction.services.audit_service import append_audit, write_audit_standalone
from app.intelligent_prediction.services.prd_forecast_service import PrdForecastService, get_prd_forecast_service

logger = get_logger(__name__)
router = APIRouter()


def _merge_list(primary: list[str], legacy: str | None) -> list[str]:
    out = [x.strip() for x in primary if x and str(x).strip()]
    if legacy and legacy.strip() and legacy.strip() not in out:
        out.append(legacy.strip())
    return out


def _prd_query(
    *,
    date_from: date | None,
    date_to: date | None,
    regional_managers: list[str],
    regional_manager: str | None,
    warehouses: list[str],
    warehouse: str | None,
    product_varieties: list[str],
    product_variety: str | None,
    page: int,
    page_size: int,
) -> PrdForecastQuery:
    today = date.today()
    df = date_from or today
    dt = date_to or (today + timedelta(days=14))
    if df > dt:
        df, dt = dt, df
    return PrdForecastQuery(
        date_from=df,
        date_to=dt,
        regional_managers=_merge_list(regional_managers, regional_manager),
        warehouses=_merge_list(warehouses, warehouse),
        product_varieties=_merge_list(product_varieties, product_variety),
        page=page,
        page_size=page_size,
    )


@router.get("/prd/chart", response_model=PrdForecastChartResponse)
async def prd_forecast_chart(
    date_from: date | None = Query(None, description="预测区间起点，默认当天"),
    date_to: date | None = Query(None, description="预测区间终点，默认当天+14"),
    regional_manager: str | None = Query(None),
    regional_managers: list[str] = Query(default=[]),
    warehouse: str | None = Query(None),
    warehouses: list[str] = Query(default=[]),
    product_variety: str | None = Query(None),
    product_varieties: list[str] = Query(default=[]),
    session: AsyncSession = Depends(get_prediction_db_session),
    svc: PrdForecastService = Depends(get_prd_forecast_service),
) -> PrdForecastChartResponse:
    q = _prd_query(
        date_from=date_from,
        date_to=date_to,
        regional_managers=regional_managers,
        regional_manager=regional_manager,
        warehouses=warehouses,
        warehouse=warehouse,
        product_varieties=product_varieties,
        product_variety=product_variety,
        page=1,
        page_size=1,
    )
    try:
        return await svc.chart_only(session, q)
    except BusinessException:
        raise
    except Exception as e:
        logger.exception("prd_forecast_chart failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/prd/detail", response_model=PrdForecastDetailResponse)
async def prd_forecast_detail(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    regional_manager: str | None = Query(None),
    regional_managers: list[str] = Query(default=[]),
    warehouse: str | None = Query(None),
    warehouses: list[str] = Query(default=[]),
    product_variety: str | None = Query(None),
    product_varieties: list[str] = Query(default=[]),
    session: AsyncSession = Depends(get_prediction_db_session),
    svc: PrdForecastService = Depends(get_prd_forecast_service),
) -> PrdForecastDetailResponse:
    q = _prd_query(
        date_from=date_from,
        date_to=date_to,
        regional_managers=regional_managers,
        regional_manager=regional_manager,
        warehouses=warehouses,
        warehouse=warehouse,
        product_varieties=product_varieties,
        product_variety=product_variety,
        page=page,
        page_size=page_size,
    )
    try:
        return await svc.detail_page(session, q)
    except BusinessException:
        raise
    except Exception as e:
        logger.exception("prd_forecast_detail failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/prd/export")
async def prd_forecast_export(
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    regional_manager: str | None = Query(None),
    regional_managers: list[str] = Query(default=[]),
    warehouse: str | None = Query(None),
    warehouses: list[str] = Query(default=[]),
    product_variety: str | None = Query(None),
    product_varieties: list[str] = Query(default=[]),
    session: AsyncSession = Depends(get_prediction_db_session),
    svc: PrdForecastService = Depends(get_prd_forecast_service),
    actor: AuditActor = Depends(get_audit_actor),
) -> StreamingResponse:
    q = _prd_query(
        date_from=date_from,
        date_to=date_to,
        regional_managers=regional_managers,
        regional_manager=regional_manager,
        warehouses=warehouses,
        warehouse=warehouse,
        product_varieties=product_varieties,
        product_variety=product_variety,
        page=1,
        page_size=10**9,
    )
    try:
        rows, _chart = await svc.compute(session, q)
        fn = f"送货量预测_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        df = pd.DataFrame([r.model_dump() for r in rows])
        buf = io.BytesIO()
        df.to_excel(buf, index=False, engine="openpyxl")
        buf.seek(0)
        await append_audit(
            session,
            "prd_forecast_export",
            resource=fn,
            detail={"rows": len(rows), "date_from": str(q.date_from), "date_to": str(q.date_to)},
            actor=actor,
        )
        headers = {"Content-Disposition": f'attachment; filename="{fn}"'}
        return StreamingResponse(
            buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers=headers,
        )
    except BusinessException:
        raise
    except Exception as e:
        logger.exception("prd_forecast_export failed")
        await write_audit_standalone(
            "prd_forecast_export_failed",
            detail={"error": str(e)},
            actor=actor,
        )
        raise HTTPException(status_code=500, detail=str(e)) from e
