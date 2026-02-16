"""
磅单管理路由 - OCR识别 + 自动关联 + 手动修正
"""
import os
import re
import shutil
from pathlib import Path
from typing import Optional, List

from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, Query, Body, Form
from pydantic import BaseModel, Field

from app.services.weighbill_service import WeighbillService, get_weighbill_service

router = APIRouter(prefix="/weighbills", tags=["磅单管理"])


# ============ 请求/响应模型 ============

class WeighbillOCRResponse(BaseModel):
    # OCR识别原始字段
    weigh_date: Optional[str] = None
    weigh_ticket_no: Optional[str] = None
    contract_no: Optional[str] = None
    vehicle_no: Optional[str] = None
    product_name: Optional[str] = None
    gross_weight: Optional[float] = None
    tare_weight: Optional[float] = None
    net_weight: Optional[float] = None
    delivery_unit: Optional[str] = None
    receive_unit: Optional[str] = None
    ocr_message: str = ""

    # 自动关联填充字段
    matched_delivery_id: Optional[int] = None
    warehouse: Optional[str] = None
    target_factory_name: Optional[str] = None
    delivery_time: Optional[str] = None
    driver_name: Optional[str] = None
    driver_phone: Optional[str] = None
    driver_id_card: Optional[str] = None
    match_message: Optional[str] = None

    # 合同价格
    unit_price: Optional[float] = None
    total_amount: Optional[float] = None
    price_message: Optional[str] = None

    # 状态
    ocr_success: bool = True
    raw_text: Optional[str] = None
    ocr_time: float = 0


class WeighbillCreateRequest(BaseModel):
    # 基础信息（可从OCR带过来，也可手动填写）
    weigh_date: str = Field(..., description="磅单日期")
    weigh_ticket_no: Optional[str] = Field(None, description="过磅单号")
    contract_no: Optional[str] = Field(None, description="合同编号")
    vehicle_no: str = Field(..., description="车牌号")
    product_name: Optional[str] = Field(None, description="货物名称")
    gross_weight: Optional[float] = Field(None, description="毛重")
    tare_weight: Optional[float] = Field(None, description="皮重")
    net_weight: float = Field(..., description="净重")

    # 关联信息（自动匹配或手动填写）
    matched_delivery_id: Optional[int] = Field(None, description="关联的报货订单ID")
    warehouse: Optional[str] = Field(None, description="送货库房")
    target_factory_name: Optional[str] = Field(None, description="目标工厂")
    delivery_time: Optional[str] = Field(None, description="送货时间")
    driver_name: Optional[str] = Field(None, description="司机姓名")
    driver_phone: Optional[str] = Field(None, description="司机电话")
    driver_id_card: Optional[str] = Field(None, description="身份证号")

    # 价格信息
    unit_price: Optional[float] = Field(None, description="合同单价")
    total_amount: Optional[float] = Field(None, description="总价")


class WeighbillUpdateRequest(BaseModel):
    # 所有字段都可修改
    weigh_date: Optional[str] = None
    weigh_ticket_no: Optional[str] = None
    contract_no: Optional[str] = None
    vehicle_no: Optional[str] = None
    product_name: Optional[str] = None
    gross_weight: Optional[float] = None
    tare_weight: Optional[float] = None
    net_weight: Optional[float] = None
    matched_delivery_id: Optional[int] = None
    warehouse: Optional[str] = None
    target_factory_name: Optional[str] = None
    delivery_time: Optional[str] = None
    driver_name: Optional[str] = None
    driver_phone: Optional[str] = None
    driver_id_card: Optional[str] = None
    unit_price: Optional[float] = None
    total_amount: Optional[float] = None
    ocr_status: Optional[str] = None


class WeighbillOut(BaseModel):
    id: int
    weigh_date: Optional[str] = None
    weigh_ticket_no: Optional[str] = None
    contract_no: Optional[str] = None
    vehicle_no: Optional[str] = None
    product_name: Optional[str] = None
    gross_weight: Optional[float] = None
    tare_weight: Optional[float] = None
    net_weight: Optional[float] = None
    unit_price: Optional[float] = None
    total_amount: Optional[float] = None
    weighbill_image: Optional[str] = None
    ocr_status: str = "待确认"
    is_manual_corrected: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


# ============ 路由 ============

