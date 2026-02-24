import re
from typing import Optional, Dict, Any, List
from enum import IntEnum
from datetime import datetime, date
from decimal import Decimal, ROUND_HALF_UP

from core.database import get_conn
from core.table_access import build_dynamic_select, _quote_identifier
from core.logging import get_logger

logger = get_logger(__name__)


# ========== 枚举定义 ==========

class PaymentStatus(IntEnum):
    """回款状态枚举"""
    UNPAID = 0       # 未回款
    PARTIAL = 1      # 部分回款
    PAID = 2         # 已结清
    OVERPAID = 3     # 超额回款（异常）


class PaymentStage(IntEnum):
    """回款阶段枚举"""
    DEPOSIT = 0      # 定金
    DELIVERY = 1     # 到货款（90%）
    FINAL = 2        # 尾款（10%）


# ========== 工具函数 ==========

def validate_amount(amount: float) -> bool:
    """验证金额格式（必须为正数，最多2位小数）"""
    if amount is None or amount < 0:
        return False
    return bool(re.match(r'^\d+\.?\d{0,2}$', str(amount)))


def calculate_payment_amount(unit_price: Decimal, net_weight: Decimal) -> Decimal:
    """
    计算回款金额
    回款金额 = 回款单价（合同单价）* 净重

    Args:
        unit_price: 合同单价
        net_weight: 净重

    Returns:
        计算后的回款金额（保留2位小数）
    """
    amount = unit_price * net_weight
    return amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def determine_payment_status(total_amount: Decimal, paid_amount: Decimal) -> PaymentStatus:
    """
    根据已付金额确定回款状态

    Args:
        total_amount: 应回款总额
        paid_amount: 已回款金额

    Returns:
        回款状态
    """
    if paid_amount <= 0:
        return PaymentStatus.UNPAID
    elif paid_amount >= total_amount:
        if paid_amount > total_amount:
            return PaymentStatus.OVERPAID
        return PaymentStatus.PAID
    else:
        return PaymentStatus.PARTIAL


# ========== 收款明细服务 ==========

