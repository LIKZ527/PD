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
                    (sales_order_id, int(PaymentStatus.OVERPAID))
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
                    "status": int(PaymentStatus.UNPAID),
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

                conn.commit()  # 添加提交

                # 返回结果
                return {
                    "payment_detail_id": payment_detail_id,
                    "total_amount": float(total_amount),
                    "paid_amount": float(new_paid),
                    "unpaid_amount": float(unpaid_amount),
                    "status": int(new_status),
                    "status_name": new_status.name,
                    "current_payment": float(payment_amount),
                    "payment_stage": int(payment_stage),
                    "payment_stage_name": payment_stage.name
                }

    @staticmethod
    def list_payment_details(
            page: int = 1,
            size: int = 20,
            status: Optional[int] = None,
            smelter_name: Optional[str] = None,
            contract_no: Optional[str] = None,
            start_date: Optional[date] = None,
            end_date: Optional[date] = None,
            keyword: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        查询收款明细列表（包含完整的磅单和销售台账信息）
        """
        with get_conn() as conn:
            with conn.cursor() as cur:
                # 构建WHERE条件
                where_clauses = ["1=1"]
                params = []

                if status is not None:
                    where_clauses.append("pd.status = %s")
                    params.append(status)

                if smelter_name:
                    where_clauses.append("pd.smelter_name LIKE %s")
                    params.append(f"%{smelter_name}%")

                if contract_no:
                    where_clauses.append("pd.contract_no LIKE %s")
                    params.append(f"%{contract_no}%")

                if start_date:
                    where_clauses.append("DATE(pd.created_at) >= %s")
                    params.append(start_date)

                if end_date:
                    where_clauses.append("DATE(pd.created_at) <= %s")
                    params.append(end_date)

                if keyword:
                    where_clauses.append(
                        "(pd.smelter_name LIKE %s OR pd.contract_no LIKE %s OR pd.material_name LIKE %s)")
                    keyword_pattern = f"%{keyword}%"
                    params.extend([keyword_pattern, keyword_pattern, keyword_pattern])

                where_sql = " AND ".join(where_clauses)

                # 查询总数
                count_sql = f"SELECT COUNT(*) as total FROM {PaymentService.TABLE_NAME} pd WHERE {where_sql}"
                cur.execute(count_sql, tuple(params))
                total = cur.fetchone()["total"]

                # 分页查询 - 包含完整的磅单和销售台账字段
                offset = (page - 1) * size
                query_sql = f"""
                    SELECT 
                        -- 收款明细主表字段
                        pd.id,
                        pd.sales_order_id,
                        pd.smelter_name,
                        pd.contract_no,
                        pd.material_name,
                        pd.unit_price,
                        pd.net_weight,
                        pd.total_amount,
                        pd.paid_amount,
                        pd.unpaid_amount,
                        pd.status,
                        pd.remark,
                        pd.created_by,
                        pd.created_at,
                        pd.updated_at,
                        -- 磅单表(pd_weighbills)完整字段
                        wb.id as weighbill_id,
                        wb.weigh_date,
                        wb.delivery_time,
                        wb.weigh_ticket_no,
                        wb.vehicle_no as weighbill_vehicle_no,
                        wb.product_name as weighbill_product_name,
                        wb.gross_weight,
                        wb.tare_weight,
                        wb.net_weight as weighbill_net_weight,
                        wb.unit_price as weighbill_unit_price,
                        wb.total_amount as weighbill_total_amount,
                        wb.weighbill_image,
                        wb.ocr_status,
                        wb.is_manual_corrected,
                        wb.payment_schedule_date,
                        wb.uploader_id as weighbill_uploader_id,
                        wb.uploader_name as weighbill_uploader_name,
                        wb.uploaded_at as weighbill_uploaded_at,
                        -- 销售台账/报货订单(pd_deliveries)完整字段
                        d.id as delivery_id,
                        d.report_date,
                        d.warehouse,
                        d.target_factory_id,
                        d.target_factory_name,
                        d.quantity as delivery_quantity,
                        d.vehicle_no as delivery_vehicle_no,
                        d.driver_name,
                        d.driver_phone,
                        d.driver_id_card,
                        d.has_delivery_order,
                        d.delivery_order_image,
                        d.source_type,
                        d.shipper,
                        d.payee,
                        d.service_fee,
                        d.contract_no as delivery_contract_no,
                        d.contract_unit_price,
                        d.total_amount as delivery_total_amount,
                        d.status as delivery_status,
                        d.uploader_id as delivery_uploader_id,
                        d.uploader_name as delivery_uploader_name,
                        d.uploaded_at as delivery_uploaded_at
                    FROM {PaymentService.TABLE_NAME} pd
                    LEFT JOIN pd_weighbills wb ON pd.sales_order_id = wb.id
                    LEFT JOIN pd_deliveries d ON wb.delivery_id = d.id
                    WHERE {where_sql}
                    ORDER BY pd.created_at DESC
                    LIMIT %s OFFSET %s
                """

                cur.execute(query_sql, tuple(params + [size, offset]))
                rows = cur.fetchall()

                # 处理数据
                items = []
                for row in rows:
                    item = dict(row)

                    # 转换时间字段为字符串
                    time_fields = [
                        'created_at', 'updated_at', 'weigh_date', 'delivery_time',
                        'weighbill_uploaded_at', 'report_date', 'delivery_uploaded_at'
                    ]
                    for field in time_fields:
                        if item.get(field):
                            item[field] = str(item[field])

                    # 添加状态名称
                    item['status_name'] = PaymentStatus(item['status']).name if item.get('status') is not None else None
                    
                    # 计算联单费：如果 has_delivery_order 为 '无'，则联单费为 150
                    has_delivery_order = item.get('has_delivery_order')
                    if has_delivery_order == '无':
                        item['delivery_fee'] = 150.0
                    else:
                        # 有联单时，使用 service_fee 字段，如果没有则默认为 0
                        item['delivery_fee'] = float(item.get('service_fee') or 0)

                    items.append(item)

                return {
                    "total": total,
                    "page": page,
                    "size": size,
                    "items": items
                }
            

    @staticmethod
    def get_payment_detail(payment_id: int) -> Optional[Dict[str, Any]]:
        """
        获取收款明细详情（包含回款记录）
        
        Args:
            payment_id: 收款明细ID
            
        Returns:
            收款明细详情，包含回款记录列表
        """
        with get_conn() as conn:
            with conn.cursor() as cur:
                # 查询收款明细主表
                select_sql = build_dynamic_select(
                    cur,
                    PaymentService.TABLE_NAME,
                    where_clause="id=%s"
                )
                cur.execute(select_sql, (payment_id,))
                detail = cur.fetchone()
                
                if not detail:
                    return None
                
                detail = dict(detail)
                
                # 添加状态名称
                detail['status_name'] = PaymentStatus(detail['status']).name if detail.get('status') is not None else None
                
                # 转换时间字段
                time_fields = ['created_at', 'updated_at']
                for field in time_fields:
                    if detail.get(field):
                        detail[field] = str(detail[field])
                
                # 查询关联的磅单和销售台账信息（复用list_payment_details的关联逻辑）
                query_sql = f"""
                    SELECT 
                        wb.id as weighbill_id,
                        wb.weigh_date,
                        wb.delivery_time,
                        wb.weigh_ticket_no,
                        wb.vehicle_no as weighbill_vehicle_no,
                        wb.product_name as weighbill_product_name,
                        wb.gross_weight,
                        wb.tare_weight,
                        wb.net_weight as weighbill_net_weight,
                        wb.unit_price as weighbill_unit_price,
                        wb.total_amount as weighbill_total_amount,
                        wb.weighbill_image,
                        wb.ocr_status,
                        wb.is_manual_corrected,
                        wb.payment_schedule_date,
                        wb.uploader_id as weighbill_uploader_id,
                        wb.uploader_name as weighbill_uploader_name,
                        wb.uploaded_at as weighbill_uploaded_at,
                        d.id as delivery_id,
                        d.report_date,
                        d.warehouse,
                        d.target_factory_id,
                        d.target_factory_name,
                        d.quantity as delivery_quantity,
                        d.vehicle_no as delivery_vehicle_no,
                        d.driver_name,
                        d.driver_phone,
                        d.driver_id_card,
                        d.has_delivery_order,
                        d.delivery_order_image,
                        d.source_type,
                        d.shipper,
                        d.payee,
                        d.service_fee,
                        d.contract_no as delivery_contract_no,
                        d.contract_unit_price,
                        d.total_amount as delivery_total_amount,
                        d.status as delivery_status,
                        d.uploader_id as delivery_uploader_id,
                        d.uploader_name as delivery_uploader_name,
                        d.uploaded_at as delivery_uploaded_at
                    FROM {PaymentService.TABLE_NAME} pd
                    LEFT JOIN sales_orders so ON pd.sales_order_id = so.id
                    LEFT JOIN pd_weighbills wb ON so.id = wb.sales_order_id
                    LEFT JOIN pd_deliveries d ON wb.delivery_id = d.id
                    WHERE pd.id = %s
                    LIMIT 1
                """
                cur.execute(query_sql, (payment_id,))
                extra_info = cur.fetchone()
                
                if extra_info:
                    extra_info = dict(extra_info)
                    # 转换时间字段
                    time_fields = ['weigh_date', 'delivery_time', 'weighbill_uploaded_at', 
                                  'report_date', 'delivery_uploaded_at']
                    for field in time_fields:
                        if extra_info.get(field):
                            extra_info[field] = str(extra_info[field])
                    
                    # 计算联单费
                    has_delivery_order = extra_info.get('has_delivery_order')
                    if has_delivery_order == '无':
                        extra_info['delivery_fee'] = 150.0
                    else:
                        extra_info['delivery_fee'] = float(extra_info.get('service_fee') or 0)
                    
                    detail.update(extra_info)
                
                # 查询回款记录
                records_sql = f"""
                    SELECT 
                        id,
                        payment_amount,
                        payment_stage,
                        payment_date,
                        payment_method,
                        transaction_no,
                        remark,
                        created_at
                    FROM {PaymentService.RECORD_TABLE}
                    WHERE payment_detail_id = %s
                    ORDER BY payment_date DESC, created_at DESC
                """
                cur.execute(records_sql, (payment_id,))
                records = cur.fetchall()
                
                payment_records = []
                for record in records:
                    rec = dict(record)
                    rec['payment_stage_name'] = PaymentStage(rec['payment_stage']).name if rec.get('payment_stage') is not None else None
                    rec['payment_date'] = str(rec['payment_date']) if rec.get('payment_date') else None
                    rec['created_at'] = str(rec['created_at']) if rec.get('created_at') else None
                    payment_records.append(rec)
                
                detail['payment_records'] = payment_records
                detail['payment_count'] = len(payment_records)
                
                return detail

    @staticmethod
    def update_payment_detail(
        payment_id: int,
        smelter_name: Optional[str] = None,
        contract_no: Optional[str] = None,
        material_name: Optional[str] = None,
        remark: Optional[str] = None,
        updated_by: Optional[int] = None
    ) -> bool:
        """
        更新收款明细基础信息
        
        Args:
            payment_id: 收款明细ID
            smelter_name: 冶炼厂名称
            contract_no: 合同编号
            material_name: 物料名称
            remark: 备注
            updated_by: 更新人ID
            
        Returns:
            是否更新成功
            
        Raises:
            ValueError: 收款明细不存在
        """
        with get_conn() as conn:
            with conn.cursor() as cur:
                # 检查收款明细是否存在
                cur.execute(
                    f"SELECT id, status FROM {PaymentService.TABLE_NAME} WHERE id=%s",
                    (payment_id,)
                )
                existing = cur.fetchone()
                
                if not existing:
                    raise ValueError("收款明细不存在")
                
                # 如果已结清，限制修改
                if existing['status'] == PaymentStatus.PAID:
                    # 只允许修改备注
                    if smelter_name or contract_no or material_name:
                        raise ValueError("已结清的收款明细只允许修改备注")
                
                # 动态构建更新字段
                update_fields = []
                params = []
                
                if smelter_name is not None:
                    update_fields.append("smelter_name = %s")
                    params.append(smelter_name)
                
                if contract_no is not None:
                    update_fields.append("contract_no = %s")
                    params.append(contract_no)
                
                if material_name is not None:
                    update_fields.append("material_name = %s")
                    params.append(material_name)
                
                if remark is not None:
                    update_fields.append("remark = %s")
                    params.append(remark)
                
                if not update_fields:
                    return True  # 没有需要更新的字段
                
                update_fields.append("updated_at = %s")
                params.append(datetime.now())
                
                params.append(payment_id)
                
                update_sql = f"""
                    UPDATE {_quote_identifier(PaymentService.TABLE_NAME)}
                    SET {', '.join(update_fields)}
                    WHERE id = %s
                """
                
                cur.execute(update_sql, tuple(params))
                conn.commit()
                
                logger.info(f"更新收款明细成功: ID={payment_id}")
                return True

    @staticmethod
    def delete_payment_detail(payment_id: int) -> bool:
        """
        删除收款明细
        
        Args:
            payment_id: 收款明细ID
            
        Returns:
            是否删除成功
            
        Raises:
            ValueError: 收款明细不存在或已有回款记录无法删除
        """
        with get_conn() as conn:
            with conn.cursor() as cur:
                # 检查收款明细是否存在
                cur.execute(
                    f"SELECT id, paid_amount, status FROM {PaymentService.TABLE_NAME} WHERE id=%s",
                    (payment_id,)
                )
                existing = cur.fetchone()
                
                if not existing:
                    raise ValueError("收款明细不存在")
                
                # 检查是否有回款记录
                if existing['paid_amount'] > 0 or existing['status'] != PaymentStatus.UNPAID:
                    raise ValueError("已有回款记录的明细无法删除，请先删除回款记录")
                
                # 检查是否存在关联的回款记录表记录
                cur.execute(
                    f"SELECT COUNT(*) as count FROM {PaymentService.RECORD_TABLE} WHERE payment_detail_id=%s",
                    (payment_id,)
                )
                record_count = cur.fetchone()['count']
                
                if record_count > 0:
                    raise ValueError(f"存在{record_count}条回款记录，无法删除收款明细")
                
                # 执行删除
                delete_sql = f"DELETE FROM {_quote_identifier(PaymentService.TABLE_NAME)} WHERE id = %s"
                cur.execute(delete_sql, (payment_id,))
                conn.commit()
                
                logger.info(f"删除收款明细成功: ID={payment_id}")
                return True

    @staticmethod
    def get_contract_shipping_progress(
        contract_no: Optional[str] = None,
        smelter_name: Optional[str] = None,
        page: int = 1,
        size: int = 20
    ) -> Dict[str, Any]:
        """
        获取合同发运进度列表
        统计每个合同的车数、吨数、已运/剩余情况
        """
        with get_conn() as conn:
            with conn.cursor() as cur:
                # 构建WHERE条件
                where_clauses = ["1=1"]
                params = []
                
                if contract_no:
                    where_clauses.append("pd.contract_no LIKE %s")
                    params.append(f"%{contract_no}%")
                
                if smelter_name:
                    where_clauses.append("pd.smelter_name LIKE %s")
                    params.append(f"%{smelter_name}%")
                
                where_sql = " AND ".join(where_clauses)
                
                # 查询总数
                count_sql = f"""
                    SELECT COUNT(DISTINCT pd.contract_no) as total 
                    FROM {PaymentService.TABLE_NAME} pd
                    WHERE {where_sql}
                """
                cur.execute(count_sql, tuple(params))
                total = cur.fetchone()["total"]
                
                # 查询合同发运进度
                offset = (page - 1) * size
                query_sql = f"""
                    SELECT 
                        pd.contract_no,
                        pd.smelter_name,
                        COUNT(DISTINCT pd.id) as total_orders,
                        SUM(pd.net_weight) as planned_total_weight,
                        COUNT(DISTINCT wb.id) as shipped_vehicles,
                        SUM(CASE WHEN wb.id IS NOT NULL THEN wb.net_weight ELSE 0 END) as shipped_weight,
                        SUM(pd.net_weight) - SUM(CASE WHEN wb.id IS NOT NULL THEN wb.net_weight ELSE 0 END) as remaining_weight,
                        MAX(wb.weigh_date) as last_ship_date
                    FROM {PaymentService.TABLE_NAME} pd
                    LEFT JOIN sales_orders so ON pd.sales_order_id = so.id
                    LEFT JOIN pd_weighbills wb ON so.id = wb.sales_order_id
                    WHERE {where_sql}
                    GROUP BY pd.contract_no, pd.smelter_name
                    ORDER BY MAX(pd.created_at) DESC
                    LIMIT %s OFFSET %s
                """
                
                cur.execute(query_sql, tuple(params + [size, offset]))
                rows = cur.fetchall()
                
                items = []
                for row in rows:
                    item = dict(row)
                    planned_weight = float(item.get('planned_total_weight') or 0)
                    shipped_weight = float(item.get('shipped_weight') or 0)
                    shipped_vehicles = int(item.get('shipped_vehicles') or 0)
                    remaining_weight = planned_weight - shipped_weight
                    
                    # 估算剩余车数
                    if shipped_vehicles > 0 and shipped_weight > 0:
                        avg_weight_per_vehicle = shipped_weight / shipped_vehicles
                        remaining_vehicles = int(remaining_weight / avg_weight_per_vehicle) if avg_weight_per_vehicle > 0 else 0
                    else:
                        remaining_vehicles = int(remaining_weight / 30) if remaining_weight > 0 else 0
                    
                    total_vehicles = shipped_vehicles + remaining_vehicles
                    
                    items.append({
                        "contract_no": item["contract_no"],
                        "smelter_name": item["smelter_name"],
                        "total_vehicles": total_vehicles,
                        "planned_total_weight": round(planned_weight, 2),
                        "shipped_vehicles": shipped_vehicles,
                        "remaining_vehicles": remaining_vehicles,
                        "shipped_weight": round(shipped_weight, 2),
                        "remaining_weight": round(remaining_weight, 2),
                        "last_ship_date": str(item["last_ship_date"]) if item.get("last_ship_date") else None,
                        "progress_rate": round(shipped_weight / planned_weight * 100, 2) if planned_weight > 0 else 0
                    })
                
                return {
                    "total": total,
                    "page": page,
                    "size": size,
                    "items": items
                }

    @staticmethod
    def get_contract_payment_summary(
        contract_no: Optional[str] = None,
        smelter_name: Optional[str] = None,
        status: Optional[int] = None,
        page: int = 1,
        size: int = 20
    ) -> Dict[str, Any]:
        """
        获取合同回款汇总列表（按合同编号分组）
        """
        with get_conn() as conn:
            with conn.cursor() as cur:
                where_clauses = ["1=1"]
                params = []
                
                if contract_no:
                    where_clauses.append("pd.contract_no LIKE %s")
                    params.append(f"%{contract_no}%")
                
                if smelter_name:
                    where_clauses.append("pd.smelter_name LIKE %s")
                    params.append(f"%{smelter_name}%")
                
                if status is not None:
                    where_clauses.append("pd.status = %s")
                    params.append(status)
                
                where_sql = " AND ".join(where_clauses)
                
                count_sql = f"""
                    SELECT COUNT(DISTINCT pd.contract_no) as total 
                    FROM {PaymentService.TABLE_NAME} pd
                    WHERE {where_sql}
                """
                cur.execute(count_sql, tuple(params))
                total = cur.fetchone()["total"]
                
                offset = (page - 1) * size
                query_sql = f"""
                    SELECT 
                        pd.contract_no,
                        pd.smelter_name,
                        SUM(pd.total_amount) as total_receivable,
                        SUM(pd.paid_amount) as total_received,
                        SUM(pd.unpaid_amount) as total_unreceived,
                        COUNT(DISTINCT pd.id) as order_count,
                        SUM(CASE WHEN pd.status = 0 THEN 1 ELSE 0 END) as unpaid_count,
                        SUM(CASE WHEN pd.status = 1 THEN 1 ELSE 0 END) as partial_count,
                        SUM(CASE WHEN pd.status = 2 THEN 1 ELSE 0 END) as paid_count,
                        SUM(CASE WHEN pd.status = 3 THEN 1 ELSE 0 END) as overpaid_count,
                        MAX(pr.payment_date) as last_payment_date
                    FROM {PaymentService.TABLE_NAME} pd
                    LEFT JOIN {PaymentService.RECORD_TABLE} pr ON pd.id = pr.payment_detail_id
                    WHERE {where_sql}
                    GROUP BY pd.contract_no, pd.smelter_name
                    ORDER BY SUM(pd.total_amount) DESC
                    LIMIT %s OFFSET %s
                """
                
                cur.execute(query_sql, tuple(params + [size, offset]))
                rows = cur.fetchall()
                
                items = []
                for row in rows:
                    item = dict(row)
                    total_receivable = float(item.get('total_receivable') or 0)
                    total_received = float(item.get('total_received') or 0)
                    total_unreceived = float(item.get('total_unreceived') or 0)
                    
                    # 确定合同整体回款状态
                    order_count = int(item.get('order_count') or 0)
                    unpaid_count = int(item.get('unpaid_count') or 0)
                    paid_count = int(item.get('paid_count') or 0)
                    overpaid_count = int(item.get('overpaid_count') or 0)
                    
                    if unpaid_count == order_count:
                        contract_status = 0
                        contract_status_name = "未回款"
                    elif paid_count == order_count:
                        contract_status = 2
                        contract_status_name = "已结清"
                    elif overpaid_count > 0:
                        contract_status = 3
                        contract_status_name = "超额回款"
                    else:
                        contract_status = 1
                        contract_status_name = "部分回款"
                    
                    items.append({
                        "contract_no": item["contract_no"],
                        "smelter_name": item["smelter_name"],
                        "order_count": order_count,
                        "total_receivable": round(total_receivable, 2),
                        "total_received": round(total_received, 2),
                        "total_unreceived": round(total_unreceived, 2),
                        "collection_rate": round(total_received / total_receivable * 100, 2) if total_receivable > 0 else 0,
                        "contract_status": contract_status,
                        "contract_status_name": contract_status_name,
                        "status_breakdown": {
                            "unpaid": unpaid_count,
                            "partial": int(item.get("partial_count") or 0),
                            "paid": paid_count,
                            "overpaid": overpaid_count
                        },
                        "last_payment_date": str(item["last_payment_date"]) if item.get("last_payment_date") else None
                    })
                
                return {
                    "total": total,
                    "page": page,
                    "size": size,
                    "items": items
                }

    @staticmethod
    def get_contract_payment_details(
        contract_no: str,
        page: int = 1,
        size: int = 20
    ) -> Dict[str, Any]:
        """
        获取单个合同的回款明细列表
        """
        if not contract_no:
            raise ValueError("合同编号不能为空")
        
        with get_conn() as conn:
            with conn.cursor() as cur:
                # 查询合同基本信息
                contract_sql = f"""
                    SELECT DISTINCT
                        pd.contract_no,
                        pd.smelter_name,
                        SUM(pd.total_amount) as contract_total,
                        SUM(pd.paid_amount) as contract_paid,
                        SUM(pd.unpaid_amount) as contract_unpaid
                    FROM {PaymentService.TABLE_NAME} pd
                    WHERE pd.contract_no = %s
                    GROUP BY pd.contract_no, pd.smelter_name
                """
                cur.execute(contract_sql, (contract_no,))
                contract_info = cur.fetchone()
                
                if not contract_info:
                    raise ValueError("合同不存在")
                
                # 查询该合同下的所有收款明细
                where_sql = "pd.contract_no = %s"
                params = [contract_no]
                
                count_sql = f"""
                    SELECT COUNT(*) as total 
                    FROM {PaymentService.TABLE_NAME} pd
                    WHERE {where_sql}
                """
                cur.execute(count_sql, tuple(params))
                total = cur.fetchone()["total"]
                
                offset = (page - 1) * size
                query_sql = f"""
                    SELECT 
                        pd.id,
                        pd.sales_order_id,
                        pd.material_name,
                        pd.unit_price,
                        pd.net_weight,
                        pd.total_amount,
                        pd.paid_amount,
                        pd.unpaid_amount,
                        pd.status,
                        pd.remark,
                        pd.created_at,
                        wb.weigh_ticket_no,
                        wb.weigh_date,
                        wb.net_weight as shipped_weight,
                        (SELECT COUNT(*) FROM {PaymentService.RECORD_TABLE} pr WHERE pr.payment_detail_id = pd.id) as payment_record_count
                    FROM {PaymentService.TABLE_NAME} pd
                    LEFT JOIN sales_orders so ON pd.sales_order_id = so.id
                    LEFT JOIN pd_weighbills wb ON so.id = wb.sales_order_id
                    WHERE {where_sql}
                    ORDER BY pd.created_at DESC
                    LIMIT %s OFFSET %s
                """
                
                cur.execute(query_sql, tuple(params + [size, offset]))
                rows = cur.fetchall()
                
                items = []
                for row in rows:
                    item = dict(row)
                    item['status_name'] = PaymentStatus(item['status']).name if item.get('status') is not None else None
                    item['created_at'] = str(item['created_at']) if item.get('created_at') else None
                    item['weigh_date'] = str(item['weigh_date']) if item.get('weigh_date') else None
                    items.append(item)
                
                # 查询该合同下的所有回款记录
                records_sql = f"""
                    SELECT 
                        pr.id,
                        pr.payment_detail_id,
                        pr.payment_amount,
                        pr.payment_stage,
                        pr.payment_date,
                        pr.payment_method,
                        pr.transaction_no,
                        pr.remark,
                        pr.created_at
                    FROM {PaymentService.RECORD_TABLE} pr
                    INNER JOIN {PaymentService.TABLE_NAME} pd ON pr.payment_detail_id = pd.id
                    WHERE pd.contract_no = %s
                    ORDER BY pr.payment_date DESC, pr.created_at DESC
                """
                cur.execute(records_sql, (contract_no,))
                records = cur.fetchall()
                
                payment_records = []
                for record in records:
                    rec = dict(record)
                    rec['payment_stage_name'] = PaymentStage(rec['payment_stage']).name if rec.get('payment_stage') is not None else None
                    rec['payment_date'] = str(rec['payment_date']) if rec.get('payment_date') else None
                    rec['created_at'] = str(rec['created_at']) if rec.get('created_at') else None
                    payment_records.append(rec)
                
                return {
                    "contract_info": {
                        "contract_no": contract_info["contract_no"],
                        "smelter_name": contract_info["smelter_name"],
                        "total_receivable": float(contract_info["contract_total"]),
                        "total_received": float(contract_info["contract_paid"]),
                        "total_unreceived": float(contract_info["contract_unpaid"]),
                        "collection_rate": round(float(contract_info["contract_paid"]) / float(contract_info["contract_total"]) * 100, 2) if float(contract_info["contract_total"]) > 0 else 0
                    },
                    "total_orders": total,
                    "page": page,
                    "size": size,
                    "orders": items,
                    "payment_records": payment_records,
                    "payment_record_count": len(payment_records)
                }