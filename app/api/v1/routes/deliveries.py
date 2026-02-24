"""
销售台账/报货订单路由
"""
import os
import shutil
from decimal import Decimal
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends, Query, Body
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.services.delivery_service import DeliveryService, get_delivery_service
from core.auth import get_current_user  # 添加认证依赖导入

router = APIRouter(prefix="/deliveries", tags=["销售台账/报货订单"])


# ============ 请求/响应模型 ============

class DeliveryCreateRequest(BaseModel):
    report_date: str = Field(..., description="报货日期")
    warehouse: Optional[str] = Field(None, description="送货库房")
    target_factory_id: Optional[int] = Field(None, description="目标工厂ID")
    target_factory_name: str = Field(..., description="目标工厂名称")
    product_name: str = Field(..., description="货物品种")
    quantity: float = Field(..., description="数量（吨）")
    vehicle_no: str = Field(..., description="车牌号")
    driver_name: str = Field(..., description="司机姓名")
    driver_phone: str = Field(..., description="司机电话")
    driver_id_card: Optional[str] = Field(None, description="身份证号")
    has_delivery_order: str = Field("无", description="是否有联单：有/无")
    payee: Optional[str] = Field(None, description="收款人")
    service_fee: float = Field(0, description="服务费")
    status: str = Field("待确认", description="状态")
    uploaded_by: Optional[str] = Field(None, description="上传者身份：司机/公司（用于判断来源）")


class DeliveryUpdateRequest(BaseModel):
    report_date: Optional[str] = None
    warehouse: Optional[str] = None
    target_factory_id: Optional[int] = None
    target_factory_name: Optional[str] = None
    product_name: Optional[str] = None
    quantity: Optional[float] = None
    vehicle_no: Optional[str] = None
    driver_name: Optional[str] = None
    driver_phone: Optional[str] = None
    driver_id_card: Optional[str] = None
    has_delivery_order: Optional[str] = None
    payee: Optional[str] = None
    service_fee: Optional[float] = None
    status: Optional[str] = None
    uploaded_by: Optional[str] = None


class DeliveryOut(BaseModel):
    id: int
    report_date: Optional[str] = None
    warehouse: Optional[str] = None
    target_factory_name: Optional[str] = None
    product_name: Optional[str] = None
    quantity: Optional[float] = None
    vehicle_no: Optional[str] = None
    driver_name: Optional[str] = None
    driver_phone: Optional[str] = None
    driver_id_card: Optional[str] = None
    has_delivery_order: Optional[str] = None
    delivery_order_image: Optional[str] = None
    delivery_order_upload_status: Optional[str] = None
    source_type: Optional[str] = None
    shipper: Optional[str] = None
    payee: Optional[str] = None
    service_fee: Optional[float] = None
    contract_no: Optional[str] = None
    contract_unit_price: Optional[float] = None
    total_amount: Optional[float] = None
    status: Optional[str] = None
    uploader_id: Optional[int] = None  # 新增
    uploader_name: Optional[str] = None  # 新增
    uploaded_at: Optional[str] = None  # 新增
    created_at: Optional[str] = None


# ============ 路由 ============

@router.post("/", response_model=dict)
async def create_delivery(
        report_date: str = Form(...),
        warehouse: Optional[str] = Form(None),
        target_factory_id: Optional[int] = Form(None),
        target_factory_name: str = Form(...),
        product_name: str = Form(...),
        quantity: float = Form(...),
        vehicle_no: str = Form(...),
        driver_name: str = Form(...),
        driver_phone: str = Form(...),
        driver_id_card: Optional[str] = Form(None),
        has_delivery_order: str = Form("无"),
        payee: Optional[str] = Form(None),
        service_fee: float = Form(0),
        status: str = Form("待确认"),
        uploaded_by: Optional[str] = Form(None),
        delivery_order_image: Optional[UploadFile] = File(None),
        service: DeliveryService = Depends(get_delivery_service),
        current_user: dict = Depends(get_current_user)  # 使用认证依赖获取当前用户
):
    """创建报货订单（支持上传联单图片）"""
    try:
        data = {
            "report_date": report_date,
            "warehouse": warehouse,
            "target_factory_id": target_factory_id,
            "target_factory_name": target_factory_name,
            "product_name": product_name,
            "quantity": quantity,
            "vehicle_no": vehicle_no,
            "driver_name": driver_name,
            "driver_phone": driver_phone,
            "driver_id_card": driver_id_card,
            "has_delivery_order": has_delivery_order,
            "payee": payee,
            "service_fee": service_fee,
            "status": status,
            "uploaded_by": uploaded_by,
        }

        image_bytes = None
        if delivery_order_image:
            image_bytes = await delivery_order_image.read()

        result = service.create_delivery(data, image_bytes, current_user)

        if result["success"]:
            return result
        else:
            raise HTTPException(status_code=400, detail=result.get("error"))

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/", response_model=dict)
async def list_deliveries(
    exact_factory_name: Optional[str] = Query(None, description="精确目标工厂"),
    exact_status: Optional[str] = Query(None, description="精确状态"),
    exact_vehicle_no: Optional[str] = Query(None, description="精确车牌号"),
    exact_driver_name: Optional[str] = Query(None, description="精确司机姓名"),
    exact_driver_phone: Optional[str] = Query(None, description="精确司机电话"),
    fuzzy_keywords: Optional[str] = Query(None, description="模糊关键词（空格分隔）"),
        date_from: Optional[str] = Query(None, description="开始日期"),
        date_to: Optional[str] = Query(None, description="结束日期"),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        service: DeliveryService = Depends(get_delivery_service)
):
    """查询报货订单列表"""
    return service.list_deliveries(
        exact_factory_name=exact_factory_name,
        exact_status=exact_status,
        exact_vehicle_no=exact_vehicle_no,
        exact_driver_name=exact_driver_name,
        exact_driver_phone=exact_driver_phone,
        fuzzy_keywords=fuzzy_keywords,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size
    )


