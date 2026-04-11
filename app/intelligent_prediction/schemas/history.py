"""历史数据 API 结构。"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DeliveryRecordRead(BaseModel):
    """单条送货记录（读取）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    regional_manager: str
    warehouse: str
    delivery_date: date
    product_variety: str
    weight: Decimal
    created_at: datetime


class HistoryListResponse(BaseModel):
    """分页列表。"""

    total: int
    page: int
    page_size: int
    items: list[DeliveryRecordRead]


class HistoryImportRowError(BaseModel):
    """导入错误行信息。"""

    row_index: int = Field(..., description="Excel 行号（1-based，含表头则为数据行）")
    field: Optional[str] = None
    message: str


class HistoryImportResponse(BaseModel):
    """导入结果。"""

    inserted: int
    skipped: int
    errors: list[HistoryImportRowError]


class HistoryBatchDeleteRequest(BaseModel):
    """批量删除请求。"""

    ids: list[int] = Field(..., min_length=1, max_length=2000)

    @field_validator("ids")
    @classmethod
    def unique_ids(cls, v: list[int]) -> list[int]:
        """去重。"""
        return list(dict.fromkeys(v))


class HistoryQueryParams(BaseModel):
    """查询参数（由服务层组装）。"""

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=200)
    regional_manager: Optional[str] = None
    warehouse: Optional[str] = None
    product_variety: Optional[str] = None
    regional_managers: list[str] = Field(default_factory=list)
    warehouses: list[str] = Field(default_factory=list)
    product_varieties: list[str] = Field(default_factory=list)
    date_from: Optional[date] = None
    date_to: Optional[date] = None

    @field_validator("regional_manager", "warehouse", "product_variety", mode="before")
    @classmethod
    def empty_to_none(cls, v: Any) -> Any:
        """空字符串视为未筛选。"""
        if v is None:
            return None
        if isinstance(v, str) and not v.strip():
            return None
        return v.strip() if isinstance(v, str) else v

    @field_validator("regional_managers", "warehouses", "product_varieties", mode="before")
    @classmethod
    def normalize_str_lists(cls, v: Any) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            parts = [p.strip() for p in v.split(",") if p.strip()]
            return parts
        if isinstance(v, (list, tuple)):
            out: list[str] = []
            for x in v:
                if x is None:
                    continue
                s = str(x).strip()
                if s:
                    out.append(s)
            return out
        return []