@router.post("/ocr", response_model=WeighbillOCRResponse)
async def ocr_weighbill(
        file: UploadFile = File(..., description="磅单图片"),
        auto_match: bool = Query(True, description="是否自动关联匹配"),
        service: WeighbillService = Depends(get_weighbill_service)
):
    """
    上传磅单图片进行OCR识别

    流程：
    1. OCR识别磅单关键信息
    2. 通过日期+车牌号匹配报货订单
    3. 通过合同编号获取合同单价
    4. 自动计算总价
    5. 返回完整数据供用户确认/修正
    """
    allowed_types = ["image/jpeg", "image/jpg", "image/png", "image/bmp"]
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail="仅支持jpg/png/bmp格式")

    temp_path = Path("uploads/temp") / f"weighbill_{os.urandom(4).hex()}.jpg"
    temp_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        # 保存临时文件
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # 预处理
        processed_path = service.preprocess_image(str(temp_path))

        # OCR识别
        result = service.recognize_weighbill(processed_path)

        # 清理临时文件
        if processed_path != str(temp_path) and os.path.exists(processed_path):
            os.remove(processed_path)
        os.remove(temp_path)

        if not result["success"]:
            raise HTTPException(status_code=400, detail=result.get("error", "识别失败"))

        ocr_data = result["data"]

        # 自动关联匹配
        if auto_match:
            ocr_data = service.auto_fill_data(ocr_data)

        return WeighbillOCRResponse(**ocr_data)

    except HTTPException:
        raise
    except Exception as e:
        if temp_path.exists():
            os.remove(temp_path)
        raise HTTPException(status_code=500, detail=f"处理失败: {str(e)}")


@router.post("/", response_model=dict)
async def create_weighbill(
        request: WeighbillCreateRequest = Body(...),
        weighbill_image: Optional[UploadFile] = File(None, description="磅单图片（可选，OCR时已传则不用）"),
        is_manual: bool = Form(True, description="是否人工录入/修正"),
        service: WeighbillService = Depends(get_weighbill_service)
):
    """
    保存磅单（OCR后确认保存，或纯手动录入）

    - OCR识别后：用户确认无误，提交保存
    - 纯手动：直接填写所有字段保存
    """
    try:
        data = request.dict()

        # 如果没有总价但有单价和净重，自动计算
        if not data.get("total_amount") and data.get("unit_price") and data.get("net_weight"):
            data["total_amount"] = round(data["unit_price"] * data["net_weight"], 2)

        # 处理图片
        image_path = None
        if weighbill_image:
            file_ext = Path(weighbill_image.filename).suffix.lower() or ".jpg"
            safe_name = re.sub(r'[^\w\-]', '_', data.get("vehicle_no", "unknown"))
            filename = f"weighbill_{safe_name}_{data.get('weigh_date', 'unknown')}{file_ext}"
            file_path = Path("uploads/weighbills") / filename

            # 确保目录存在
            file_path.parent.mkdir(parents=True, exist_ok=True)

            # 保存图片
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(weighbill_image.file, buffer)

            image_path = str(file_path)

        # 保存到数据库
        result = service.create_weighbill(data, image_path, is_manual)

        if result["success"]:
            return result
        else:
            raise HTTPException(status_code=400, detail=result.get("error"))

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{bill_id}", response_model=WeighbillOut)
async def get_weighbill(
        bill_id: int,
        service: WeighbillService = Depends(get_weighbill_service)
):
    """查看磅单详情"""
    bill = service.get_weighbill(bill_id)
    if not bill:
        raise HTTPException(status_code=404, detail="磅单不存在")
    return bill