@router.get("/{delivery_id}", response_model=DeliveryOut)
async def get_delivery(
        delivery_id: int,
        service: DeliveryService = Depends(get_delivery_service)
):
    """查看订单详情"""
    delivery = service.get_delivery(delivery_id)
    if not delivery:
        raise HTTPException(status_code=404, detail="订单不存在")
    return delivery


@router.put("/{delivery_id}", response_model=dict)
async def update_delivery(
        delivery_id: int,
        request: DeliveryUpdateRequest,
        service: DeliveryService = Depends(get_delivery_service),
        current_user: str = "admin"
):
    """
    编辑报货订单（纯JSON，不涉及文件上传）
    如需修改图片，请使用 /{delivery_id}/upload-order 接口
    """
    try:
        data = {k: v for k, v in request.dict().items() if v is not None}

        if not data:
            raise HTTPException(status_code=400, detail="没有要更新的字段")

        result = service.update_delivery(delivery_id, data, None, False)

        if result["success"]:
            return result
        else:
            raise HTTPException(status_code=400, detail=result.get("error"))

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{delivery_id}")
async def delete_delivery(
        delivery_id: int,
        service: DeliveryService = Depends(get_delivery_service)
):
    """删除订单"""
    result = service.delete_delivery(delivery_id)
    if result["success"]:
        return result
    else:
        raise HTTPException(status_code=400, detail=result.get("error"))


@router.post("/{delivery_id}/upload-order")
async def upload_delivery_order(
        delivery_id: int,
        image: UploadFile = File(..., description="联单图片"),
        has_delivery_order: Optional[str] = Form(None, description="同步修改联单状态：有/无"),
        uploaded_by: str = Form("公司"),
        service: DeliveryService = Depends(get_delivery_service)
):
    """
    单独上传/更新联单图片
    用于：后期补传、重新上传、修改状态
    """
    try:
        image_bytes = await image.read()

        data = {}
        if has_delivery_order:
            data['has_delivery_order'] = has_delivery_order
            data['uploaded_by'] = uploaded_by

        result = service.update_delivery(delivery_id, data, image_bytes)

        if result["success"]:
            return {"success": True, "message": "图片上传成功", "data": result["data"]}
        else:
            raise HTTPException(status_code=400, detail=result.get("error"))

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{delivery_id}/image")
async def delete_delivery_image(
    delivery_id: int,
    service: DeliveryService = Depends(get_delivery_service)
):
    """删除联单图片（保留有联单状态，但删除图片文件）"""
    result = service.update_delivery(delivery_id, {}, None, delete_image=True)
    if result["success"]:
        return {"success": True, "message": "图片已删除"}
    else:
        raise HTTPException(status_code=400, detail=result.get("error"))


@router.get("/{delivery_id}/image")
async def get_delivery_image(
    delivery_id: int,
    service: DeliveryService = Depends(get_delivery_service)
):
    """
    查看联单图片
    直接返回图片文件
    """
    try:
        delivery = service.get_delivery(delivery_id)
        if not delivery:
            raise HTTPException(status_code=404, detail="订单不存在")

        image_path = delivery.get("delivery_order_image")
        if not image_path:
            raise HTTPException(status_code=404, detail="该订单没有上传图片")

        if not os.path.exists(image_path):
            raise HTTPException(status_code=404, detail="图片文件不存在")

        return FileResponse(
            path=image_path,
            media_type="image/jpeg",
            filename=f"delivery_{delivery_id}.jpg"
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取图片失败: {str(e)}")