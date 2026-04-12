"""
TL比价模块路由
接口前缀：/tl
"""
import io
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import StreamingResponse

from app.models.tl import (
    ComparisonRequest,
    UploadFreightRequest,
    UpdateFreightRequest,
    CategoryMappingItem,
    ConfirmPriceTableRequest,
    AddWarehouseRequest,
    UpdateWarehouseRequest,
    AddSmelterRequest,
    UploadVarietyRequest,
    UpdateSmelterRequest,
    PurchaseSuggestionRequest,
    TaxRateUpsertRequest,
)
from app.services.tl_service import PurchaseSuggestionLLMError, TLService, get_tl_service

router = APIRouter(prefix="/tl", tags=["TL比价模块"])


def _merge_quote_list_filters(
    date_from: Optional[str],
    date_to: Optional[str],
    start_date: Optional[str],
    end_date: Optional[str],
    category_name: Optional[str],
    variety: Optional[str],
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """与「查询条件」对齐：start_date/end_date 同 date_from/date_to；variety 优先于 category_name。"""
    d_from = date_from or start_date
    d_to = date_to or end_date
    cat: Optional[str] = None
    if variety is not None and str(variety).strip():
        cat = str(variety).strip()
    elif category_name is not None and str(category_name).strip():
        cat = str(category_name).strip()
    return d_from, d_to, cat


@router.post("/add_warehouse", summary="添加仓库")
def add_warehouse(
    body: AddWarehouseRequest,
    service: TLService = Depends(get_tl_service),
):
    try:
        return service.add_warehouse(name=body.仓库名)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get_warehouses", summary="获取仓库列表")
def get_warehouses(service: TLService = Depends(get_tl_service)):
    try:
        data = service.get_warehouses()
        return {"code": 200, "data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/update_warehouse", summary="修改仓库信息")
def update_warehouse(
    body: UpdateWarehouseRequest,
    service: TLService = Depends(get_tl_service),
):
    try:
        return service.update_warehouse(
            warehouse_id=body.仓库id,
            name=body.仓库名,
            is_active=body.is_active,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/delete_warehouse", summary="删除仓库（软删除）")
def delete_warehouse(
    warehouse_id: int,
    service: TLService = Depends(get_tl_service),
):
    try:
        return service.delete_warehouse(warehouse_id=warehouse_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/add_smelter", summary="新建冶炼厂")
def add_smelter(
    body: AddSmelterRequest,
    service: TLService = Depends(get_tl_service),
):
    try:
        return service.add_smelter(name=body.冶炼厂名)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get_smelters", summary="获取冶炼厂列表")
def get_smelters(service: TLService = Depends(get_tl_service)):
    try:
        data = service.get_smelters()
        return {"code": 200, "data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/update_smelter", summary="修改冶炼厂信息")
def update_smelter(
    body: UpdateSmelterRequest,
    service: TLService = Depends(get_tl_service),
):
    try:
        return service.update_smelter(
            smelter_id=body.冶炼厂id,
            name=body.冶炼厂名,
            is_active=body.is_active,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/delete_smelter", summary="删除冶炼厂（软删除）")
def delete_smelter(
    smelter_id: int,
    service: TLService = Depends(get_tl_service),
):
    try:
        return service.delete_smelter(smelter_id=smelter_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get_categories", summary="获取品类列表")
def get_categories(service: TLService = Depends(get_tl_service)):
    try:
        data = service.get_categories()
        return {"code": 200, "data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/upload_variety", summary="上传品种")
def upload_variety(
    body: List[UploadVarietyRequest],
    service: TLService = Depends(get_tl_service),
):
    try:
        items = [item.model_dump() for item in body]
        return service.upload_variety(items)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/get_comparison", summary="获取比价表")
def get_comparison(
    body: ComparisonRequest,
    service: TLService = Depends(get_tl_service),
):
    try:
        out = service.get_comparison(
            warehouse_ids=body.选中仓库id列表,
            smelter_ids=body.冶炼厂id列表,
            category_ids=body.品类id列表,
            price_type=body.price_type,
            tons=body.吨数,
            tons_per_truck=body.每车吨数,
            optimal_basis_list=body.最优价计税口径列表,
            optimal_sort_basis=body.最优价排序口径,
        )
        return {
            "code": 200,
            "data": out["明细"],
            "冶炼厂利润排行": out["冶炼厂利润排行"],
            "最优价排序口径": out["最优价排序口径"],
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/upload_price_table", summary="上传价格表")
def upload_price_table(
    file: List[UploadFile] = File(..., description="价格表图片，支持批量上传"),
    service: TLService = Depends(get_tl_service),
):
    allowed_types = {"image/jpeg", "image/jpg", "image/png", "image/bmp", "image/webp"}
    for f in file:
        if f.content_type not in allowed_types:
            raise HTTPException(
                status_code=400,
                detail=f"文件 '{f.filename}' 格式不支持，仅允许 jpg/png/bmp/webp",
            )
    try:
        return service.upload_price_table(file)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/confirm_price_table", summary="确认并写入报价数据")
def confirm_price_table(
    body: ConfirmPriceTableRequest,
    service: TLService = Depends(get_tl_service),
):
    try:
        items = [item.model_dump() for item in body.数据]
        full_data = body.full_data.model_dump() if body.full_data else None
        return service.confirm_price_table(
            quote_date_str=body.报价日期,
            items=items,
            full_data=full_data,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get_quote_details_list", summary="报价数据列表")
def get_quote_details_list(
    factory_id: Optional[int] = None,
    quote_date: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    category_name: Optional[str] = None,
    variety: Optional[str] = None,
    category_exact: bool = Query(
        False,
        description="品种为下拉精确选中时传 true；false 为模糊匹配（默认）",
    ),
    page: int = 1,
    page_size: int = 50,
    response_format: str = Query(
        "full",
        description='返回字段：`full`=库表全量列；`table`=与「报价数据列表」页表格列一致',
    ),
    service: TLService = Depends(get_tl_service),
):
    eff_from, eff_to, eff_cat = _merge_quote_list_filters(
        date_from, date_to, start_date, end_date, category_name, variety
    )
    try:
        return service.get_quote_details_list(
            factory_id=factory_id,
            quote_date=quote_date,
            date_from=eff_from,
            date_to=eff_to,
            category_name=eff_cat,
            category_exact=category_exact,
            page=page,
            page_size=page_size,
            response_format=response_format,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/export_quote_details_excel", summary="导出报价数据 Excel")
def export_quote_details_excel(
    factory_id: Optional[int] = None,
    quote_date: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    category_name: Optional[str] = None,
    variety: Optional[str] = None,
    category_exact: bool = Query(
        False,
        description="与列表接口一致：下拉选品种建议 true",
    ),
    service: TLService = Depends(get_tl_service),
):
    eff_from, eff_to, eff_cat = _merge_quote_list_filters(
        date_from, date_to, start_date, end_date, category_name, variety
    )
    try:
        data = service.export_quote_details_excel(
            factory_id=factory_id,
            quote_date=quote_date,
            date_from=eff_from,
            date_to=eff_to,
            category_name=eff_cat,
            category_exact=category_exact,
        )
        filename = "报价数据导出.xlsx"
        return StreamingResponse(
            io.BytesIO(data),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            },
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/upload_freight", summary="上传运费")
def upload_freight(
    body: List[UploadFreightRequest],
    service: TLService = Depends(get_tl_service),
):
    try:
        freight_list = [item.model_dump() for item in body]
        return service.upload_freight(freight_list)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get_freight_list", summary="运费列表")
def get_freight_list(
    warehouse_id: Optional[int] = None,
    factory_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    service: TLService = Depends(get_tl_service),
):
    try:
        return service.get_freight_list(
            warehouse_id=warehouse_id,
            factory_id=factory_id,
            date_from=date_from,
            date_to=date_to,
            page=page,
            page_size=page_size,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/update_freight", summary="编辑运费")
def update_freight(
    body: UpdateFreightRequest,
    service: TLService = Depends(get_tl_service),
):
    try:
        return service.update_freight(
            freight_id=body.运费id,
            price_per_ton=body.运费,
            effective_date_str=body.生效日期,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get_category_mapping", summary="获取品类映射表")
def get_category_mapping(service: TLService = Depends(get_tl_service)):
    try:
        data = service.get_category_mapping()
        return {"code": 200, "data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/get_purchase_suggestion", summary="采购建议")
def get_purchase_suggestion(
    body: PurchaseSuggestionRequest,
    service: TLService = Depends(get_tl_service),
):
    try:
        demands = [d.model_dump() for d in body.demands]
        return service.get_purchase_suggestion(
            warehouse_ids=body.warehouse_ids,
            demands=demands,
            price_type=body.price_type,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except PurchaseSuggestionLLMError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get_tax_rates", summary="获取税率表")
def get_tax_rates(
    factory_ids: Optional[str] = None,
    service: TLService = Depends(get_tl_service),
):
    try:
        ids = [int(x) for x in factory_ids.split(",")] if factory_ids else None
        data = service.get_tax_rates(factory_ids=ids)
        return {"code": 200, "data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/upsert_tax_rates", summary="批量设置税率")
def upsert_tax_rates(
    body: TaxRateUpsertRequest,
    service: TLService = Depends(get_tl_service),
):
    try:
        items = [item.model_dump() for item in body.items]
        return service.upsert_tax_rates(items)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/delete_tax_rate", summary="删除某冶炼厂的某税率记录")
def delete_tax_rate(
    factory_id: int,
    tax_type: str,
    service: TLService = Depends(get_tl_service),
):
    try:
        return service.delete_tax_rate(factory_id=factory_id, tax_type=tax_type)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/update_category_mapping", summary="更新品类映射表")
def update_category_mapping(
    body: List[CategoryMappingItem],
    service: TLService = Depends(get_tl_service),
):
    try:
        last_cid: Optional[int] = None
        for item in body:
            r = service.update_category_mapping(
                category_id=item.品类id,
                names=item.品类名称,
            )
            last_cid = r.get("品类id")
        out: Dict[str, Any] = {"code": 200, "msg": "品类映射表更新成功，数据已存入数据库"}
        if last_cid is not None:
            out["品类id"] = last_cid
        return out
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