@router.put("/{bill_id}", response_model=dict)
async def update_weighbill(
        bill_id: int,
        request: WeighbillUpdateRequest = Body(...),
        service: WeighbillService = Depends(get_weighbill_service)
):
    """
    编辑/修正磅单

    用于：
    - OCR识别错误时人工修正
    - 补充缺失信息
    - 修改关联关系
    """
    try:
        data = {k: v for k, v in request.dict().items() if v is not None}

        # 重新计算总价
        if "unit_price" in data or "net_weight" in data:
            # 获取现有数据
            existing = service.get_weighbill(bill_id)
            unit_price = data.get("unit_price", existing.get("unit_price"))
            net_weight = data.get("net_weight", existing.get("net_weight"))
            if unit_price and net_weight:
                data["total_amount"] = round(unit_price * net_weight, 2)

        result = service.update_weighbill(bill_id, data)

        if result["success"]:
            return result
        else:
            raise HTTPException(status_code=400, detail=result.get("error"))

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/", response_model=dict)
async def list_weighbills(
        status: Optional[str] = Query(None, description="状态筛选：待确认/已确认/已修正"),
        vehicle_no: Optional[str] = Query(None, description="车牌号筛选"),
        contract_no: Optional[str] = Query(None, description="合同编号筛选"),
        date_from: Optional[str] = Query(None, description="开始日期"),
        date_to: Optional[str] = Query(None, description="结束日期"),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        service: WeighbillService = Depends(get_weighbill_service)
):
    """查询磅单列表"""
    try:
        with service.get_conn() as conn:
            with conn.cursor() as cur:
                where_clauses = ["1=1"]
                params = []

                if status:
                    where_clauses.append("ocr_status = %s")
                    params.append(status)

                if vehicle_no:
                    where_clauses.append("vehicle_no LIKE %s")
                    params.append(f"%{vehicle_no}%")

                if contract_no:
                    where_clauses.append("contract_no = %s")
                    params.append(contract_no)

                if date_from:
                    where_clauses.append("weigh_date >= %s")
                    params.append(date_from)

                if date_to:
                    where_clauses.append("weigh_date <= %s")
                    params.append(date_to)

                where_sql = "WHERE " + " AND ".join(where_clauses)

                # 总数
                cur.execute(f"SELECT COUNT(*) FROM pd_weighbills {where_sql}", tuple(params))
                total = cur.fetchone()[0]

                # 分页
                offset = (page - 1) * page_size
                cur.execute(f"""
                    SELECT * FROM pd_weighbills 
                    {where_sql}
                    ORDER BY created_at DESC
                    LIMIT %s OFFSET %s
                """, tuple(params + [page_size, offset]))

                columns = [desc[0] for desc in cur.description]
                rows = cur.fetchall()
                data = []
                for row in rows:
                    item = dict(zip(columns, row))
                    for key in ["weigh_date", "created_at", "updated_at"]:
                        if item.get(key):
                            item[key] = str(item[key])
                    data.append(item)

                return {
                    "success": True,
                    "data": data,
                    "total": total,
                    "page": page,
                    "page_size": page_size
                }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{bill_id}")
async def delete_weighbill(
        bill_id: int,
        service: WeighbillService = Depends(get_weighbill_service)
):
    """删除磅单"""
    try:
        # 获取图片路径
        bill = service.get_weighbill(bill_id)
        if bill and bill.get("weighbill_image") and os.path.exists(bill["weighbill_image"]):
            os.remove(bill["weighbill_image"])

        with service.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM pd_weighbills WHERE id = %s", (bill_id,))

        return {"success": True, "message": "删除成功"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{bill_id}/confirm")
async def confirm_weighbill(
        bill_id: int,
        service: WeighbillService = Depends(get_weighbill_service)
):
    """
    确认磅单（OCR识别后确认无误）

    确认后：
    - 更新报货订单状态
    - 触发后续流程（如结算）
    """
    try:
        result = service.update_weighbill(bill_id, {
            "ocr_status": "已确认"
        })

        if result["success"]:
            # TODO: 更新关联的报货订单状态
            # TODO: 触发结算流程

            return {"success": True, "message": "磅单已确认"}
        else:
            raise HTTPException(status_code=400, detail=result.get("error"))

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/match/delivery")
async def match_delivery(
        weigh_date: str = Query(..., description="磅单日期"),
        vehicle_no: str = Query(..., description="车牌号"),
        service: WeighbillService = Depends(get_weighbill_service)
):
    """
    手动匹配报货订单

    当自动匹配失败时，提供手动匹配接口
    """
    delivery = service.match_delivery_info(weigh_date, vehicle_no)
    if delivery:
        # 转换时间格式
        for key in ["report_date", "delivery_time", "created_at", "updated_at"]:
            if delivery.get(key):
                delivery[key] = str(delivery[key])
        return {"success": True, "data": delivery, "matched": True}
    else:
        return {"success": True, "data": None, "matched": False, "message": "未找到匹配的报货订单"}


@router.get("/contract/price")
async def get_contract_price(
        contract_no: str = Query(..., description="合同编号"),
        product_name: str = Query("", description="产品名称"),
        service: WeighbillService = Depends(get_weighbill_service)
):
    """手动获取合同单价"""
    price = service.get_contract_price(contract_no, product_name)
    if price:
        return {"success": True, "unit_price": price}
    else:
        return {"success": False, "message": "未找到合同或价格信息"}