class PaymentService:
    """
    冶炼厂回款明细服务

    功能：
    1. 根据销售业务数据生成收款明细台账
    2. 支持财务人员录入收款信息
    3. 支持分段收款模式（定金/到货款90%/尾款10%）
    4. 自动计算累计已付金额与未付金额
    """

    TABLE_NAME = "pd_payment_details"
    RECORD_TABLE = "pd_payment_records"

    @staticmethod
    def ensure_tables_exist():
        """
        确保收款明细表和回款记录表存在
        """
        with get_conn() as conn:
            with conn.cursor() as cur:
                # 检查主表
                cur.execute(f"SHOW TABLES LIKE '{PaymentService.TABLE_NAME}'")
                if not cur.fetchone():
                    raise RuntimeError(f"{PaymentService.TABLE_NAME} 表不存在，请先执行数据库初始化")

                # 检查记录表
                cur.execute(f"SHOW TABLES LIKE '{PaymentService.RECORD_TABLE}'")
                if not cur.fetchone():
                    raise RuntimeError(f"{PaymentService.RECORD_TABLE} 表不存在，请先执行数据库初始化")

    @staticmethod
    def create_payment_detail(
        sales_order_id: int,
        smelter_name: str,
        contract_no: str,
        unit_price: Decimal,
        net_weight: Decimal,
        material_name: Optional[str] = None,
        remark: Optional[str] = None,
        created_by: Optional[int] = None
    ) -> int:
        """
        创建收款明细台账（根据销售业务数据生成）

        Args:
            sales_order_id: 销售订单ID
            smelter_name: 冶炼厂名称
            contract_no: 合同编号
            unit_price: 合同单价
            net_weight: 净重
            material_name: 物料名称（可选）
            remark: 备注（可选）
            created_by: 创建人ID（可选）

        Returns:
            收款明细ID

        Raises:
            ValueError: 参数校验失败
        """
        # 参数校验
        if not sales_order_id or sales_order_id <= 0:
            raise ValueError("销售订单ID无效")

        if not smelter_name:
            raise ValueError("冶炼厂名称不能为空")

        if not contract_no:
            raise ValueError("合同编号不能为空")

        if unit_price is None or unit_price < 0:
            raise ValueError("合同单价无效")

        if net_weight is None or net_weight < 0:
            raise ValueError("净重无效")

        # 计算应回款总额
        total_amount = calculate_payment_amount(unit_price, net_weight)

        with get_conn() as conn:
            with conn.cursor() as cur:
                # 检查是否已存在该销售订单的收款明细
                cur.execute(
                    f"SELECT id FROM {PaymentService.TABLE_NAME} WHERE sales_order_id=%s AND status!=%s",
                    (sales_order_id, PaymentStatus.OVERPAID)
                )
                if cur.fetchone():
                    raise ValueError("该销售订单已存在收款明细")

                # 动态获取表结构
                cur.execute(f"SHOW COLUMNS FROM {PaymentService.TABLE_NAME}")
                columns = [r["Field"] for r in cur.fetchall()]

                # 准备插入数据
                data = {
                    "sales_order_id": sales_order_id,
                    "smelter_name": smelter_name,
                    "contract_no": contract_no,
                    "material_name": material_name or "",
                    "unit_price": float(unit_price),
                    "net_weight": float(net_weight),
                    "total_amount": float(total_amount),
                    "paid_amount": 0.00,
                    "unpaid_amount": float(total_amount),
                    "status": PaymentStatus.UNPAID,
                    "created_by": created_by,
                    "created_at": datetime.now(),
                    "updated_at": datetime.now()
                }

                if remark and "remark" in columns:
                    data["remark"] = remark

                # 构建插入SQL
                cols = list(data.keys())
                vals = list(data.values())

                cols_sql = ",".join([_quote_identifier(c) for c in cols])
                placeholders = ",".join(["%s"] * len(vals))

                sql = f"INSERT INTO {_quote_identifier(PaymentService.TABLE_NAME)} ({cols_sql}) VALUES ({placeholders})"
                cur.execute(sql, tuple(vals))

                payment_id = cur.lastrowid
                conn.commit()

                logger.info(f"创建收款明细成功: ID={payment_id}, 订单={sales_order_id}, 总额={total_amount}")
                return payment_id

    @staticmethod
    def record_payment(
        payment_detail_id: int,
        payment_amount: Decimal,
        payment_stage: PaymentStage = PaymentStage.DELIVERY,
        payment_date: Optional[date] = None,
        payment_method: Optional[str] = None,
        transaction_no: Optional[str] = None,
        remark: Optional[str] = None,
        recorded_by: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        录入回款记录（支持分段收款）

        Args:
            payment_detail_id: 收款明细ID
            payment_amount: 回款金额
            payment_stage: 回款阶段（定金/到货款/尾款）
            payment_date: 回款日期（默认今天）
            payment_method: 支付方式
            transaction_no: 交易流水号
            remark: 备注
            recorded_by: 录入人ID

        Returns:
            更新后的收款明细信息

        Raises:
            ValueError: 参数校验失败或明细不存在
        """
        # 参数校验
        if not payment_detail_id or payment_detail_id <= 0:
            raise ValueError("收款明细ID无效")

        if payment_amount is None or payment_amount <= 0:
            raise ValueError("回款金额必须大于0")

        payment_date = payment_date or date.today()

        with get_conn() as conn:
            with conn.cursor() as cur:
                # 获取收款明细
                select_sql = build_dynamic_select(
                    cur,
                    PaymentService.TABLE_NAME,
                    where_clause="id=%s",
                    select_fields=["id", "total_amount", "paid_amount", "unpaid_amount", "status"]
                )
                cur.execute(select_sql, (payment_detail_id,))
                detail = cur.fetchone()

                if not detail:
                    raise ValueError("收款明细不存在")

                if detail["status"] == PaymentStatus.PAID:
                    raise ValueError("该订单已结清，无法继续录入回款")

                total_amount = Decimal(str(detail["total_amount"]))
                current_paid = Decimal(str(detail["paid_amount"]))
                new_paid = current_paid + payment_amount
                unpaid_amount = total_amount - new_paid

                # 确定新的状态
                new_status = determine_payment_status(total_amount, new_paid)

                # 插入回款记录
                record_data = {
                    "payment_detail_id": payment_detail_id,
                    "payment_amount": float(payment_amount),
                    "payment_stage": int(payment_stage),
                    "payment_date": payment_date,
                    "payment_method": payment_method or "",
                    "transaction_no": transaction_no or "",
                    "remark": remark or "",
                    "recorded_by": recorded_by,
                    "created_at": datetime.now()
                }

                # 动态获取记录表结构
                cur.execute(f"SHOW COLUMNS FROM {PaymentService.RECORD_TABLE}")
                record_columns = [r["Field"] for r in cur.fetchall()]

                # 过滤存在的字段
                record_data = {k: v for k, v in record_data.items() if k in record_columns}

                cols = list(record_data.keys())
                vals = list(record_data.values())
                cols_sql = ",".join([_quote_identifier(c) for c in cols])
                placeholders = ",".join(["%s"] * len(vals))

                record_sql = f"INSERT INTO {_quote_identifier(PaymentService.RECORD_TABLE)} ({cols_sql}) VALUES ({placeholders})"
                cur.execute(record_sql, tuple(vals))

                # 更新收款明细
                update_sql = f"""
                    UPDATE {_quote_identifier(PaymentService.TABLE_NAME)}
                    SET paid_amount = %s,
                        unpaid_amount = %s,
                        status = %s,
                        updated_at = %s
                    WHERE id = %s
                """
                cur.execute(update_sql, (
                    float(new_paid),
                    float(unpaid_amount),
                    int(new_status),
                    datetime.now(),
                    payment_detail_id
                ))