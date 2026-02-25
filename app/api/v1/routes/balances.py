"""
磅单结余管理 + 支付回单路由（优化版）
"""
import json
import os
import re
import shutil
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, Query, Body, Form
from pydantic import BaseModel, Field, ValidationError

from app.core.paths import TEMP_UPLOADS_DIR
from app.services.balance_service import BalanceService, get_balance_service, UPLOAD_DIR

router = APIRouter(prefix="/balances", tags=["磅单结余管理"])


# ========== 请求/响应模型 ==========

class PaymentReceiptOCRResponse(BaseModel):
    receipt_no: Optional[str] = None
    payment_date: Optional[str] = None
    payment_time: Optional[str] = None
    payer_name: Optional[str] = None
    payer_account: Optional[str] = None
    payee_name: Optional[str] = None
    payee_account: Optional[str] = None
    amount: Optional[float] = None
    bank_name: Optional[str] = None
    remark: Optional[str] = None
    ocr_message: str = ""
    raw_text: Optional[str] = None
    ocr_time: float = 0
    ocr_success: bool = True


class PaymentReceiptCreateRequest(BaseModel):
    receipt_no: Optional[str] = Field(None, description="回单编号")
    payment_date: str = Field(..., description="支付日期")
    payment_time: Optional[str] = Field(None, description="支付时间")
    payer_name: Optional[str] = Field(None, description="付款人")
    payer_account: Optional[str] = Field(None, description="付款账号")
    payee_name: str = Field(..., description="收款人（司机）")
    payee_account: Optional[str] = Field(None, description="收款账号")
    amount: float = Field(..., description="支付金额")
    bank_name: Optional[str] = Field(None, description="银行名称")
    remark: Optional[str] = Field(None, description="备注")


class SettlementItem(BaseModel):
    balance_id: int = Field(..., description="结余明细ID")
    amount: float = Field(..., description="本次核销金额")


class BalanceOut(BaseModel):
    id: int
    contract_no: Optional[str] = None
    driver_name: Optional[str] = None
    driver_phone: Optional[str] = None
    vehicle_no: Optional[str] = None
    payable_amount: Optional[float] = None
    paid_amount: Optional[float] = None
    balance_amount: Optional[float] = None
    payment_status: int = 0
    payment_status_name: Optional[str] = None  # 新增
    created_at: Optional[str] = None
    # 关联的磅单字段
    weighbill_image: Optional[str] = None  # 新增
    # 关联的支付回单摘要（可选）
    receipt_count: Optional[int] = 0  # 新增：关联的回单数量

class PaymentReceiptListOut(BaseModel):
    """支付回单列表响应"""
    id: int
    receipt_no: Optional[str] = None
    receipt_image: Optional[str] = None
    payment_date: Optional[str] = None
    payment_time: Optional[str] = None
    payer_name: Optional[str] = None
    payer_account: Optional[str] = None
    payee_name: Optional[str] = None
    payee_account: Optional[str] = None
    amount: Optional[float] = None
    bank_name: Optional[str] = None
    remark: Optional[str] = None
    ocr_status: int
    ocr_status_name: str
    is_manual_corrected: int
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
# ========== 路由 ==========

@router.post("/generate")
async def generate_balance(
        contract_no: Optional[str] = Query(None, description="指定合同编号"),
        delivery_id: Optional[int] = Query(None, description="指定报货订单"),
        weighbill_id: Optional[int] = Query(None, description="指定磅单ID"),
        service: BalanceService = Depends(get_balance_service)
):
    """
    生成磅单结余明细
    根据已确认的磅单数据，自动生成应付明细
    """
    result = service.generate_balance_details(contract_no, delivery_id, weighbill_id)
    if result["success"]:
        return result
    else:
        raise HTTPException(status_code=400, detail=result.get("error"))


@router.get("/", response_model=dict)
async def list_balances(
        exact_contract_no: Optional[str] = Query(None, description="精确合同编号"),
        exact_driver_name: Optional[str] = Query(None, description="精确司机姓名"),
        fuzzy_keywords: Optional[str] = Query(None, description="模糊关键词（空格分隔）"),
        payment_status: Optional[int] = Query(None, description="0=待支付, 1=部分支付, 2=已结清"),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        service: BalanceService = Depends(get_balance_service)
):
    """查询结余明细列表"""
    return service.list_balance_details(
        exact_contract_no,
        exact_driver_name,
        fuzzy_keywords,
        payment_status,
        page,
        page_size,
    )


@router.get("/{balance_id}", response_model=BalanceOut)
async def get_balance(
        balance_id: int,
        service: BalanceService = Depends(get_balance_service)
):
    """查看结余明细详情（包含支付记录）"""
    balance = service.get_balance_detail(balance_id)
    if not balance:
        raise HTTPException(status_code=404, detail="结余明细不存在")

    # 转换状态为可读字符串
    status_map = {0: "待支付", 1: "部分支付", 2: "已结清"}
    balance['payment_status_label'] = status_map.get(balance.get('payment_status'), "未知")

    return balance


