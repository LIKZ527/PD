"""历史送货：Excel 导入、分页、批量删除。"""

from __future__ import annotations

import io
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import pandas as pd
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationBusinessException
from app.core.logging import get_logger
from app.intelligent_prediction.models import DeliveryRecord
from app.intelligent_prediction.schemas.history import (
    DeliveryRecordRead,
    HistoryImportResponse,
    HistoryImportRowError,
    HistoryListResponse,
    HistoryQueryParams,
)

logger = get_logger(__name__)


class HistoryService:
    """历史记录业务逻辑。"""

    REQUIRED_COLUMNS_CANONICAL: dict[str, str] = {
        "大区经理": "regional_manager",
        "仓库": "warehouse",
        "送货日期": "delivery_date",
        "品种": "product_variety",
        "重量": "weight",
    }

    ALIAS_TO_CANONICAL: dict[str, str] = {
        "大区经理": "大区经理",
        "大區經理": "大区经理",
        "Regional Manager": "大区经理",
        "regional_manager": "大区经理",
        "仓库": "仓库",
        "倉庫": "仓库",
        "Warehouse": "仓库",
        "warehouse": "仓库",
        "送货日期": "送货日期",
        "送貨日期": "送货日期",
        "Delivery Date": "送货日期",
        "delivery_date": "送货日期",
        "品种": "品种",
        "品種": "品种",
        "Product Variety": "品种",
        "product_variety": "品种",
        "重量": "重量",
        "Weight": "重量",
        "weight": "重量",
    }

    def _normalize_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        rename_map: dict[str, str] = {}
        for c in df.columns:
            key = str(c).strip()
            if key in self.ALIAS_TO_CANONICAL:
                rename_map[c] = self.ALIAS_TO_CANONICAL[key]
        return df.rename(columns=rename_map)

    def _validate_headers(self, df: pd.DataFrame) -> None:
        cols = {str(c).strip() for c in df.columns}
        needed = set(self.REQUIRED_COLUMNS_CANONICAL.keys())
        if cols != needed:
            missing = needed - cols
            extra = cols - needed
            raise ValidationBusinessException(
                "Excel 表头不符合要求",
                details={
                    "required": sorted(needed),
                    "missing": sorted(missing),
                    "extra": sorted(extra),
                },
            )

    def _parse_date_cell(self, value: Any) -> tuple[date | None, str | None]:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None, "empty_date"
        if isinstance(value, datetime):
            return value.date(), None
        if isinstance(value, date):
            return value, None
        s = str(value).strip()
        if not s:
            return None, "empty_date"
        parsed = pd.to_datetime(s, errors="coerce", dayfirst=False)
        if pd.isna(parsed):
            return None, f"unrecognized_date:{s}"
        ts = parsed.to_pydatetime()
        return ts.date(), None

    def _parse_weight_cell(self, value: Any) -> tuple[Decimal | None, str | None]:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None, "empty_weight"
        if isinstance(value, (int, float)):
            return Decimal(str(value)), None
        s = str(value).strip().replace(",", "")
        if s == "":
            return None, "empty_weight"
        try:
            return Decimal(s), None
        except InvalidOperation:
            return None, f"non_numeric_weight:{s[:80]}"

    async def import_excel(
        self,
        session: AsyncSession,
        file_bytes: bytes,
        filename: str,
    ) -> HistoryImportResponse:
        _ = filename
        try:
            df = pd.read_excel(io.BytesIO(file_bytes), engine="openpyxl")
        except Exception as e:
            logger.exception("excel read failed")
            raise ValidationBusinessException(f"无法读取 Excel：{e}") from e

        df = self._normalize_columns(df)
        self._validate_headers(df)

        errors: list[HistoryImportRowError] = []
        to_insert: list[DeliveryRecord] = []

        for idx, row in df.iterrows():
            excel_row = int(idx) + 2
            rm = row.get("大区经理")
            wh = row.get("仓库")
            dv = row.get("送货日期")
            variety = row.get("品种")
            wv = row.get("重量")

            row_errors: list[str] = []
            if rm is None or str(rm).strip() == "":
                row_errors.append("大区经理必填")
            if wh is None or str(wh).strip() == "":
                row_errors.append("仓库必填")
            if variety is None or str(variety).strip() == "":
                row_errors.append("品种必填")

            d, de = self._parse_date_cell(dv)
            if de:
                row_errors.append(f"日期:{de}")

            w, we = self._parse_weight_cell(wv)
            if we:
                row_errors.append(f"重量:{we}")
            if w is not None and w < 0:
                row_errors.append("重量不可为负")

            if row_errors:
                errors.append(
                    HistoryImportRowError(
                        row_index=excel_row,
                        field="row",
                        message="; ".join(row_errors),
                    )
                )
                continue

            assert d is not None and w is not None
            to_insert.append(
                DeliveryRecord(
                    regional_manager=str(rm).strip(),
                    warehouse=str(wh).strip(),
                    delivery_date=d,
                    product_variety=str(variety).strip(),
                    weight=w,
                )
            )

        if errors:
            raise ValidationBusinessException(
                "导入失败：存在错误行，已整批拒绝",
                details={"errors": [e.model_dump() for e in errors]},
            )

        for rec in to_insert:
            session.add(rec)

        inserted = len(to_insert)
        logger.info("history import finished inserted=%s", inserted)
        return HistoryImportResponse(inserted=inserted, skipped=0, errors=[])

    async def list_records(
        self,
        session: AsyncSession,
        q: HistoryQueryParams,
    ) -> HistoryListResponse:
        stmt = select(DeliveryRecord)
        count_stmt = select(func.count()).select_from(DeliveryRecord)

        rms = list(q.regional_managers)
        if not rms and q.regional_manager:
            rms = [q.regional_manager]
        if rms:
            stmt = stmt.where(DeliveryRecord.regional_manager.in_(rms))
            count_stmt = count_stmt.where(DeliveryRecord.regional_manager.in_(rms))

        whs = list(q.warehouses)
        if not whs and q.warehouse:
            whs = [q.warehouse]
        if whs:
            stmt = stmt.where(DeliveryRecord.warehouse.in_(whs))
            count_stmt = count_stmt.where(DeliveryRecord.warehouse.in_(whs))

        vars_ = list(q.product_varieties)
        if not vars_ and q.product_variety:
            vars_ = [q.product_variety]
        if vars_:
            stmt = stmt.where(DeliveryRecord.product_variety.in_(vars_))
            count_stmt = count_stmt.where(DeliveryRecord.product_variety.in_(vars_))
        if q.date_from:
            stmt = stmt.where(DeliveryRecord.delivery_date >= q.date_from)
            count_stmt = count_stmt.where(DeliveryRecord.delivery_date >= q.date_from)
        if q.date_to:
            stmt = stmt.where(DeliveryRecord.delivery_date <= q.date_to)
            count_stmt = count_stmt.where(DeliveryRecord.delivery_date <= q.date_to)

        total_res = await session.execute(count_stmt)
        total = int(total_res.scalar_one())

        stmt = stmt.order_by(DeliveryRecord.delivery_date.desc(), DeliveryRecord.id.desc())
        offset = (q.page - 1) * q.page_size
        stmt = stmt.offset(offset).limit(q.page_size)
        res = await session.execute(stmt)
        rows = res.scalars().all()
        items = [DeliveryRecordRead.model_validate(r, from_attributes=True) for r in rows]
        return HistoryListResponse(total=total, page=q.page, page_size=q.page_size, items=items)

    async def batch_delete(self, session: AsyncSession, ids: list[int]) -> int:
        if not ids:
            return 0
        stmt = delete(DeliveryRecord).where(DeliveryRecord.id.in_(ids))
        res = await session.execute(stmt)
        return int(res.rowcount or 0)


def get_history_service() -> HistoryService:
    return HistoryService()
