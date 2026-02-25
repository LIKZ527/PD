"""
磅单结余管理 + 支付回单处理服务（优化版）
"""
import logging
import os
import re
import shutil
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta

from PIL import Image, ImageEnhance, ImageFilter

try:
    from rapidocr_onnxruntime import RapidOCR

    RAPIDOCR_AVAILABLE = True
except ImportError:
    RAPIDOCR_AVAILABLE = False

from app.core.paths import UPLOADS_DIR
from app.services.contract_service import get_conn

logger = logging.getLogger(__name__)

UPLOAD_DIR = UPLOADS_DIR / "payment_receipts"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


class BalanceService:
    """磅单结余服务"""

    def __init__(self):
        self.ocr = None
        if RAPIDOCR_AVAILABLE:
            try:
                self.ocr = RapidOCR()
                logger.info("支付回单OCR初始化成功")
            except Exception as e:
                logger.error(f"支付回单OCR初始化失败: {e}")

    # ========== 状态常量 ==========
    OCR_STATUS_PENDING = 0  # 待确认
    OCR_STATUS_CONFIRMED = 1  # 已确认
    OCR_STATUS_VERIFIED = 2  # 已核销

    PAY_STATUS_PENDING = 0  # 待支付
    PAY_STATUS_PARTIAL = 1  # 部分支付
    PAY_STATUS_SETTLED = 2  # 已结清

    # ========== 磅单结余生成 ==========

    def generate_balance_details(self, contract_no: str = None,
                                 delivery_id: int = None,
                                 weighbill_id: int = None) -> Dict[str, Any]:
        """
        根据磅单数据自动生成结余明细
        应付金额 = 净重 × 合同单价
        """
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    # 构建查询条件
                    conditions = ["w.ocr_status = '已确认'"]
                    params = []

                    if contract_no:
                        conditions.append("w.contract_no = %s")
                        params.append(contract_no)
                    if delivery_id:
                        conditions.append("w.delivery_id = %s")
                        params.append(delivery_id)
                    if weighbill_id:
                        conditions.append("w.id = %s")
                        params.append(weighbill_id)

                    # 排除已生成结余的磅单
                    conditions.append("NOT EXISTS (SELECT 1 FROM pd_balance_details b WHERE b.weighbill_id = w.id)")

                    where_sql = " AND ".join(conditions)

                    # 查询符合条件的磅单
                    cur.execute(f"""
                        SELECT 
                            w.id as weighbill_id,
                            w.contract_no,
                            w.delivery_id,
                            w.vehicle_no,
                            w.product_name,
                            w.net_weight,
                            w.unit_price,
                            d.driver_name,
                            d.driver_phone
                        FROM pd_weighbills w
                        LEFT JOIN pd_deliveries d ON w.delivery_id = d.id
                        WHERE {where_sql}
                    """, tuple(params))

                    columns = [desc[0] for desc in cur.description]
                    rows = cur.fetchall()

                    generated = []
                    for row in rows:
                        data = dict(zip(columns, row))

                        # 计算应付金额
                        net_weight = data.get('net_weight') or 0
                        unit_price = data.get('unit_price') or 0
                        payable = Decimal(str(net_weight)) * Decimal(str(unit_price))

                        # 插入结余明细
                        cur.execute("""
                            INSERT INTO pd_balance_details 
                            (contract_no, delivery_id, weighbill_id, driver_name, driver_phone,
                             vehicle_no, payable_amount, paid_amount, balance_amount, payment_status)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """, (
                            data.get('contract_no'),
                            data.get('delivery_id'),
                            data.get('weighbill_id'),
                            data.get('driver_name'),
                            data.get('driver_phone'),
                            data.get('vehicle_no'),
                            payable,
                            0,
                            payable,
                            self.PAY_STATUS_PENDING
                        ))

                        generated.append({
                            'balance_id': cur.lastrowid,
                            'weighbill_id': data.get('weighbill_id'),
                            'driver_name': data.get('driver_name'),
                            'payable_amount': float(payable)
                        })

                    return {
                        "success": True,
                        "message": f"成功生成 {len(generated)} 条结余明细",
                        "data": generated
                    }

        except Exception as e:
            logger.error(f"生成结余明细失败: {e}")
            return {"success": False, "error": str(e)}

    def recalculate_balance(self, balance_id: int) -> Dict[str, Any]:
        """重新计算结余金额和状态"""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    # 获取当前数据
                    cur.execute("""
                        SELECT payable_amount, paid_amount 
                        FROM pd_balance_details 
                        WHERE id = %s
                    """, (balance_id,))

                    row = cur.fetchone()
                    if not row:
                        return {"success": False, "error": "结余明细不存在"}

                    payable, paid = Decimal(str(row[0])), Decimal(str(row[1]))
                    balance = payable - paid

                    # 确定状态
                    if paid <= 0:
                        status = self.PAY_STATUS_PENDING
                    elif paid >= payable:
                        status = self.PAY_STATUS_SETTLED
                    else:
                        status = self.PAY_STATUS_PARTIAL

                    # 更新
                    cur.execute("""
                        UPDATE pd_balance_details 
                        SET balance_amount = %s, payment_status = %s 
                        WHERE id = %s
                    """, (balance, status, balance_id))

                    return {
                        "success": True,
                        "data": {
                            'payable': float(payable),
                            'paid': float(paid),
                            'balance': float(balance),
                            'status': status
                        }
                    }

        except Exception as e:
            logger.error(f"重新计算结余失败: {e}")
            return {"success": False, "error": str(e)}

    # ========== 支付回单OCR（待完善） ==========

    def preprocess_image(self, image_path: str) -> str:
        """图片预处理"""
        try:
            img = Image.open(image_path)
            if img.mode != "RGB":
                img = img.convert("RGB")

            enhancer = ImageEnhance.Contrast(img)
            img = enhancer.enhance(1.5)
            img = img.filter(ImageFilter.SHARPEN)

            max_size = 2000
            if max(img.size) > max_size:
                ratio = max_size / max(img.size)
                new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
                img = img.resize(new_size, Image.Resampling.LANCZOS)

            temp_path = tempfile.mktemp(suffix=".jpg")
            img.save(temp_path, "JPEG", quality=95)
            return temp_path

        except Exception as e:
            logger.error(f"预处理失败: {e}")
            return image_path

    def recognize_payment_receipt(self, image_path: str) -> Dict[str, Any]:
        """
        OCR识别支付回单
        TODO: 有待样例图片后完善具体识别逻辑
        """
        if not self.ocr:
            return {
                "success": True,
                "data": self._empty_receipt_result("OCR未初始化"),
                "ocr_success": False
            }

        try:
            result, elapse = self.ocr(image_path)
            total_elapse = sum(elapse) if isinstance(elapse, list) else float(elapse or 0)

            if not result:
                return {
                    "success": True,
                    "data": self._empty_receipt_result("未能识别到文本"),
                    "ocr_success": False
                }

            text_lines = []
            for item in result:
                bbox, text, confidence = item
                text_lines.append({"text": text.strip(), "confidence": float(confidence)})

            full_text = "\n".join([line["text"] for line in text_lines])

            logger.info("=== 支付回单OCR识别文本 ===")
            for i, line in enumerate(text_lines):
                logger.info(f"{i}: {line['text']}")

            # TODO: 有待样例图片后完善解析逻辑
            data = {
                "receipt_no": None,
                "payment_date": None,
                "payment_time": None,
                "payer_name": None,
                "payer_account": None,
                "payee_name": None,
                "payee_account": None,
                "amount": None,
                "bank_name": None,
                "remark": None,
                "ocr_message": "OCR识别完成，请人工核对填写（待样例图片优化）",
                "raw_text": full_text,
                "ocr_time": round(total_elapse, 3)
            }

            return {
                "success": True,
                "data": data,
                "ocr_success": True
            }

        except Exception as e:
            logger.error(f"支付回单识别异常: {e}")
            return {
                "success": True,
                "data": self._empty_receipt_result(f"识别异常: {str(e)}"),
                "ocr_success": False
            }

    def _empty_receipt_result(self, message: str) -> Dict:
        """返回空结果结构"""
        return {
            "receipt_no": None,
            "payment_date": None,
            "payment_time": None,
            "payer_name": None,
            "payer_account": None,
            "payee_name": None,
            "payee_account": None,
            "amount": None,
            "bank_name": None,
            "remark": None,
            "ocr_message": message,
            "raw_text": "",
            "ocr_time": 0
        }

    # ========== 匹配核销逻辑 ==========

    def match_pending_payments(self, payee_name: str, amount: float,
                               date_range: int = 7) -> List[Dict]:
        """
        根据收款人+金额匹配待支付结余
        使用组合索引 (payee_name, amount) 提高查询效率
        """
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    # 查询待支付数据，允许金额误差0.01
                    cur.execute("""
                        SELECT * FROM pd_balance_details 
                        WHERE payment_status IN (0, 1)
                        AND driver_name LIKE %s
                        AND ABS(payable_amount - %s) <= 0.01
                        AND created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
                        ORDER BY balance_amount DESC, created_at ASC
                        LIMIT 10
                    """, (f"%{payee_name}%", amount, date_range))

                    columns = [desc[0] for desc in cur.description]
                    results = []
                    for row in cur.fetchall():
                        data = dict(zip(columns, row))
                        for key in ['created_at', 'updated_at']:
                            if data.get(key):
                                data[key] = str(data[key])
                        results.append(data)

                    return results

        except Exception as e:
            logger.error(f"匹配待支付数据失败: {e}")
            return []

    def verify_payment(self, receipt_id: int, balance_items: List[Dict]) -> Dict[str, Any]:
        """
        核销支付（支持分批核销）
        balance_items: [{"balance_id": 1, "amount": 1000}, ...]
        """
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    # 获取回单信息
                    cur.execute("""
                        SELECT amount, ocr_status 
                        FROM pd_payment_receipts 
                        WHERE id = %s
                    """, (receipt_id,))

                    row = cur.fetchone()
                    if not row:
                        return {"success": False, "error": "支付回单不存在"}

                    receipt_amount, ocr_status = Decimal(str(row[0])), row[1]

                    if ocr_status == self.OCR_STATUS_VERIFIED:
                        return {"success": False, "error": "该回单已核销"}

                    total_settled = Decimal('0')
                    settled_items = []

                    for item in balance_items:
                        balance_id = item.get('balance_id')
                        settle_amount = Decimal(str(item.get('amount', 0)))

                        # 获取结余当前状态
                        cur.execute("""
                            SELECT payable_amount, paid_amount, payment_status
                            FROM pd_balance_details 
                            WHERE id = %s
                        """, (balance_id,))

                        row = cur.fetchone()
                        if not row:
                            continue

                        payable, paid, status = Decimal(str(row[0])), Decimal(str(row[1])), row[2]

                        # 验证核销金额
                        remaining = payable - paid
                        if settle_amount > remaining:
                            settle_amount = remaining  # 不能超过剩余应付

                        new_paid = paid + settle_amount

                        # 确定新状态
                        if new_paid >= payable:
                            new_status = self.PAY_STATUS_SETTLED
                        elif new_paid > 0:
                            new_status = self.PAY_STATUS_PARTIAL
                        else:
                            new_status = self.PAY_STATUS_PENDING

                        # 更新结余明细
                        new_balance = payable - new_paid
                        cur.execute("""
                            UPDATE pd_balance_details 
                            SET paid_amount = %s, balance_amount = %s, payment_status = %s 
                            WHERE id = %s
                        """, (new_paid, new_balance, new_status, balance_id))

                        # 插入关联表
                        cur.execute("""
                            INSERT INTO pd_receipt_settlements 
                            (receipt_id, balance_id, settled_amount)
                            VALUES (%s, %s, %s)
                            ON DUPLICATE KEY UPDATE settled_amount = %s
                        """, (receipt_id, balance_id, settle_amount, settle_amount))

                        total_settled += settle_amount
                        settled_items.append({
                            'balance_id': balance_id,
                            'settled_amount': float(settle_amount),
                            'status': new_status
                        })

                    # 更新回单状态
                    new_receipt_status = self.OCR_STATUS_VERIFIED if total_settled >= receipt_amount else self.OCR_STATUS_CONFIRMED
                    cur.execute("""
                        UPDATE pd_payment_receipts 
                        SET ocr_status = %s 
                        WHERE id = %s
                    """, (new_receipt_status, receipt_id))

                    return {
                        "success": True,
                        "message": f"成功核销 {len(settled_items)} 条明细",
                        "data": {
                            'receipt_id': receipt_id,
                            'total_settled': float(total_settled),
                            'receipt_status': new_receipt_status,
                            'items': settled_items
                        }
                    }

        except Exception as e:
            logger.error(f"核销支付失败: {e}")
            return {"success": False, "error": str(e)}

    # ========== CRUD操作 ==========

    def create_payment_receipt(self, data: Dict, image_path: str,
                               is_manual: bool = False) -> Dict[str, Any]:
        """创建支付回单记录"""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO pd_payment_receipts 
                        (receipt_no, receipt_image, payment_date, payment_time,
                         payer_name, payer_account, payee_name, payee_account,
                         amount, bank_name, remark, ocr_status, ocr_raw_data, is_manual_corrected)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        data.get('receipt_no'),
                        image_path,
                        data.get('payment_date'),
                        data.get('payment_time'),
                        data.get('payer_name'),
                        data.get('payer_account'),
                        data.get('payee_name'),
                        data.get('payee_account'),
                        data.get('amount'),
                        data.get('bank_name'),
                        data.get('remark'),
                        self.OCR_STATUS_CONFIRMED if is_manual else self.OCR_STATUS_PENDING,
                        data.get('raw_text'),
                        1 if is_manual else 0
                    ))

                    return {
                        "success": True,
                        "message": "支付回单保存成功",
                        "data": {"id": cur.lastrowid}
                    }

        except Exception as e:
            logger.error(f"保存支付回单失败: {e}")
            return {"success": False, "error": str(e)}

    def get_balance_detail(self, balance_id: int) -> Optional[Dict]:
        """获取结余明细详情（包含核销记录）"""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    # 主表
                    cur.execute("""
                        SELECT b.*, w.weighbill_image 
                        FROM pd_balance_details b
                        LEFT JOIN pd_weighbills w ON b.weighbill_id = w.id
                        WHERE b.id = %s
                    """, (balance_id,))

                    row = cur.fetchone()
                    if not row:
                        return None

                    columns = [desc[0] for desc in cur.description]
                    data = dict(zip(columns, row))

                    # 转换时间
                    for key in ['created_at', 'updated_at']:
                        if data.get(key):
                            data[key] = str(data[key])

                    # 查询关联的支付回单
                    cur.execute("""
                        SELECT r.id, r.payee_name, r.amount, r.payment_date, 
                               s.settled_amount, r.receipt_image
                        FROM pd_receipt_settlements s
                        JOIN pd_payment_receipts r ON s.receipt_id = r.id
                        WHERE s.balance_id = %s
                        ORDER BY s.created_at DESC
                    """, (balance_id,))

                    receipts = []
                    for r in cur.fetchall():
                        receipts.append({
                            'receipt_id': r[0],
                            'payee_name': r[1],
                            'amount': float(r[2]) if r[2] else None,
                            'payment_date': str(r[3]) if r[3] else None,
                            'settled_amount': float(r[4]) if r[4] else None,
                            'receipt_image': r[5]
                        })

                    data['payment_receipts'] = receipts
                    return data

        except Exception as e:
            logger.error(f"查询结余明细失败: {e}")
            return None

    def list_balance_details(self,
                             exact_contract_no: str = None,
                             exact_driver_name: str = None,
                             fuzzy_keywords: str = None,
                             payment_status: int = None,
                             page: int = 1,
                             page_size: int = 20) -> Dict[str, Any]:
        """查询结余明细列表（扩展版，包含关联信息）"""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    conditions = ["1=1"]
                    params = []

                    if exact_contract_no:
                        conditions.append("b.contract_no = %s")
                        params.append(exact_contract_no)
                    if exact_driver_name:
                        conditions.append("b.driver_name = %s")
                        params.append(exact_driver_name)
                    if fuzzy_keywords:
                        tokens = [t for t in fuzzy_keywords.split() if t]
                        or_clauses = []
                        for token in tokens:
                            like = f"%{token}%"
                            or_clauses.append(
                                "(b.contract_no LIKE %s OR b.driver_name LIKE %s OR b.driver_phone LIKE %s OR b.vehicle_no LIKE %s)"
                            )
                            params.extend([like, like, like, like])
                        if or_clauses:
                            conditions.append("(" + " OR ".join(or_clauses) + ")")
                    if payment_status is not None:
                        conditions.append("b.payment_status = %s")
                        params.append(payment_status)

                    where_sql = " AND ".join(conditions)

                    # 查询总数
                    cur.execute(f"""
                        SELECT COUNT(*) FROM pd_balance_details b
                        WHERE {where_sql}
                    """, tuple(params))
                    total = cur.fetchone()[0]

                    # 分页数据 - 关联磅单获取图片
                    offset = (page - 1) * page_size
                    cur.execute(f"""
                        SELECT 
                            b.*,
                            w.weighbill_image,
                            (SELECT COUNT(*) FROM pd_receipt_settlements rs 
                             JOIN pd_payment_receipts pr ON rs.receipt_id = pr.id 
                             WHERE rs.balance_id = b.id) as receipt_count
                        FROM pd_balance_details b
                        LEFT JOIN pd_weighbills w ON b.weighbill_id = w.id
                        WHERE {where_sql}
                        ORDER BY b.created_at DESC
                        LIMIT %s OFFSET %s
                    """, tuple(params + [page_size, offset]))

                    columns = [desc[0] for desc in cur.description]
                    data = []
                    status_map = {0: "待支付", 1: "部分支付", 2: "已结清"}

                    for row in cur.fetchall():
                        item = dict(zip(columns, row))
                        for key in ['created_at', 'updated_at']:
                            if item.get(key):
                                item[key] = str(item[key])
                        # 添加状态名称
                        item['payment_status_name'] = status_map.get(item.get('payment_status'), "未知")
                        data.append(item)

                    return {
                        "success": True,
                        "data": data,
                        "total": total,
                        "page": page,
                        "page_size": page_size
                    }

        except Exception as e:
            logger.error(f"查询结余列表失败: {e}")
            return {"success": False, "error": str(e), "data": [], "total": 0}

    def get_payment_receipt(self, receipt_id: int) -> Optional[Dict]:
        """获取支付回单详情"""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM pd_payment_receipts WHERE id = %s", (receipt_id,))
                    row = cur.fetchone()
                    if not row:
                        return None

                    columns = [desc[0] for desc in cur.description]
                    data = dict(zip(columns, row))

                    # 转换时间
                    for key in ['payment_date', 'payment_time', 'created_at', 'updated_at']:
                        if data.get(key):
                            data[key] = str(data[key])

                    # 查询核销的结余明细
                    cur.execute("""
                        SELECT b.id, b.driver_name, b.vehicle_no, b.payable_amount,
                               s.settled_amount
                        FROM pd_receipt_settlements s
                        JOIN pd_balance_details b ON s.balance_id = b.id
                        WHERE s.receipt_id = %s
                    """, (receipt_id,))

                    settlements = []
                    for r in cur.fetchall():
                        settlements.append({
                            'balance_id': r[0],
                            'driver_name': r[1],
                            'vehicle_no': r[2],
                            'payable_amount': float(r[3]) if r[3] else None,
                            'settled_amount': float(r[4]) if r[4] else None
                        })

                    data['settlements'] = settlements
                    return data

        except Exception as e:
            logger.error(f"查询支付回单失败: {e}")
            return None

    def list_payment_receipts(
        self,
        exact_payee_name: str = None,
        exact_ocr_status: int = None,
        date_from: str = None,
        date_to: str = None,
        fuzzy_keywords: str = None,
        page: int = 1,
        page_size: int = 20
    ) -> Dict[str, Any]:
        """查询支付回单列表"""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    conditions = ["1=1"]
                    params = []

                    if exact_payee_name:
                        conditions.append("payee_name = %s")
                        params.append(exact_payee_name)

                    if exact_ocr_status is not None:
                        conditions.append("ocr_status = %s")
                        params.append(exact_ocr_status)

                    if date_from:
                        conditions.append("payment_date >= %s")
                        params.append(date_from)

                    if date_to:
                        conditions.append("payment_date <= %s")
                        params.append(date_to)

                    if fuzzy_keywords:
                        tokens = [t for t in fuzzy_keywords.split() if t]
                        or_clauses = []
                        for token in tokens:
                            like = f"%{token}%"
                            or_clauses.append(
                                "(receipt_no LIKE %s OR payee_name LIKE %s OR payer_name LIKE %s "
                                "OR bank_name LIKE %s OR remark LIKE %s)"
                            )
                            params.extend([like, like, like, like, like])
                        if or_clauses:
                            conditions.append("(" + " OR ".join(or_clauses) + ")")

                    where_sql = " AND ".join(conditions)

                    # 查询总数
                    cur.execute(f"SELECT COUNT(*) FROM pd_payment_receipts WHERE {where_sql}", tuple(params))
                    total = cur.fetchone()[0]

                    # 分页数据
                    offset = (page - 1) * page_size
                    cur.execute(f"""
                        SELECT * FROM pd_payment_receipts 
                        WHERE {where_sql}
                        ORDER BY created_at DESC
                        LIMIT %s OFFSET %s
                    """, tuple(params + [page_size, offset]))

                    columns = [desc[0] for desc in cur.description]
                    data = []
                    for row in cur.fetchall():
                        item = dict(zip(columns, row))
                        # 转换时间字段
                        for key in ['payment_date', 'payment_time', 'created_at', 'updated_at']:
                            if item.get(key):
                                item[key] = str(item[key])
                        # 添加状态名称
                        status_map = {0: "待确认", 1: "已确认", 2: "已核销"}
                        item['ocr_status_name'] = status_map.get(item.get('ocr_status'), "未知")
                        data.append(item)

                    return {
                        "success": True,
                        "data": data,
                        "total": total,
                        "page": page,
                        "page_size": page_size
                    }

        except Exception as e:
            logger.error(f"查询支付回单列表失败: {e}")
            return {"success": False, "error": str(e), "data": [], "total": 0}

_balance_service = None


def get_balance_service():
    global _balance_service
    if _balance_service is None:
        _balance_service = BalanceService()
    return _balance_service