@router.post("/payment-receipts/ocr", response_model=PaymentReceiptOCRResponse)
async def ocr_payment_receipt(
        file: UploadFile = File(..., description="支付回单图片"),
        service: BalanceService = Depends(get_balance_service)
):
    """
    OCR识别支付回单
    TODO: 有待样例图片后完善识别逻辑
    """
    allowed_types = ["image/jpeg", "image/jpg", "image/png", "image/bmp"]
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail="仅支持jpg/png/bmp格式")

    temp_path = TEMP_UPLOADS_DIR / f"receipt_{os.urandom(4).hex()}.jpg"
    temp_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        processed_path = service.preprocess_image(str(temp_path))
        result = service.recognize_payment_receipt(processed_path)

        if processed_path != str(temp_path) and os.path.exists(processed_path):
            os.remove(processed_path)
        os.remove(temp_path)

        if not result["success"]:
            raise HTTPException(status_code=400, detail=result.get("error"))

        return PaymentReceiptOCRResponse(**result["data"])

    except HTTPException:
        raise
    except Exception as e:
        if temp_path.exists():
            os.remove(temp_path)
        raise HTTPException(status_code=500, detail=f"处理失败: {str(e)}")


@router.post("/payment-receipts", response_model=dict)
async def create_payment_receipt(
    request: Optional[PaymentReceiptCreateRequest] = Body(None),
    request_json: Optional[str] = Form(None, description="回单数据JSON字符串"),
        receipt_image: UploadFile = File(..., description="回单图片（必填）"),
        is_manual: bool = Form(True),
        service: BalanceService = Depends(get_balance_service)
):
    """
    保存支付回单（OCR后确认或纯手动录入）
    """
    try:
        if request is None:
            if not request_json:
                raise HTTPException(status_code=422, detail="缺少回单数据")
            try:
                request = PaymentReceiptCreateRequest(**json.loads(request_json))
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=400, detail=f"request_json不是合法JSON: {exc.msg}")
            except ValidationError as exc:
                raise HTTPException(status_code=422, detail=exc.errors())

        data = request.dict()

        # 保存图片
        file_ext = Path(receipt_image.filename).suffix.lower() or ".jpg"
        safe_payee = re.sub(r'[^\w\-]', '_', request.payee_name)
        filename = f"receipt_{safe_payee}_{request.payment_date}_{os.urandom(4).hex()[:8]}{file_ext}"
        file_path = UPLOAD_DIR / filename

        file_path.parent.mkdir(parents=True, exist_ok=True)

        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(receipt_image.file, buffer)

        result = service.create_payment_receipt(data, str(file_path), is_manual)

        if result["success"]:
            return result
        else:
            # 失败时删除图片
            if file_path.exists():
                os.remove(file_path)
            raise HTTPException(status_code=400, detail=result.get("error"))

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/match/pending")
async def match_pending(
        payee_name: str = Query(..., description="收款人姓名（司机）"),
        amount: float = Query(..., description="支付金额"),
        date_range: int = Query(7, description="查询天数范围"),
        service: BalanceService = Depends(get_balance_service)
):
    """
    根据收款人+金额匹配待支付结余
    用于支付回单与结余明细的匹配
    """
    matches = service.match_pending_payments(payee_name, amount, date_range)
    return {
        "success": True,
        "matched_count": len(matches),
        "data": matches
    }


@router.post("/verify-payment", response_model=dict)
async def verify_payment(
        receipt_id: int = Form(..., description="支付回单ID"),
        items: List[SettlementItem] = Body(..., description="核销明细列表"),
        service: BalanceService = Depends(get_balance_service)
):
    """
    核销支付（支持分批核销）

    示例:
    {
        "receipt_id": 1,
        "items": [
            {"balance_id": 1, "amount": 5000},
            {"balance_id": 2, "amount": 3000}
        ]
    }
    """
    balance_items = [{"balance_id": item.balance_id, "amount": item.amount} for item in items]

    result = service.verify_payment(receipt_id, balance_items)
    if result["success"]:
        return result
    else:
        raise HTTPException(status_code=400, detail=result.get("error"))


@router.get("/payment-receipts/{receipt_id}")
async def get_payment_receipt(
        receipt_id: int,
        service: BalanceService = Depends(get_balance_service)
):
    """查看支付回单详情（包含核销记录）"""
    receipt = service.get_payment_receipt(receipt_id)
    if not receipt:
        raise HTTPException(status_code=404, detail="支付回单不存在")

    # 转换状态
    status_map = {0: "待确认", 1: "已确认", 2: "已核销"}
    receipt['ocr_status_label'] = status_map.get(receipt.get('ocr_status'), "未知")

    return receipt


@router.get("/payment-receipts", response_model=dict)
async def list_payment_receipts(
        exact_payee_name: Optional[str] = Query(None, description="精确收款人姓名"),
        exact_ocr_status: Optional[int] = Query(None, ge=0, le=2, description="状态：0待确认/1已确认/2已核销"),
        date_from: Optional[str] = Query(None, description="开始日期"),
        date_to: Optional[str] = Query(None, description="结束日期"),
        fuzzy_keywords: Optional[str] = Query(None, description="模糊关键词（空格分隔）"),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        service: BalanceService = Depends(get_balance_service)
):
    """
    查询支付回单列表

    支持筛选：
    - 精确收款人姓名
    - 状态筛选（0待确认/1已确认/2已核销）
    - 日期范围
    - 模糊搜索（回单号/收款人/付款人/银行/备注）
    """
    result = service.list_payment_receipts(
        exact_payee_name=exact_payee_name,
        exact_ocr_status=exact_ocr_status,
        date_from=date_from,
        date_to=date_to,
        fuzzy_keywords=fuzzy_keywords,
        page=page,
        page_size=page_size
    )

    if result["success"]:
        return result
    else:
        raise HTTPException(status_code=500, detail=result.get("error"))