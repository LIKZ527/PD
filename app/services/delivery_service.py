"""
销售台账/报货订单服务
"""
import logging
import os
import re
import shutil
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime

from app.services.contract_service import get_conn
from app.services.customer_service import CustomerService

logger = logging.getLogger(__name__)

# 使用绝对路径，避免工作目录变化导致的问题
UPLOAD_DIR = Path(__file__).parent.parent.parent / "uploads" / "delivery_orders"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


class DeliveryService:
    """报货订单服务"""

    def _get_upload_status(self, image_path: Optional[str]) -> str:
        if image_path and os.path.exists(image_path):
            return "联单已上传"
        return "联单未上传"

    def _determine_source_type(self, has_order: str, uploaded_by: str = None) -> str:
        """
        确定来源类型
        - 有联单 -> 司机
        - 无联单 -> 公司
        - 公司人员上传有联单 -> 可指定为公司
        """
        if has_order == '有':
            if uploaded_by == '公司':
                return '公司'
            return '司机'
        else:
            return '公司'

    def _calculate_price(self, factory_name: str, product_name: str, quantity: Decimal) -> tuple:
        """
        关联合同计算价格
        返回: (contract_no, unit_price, total_amount)
        """
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id FROM pd_customers WHERE smelter_name = %s",
                        (factory_name,)
                    )
                    customer = cur.fetchone()
                    if not customer:
                        return None, None, None

                    cur.execute("""
                        SELECT contract_no, unit_price 
                        FROM pd_contracts 
                        WHERE smelter_company = %s 
                        AND status = '生效中'
                        AND contract_date <= CURDATE()
                        AND (end_date IS NULL OR end_date >= CURDATE())
                        ORDER BY created_at DESC
                        LIMIT 1
                    """, (factory_name,))

                    contract = cur.fetchone()
                    if not contract:
                        return None, None, None

                    contract_no, unit_price = contract
                    if unit_price and quantity:
                        total = Decimal(str(unit_price)) * Decimal(str(quantity))
                    else:
                        total = None

                    return contract_no, float(unit_price) if unit_price else None, float(total) if total else None

        except Exception as e:
            logger.error(f"计算价格失败: {e}")
            return None, None, None

    def create_delivery(self, data: Dict, image_file: bytes = None, current_user: str = "system") -> Dict[str, Any]:
        """创建报货订单"""
        image_path = None
        temp_file_path = None

        try:
            # 处理来源类型
            has_order = data.get('has_delivery_order', '无')
            uploaded_by = data.get('uploaded_by')
            source_type = self._determine_source_type(has_order, uploaded_by)
            data['source_type'] = source_type

            if not data.get('shipper'):
                data['shipper'] = current_user

            # 计算价格
            contract_no = None
            unit_price = None
            total_amount = None

            if data.get('target_factory_name') and data.get('product_name') and data.get('quantity'):
                contract_no, unit_price, total_amount = self._calculate_price(
                    data['target_factory_name'],
                    data['product_name'],
                    Decimal(str(data['quantity']))
                )

            # 先处理图片到临时位置
            if image_file and has_order == '有':
                file_ext = ".jpg"
                safe_name = re.sub(r'[^\w\-]', '_', str(data.get('vehicle_no', 'unknown')))
                filename = f"order_{safe_name}_{datetime.now().strftime('%Y%m%d%H%M%S')}{file_ext}"
                file_path = UPLOAD_DIR / filename

                # 先保存到临时路径
                temp_file_path = file_path
                with open(file_path, "wb") as f:
                    f.write(image_file)
                image_path = str(file_path)

            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO pd_deliveries 
                        (report_date, warehouse, target_factory_id, target_factory_name,
                         product_name, quantity, vehicle_no, driver_name, driver_phone, driver_id_card,
                         has_delivery_order, delivery_order_image, source_type,
                         shipper, payee, service_fee, contract_no, contract_unit_price, total_amount, status)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        data.get('report_date'),
                        data.get('warehouse'),
                        data.get('target_factory_id'),
                        data.get('target_factory_name'),
                        data.get('product_name'),
                        data.get('quantity'),
                        data.get('vehicle_no'),
                        data.get('driver_name'),
                        data.get('driver_phone'),
                        data.get('driver_id_card'),
                        has_order,
                        image_path,
                        source_type,
                        data.get('shipper'),
                        data.get('payee'),
                        data.get('service_fee', 0),
                        contract_no,
                        unit_price,
                        total_amount,
                        data.get('status', '待确认')
                    ))

                    delivery_id = cur.lastrowid

                    # 数据库成功后再确认文件（已经保存了，这里只是确认）
                    temp_file_path = None

                    return {
                        "success": True,
                        "message": "报货订单创建成功",
                        "data": {
                            "id": delivery_id,
                            "contract_no": contract_no,
                            "contract_unit_price": unit_price,
                            "total_amount": total_amount,
                            "source_type": source_type
                        }
                    }

        except Exception as e:
            # 如果数据库失败，删除已保存的图片
            if temp_file_path and os.path.exists(temp_file_path):
                try:
                    os.remove(temp_file_path)
                except:
                    pass
            logger.error(f"创建报货订单失败: {e}")
            return {"success": False, "error": str(e)}

    def update_delivery(self, delivery_id: int, data: Dict,
                        image_file: bytes = None, delete_image: bool = False) -> Dict[str, Any]:
        """
        更新报货订单（支持修改联单状态和重新上传图片）
        """
        temp_new_file = None
        old_image_to_delete = None

        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT has_delivery_order, delivery_order_image FROM pd_deliveries WHERE id = %s",
                        (delivery_id,)
                    )
                    old = cur.fetchone()
                    if not old:
                        return {"success": False, "error": f"订单ID {delivery_id} 不存在"}

                    old_has_order, old_image_path = old

                    # 处理来源类型
                    has_order = data.get('has_delivery_order', old_has_order)
                    if 'has_delivery_order' in data or 'uploaded_by' in data:
                        uploaded_by = data.get('uploaded_by')
                        data['source_type'] = self._determine_source_type(has_order, uploaded_by)

                    # 处理图片 - 先准备新文件，不立即删除旧文件
                    new_image_path = old_image_path

                    if delete_image and old_image_path:
                        old_image_to_delete = old_image_path
                        new_image_path = None

                    if image_file:
                        # 保存新图片到临时位置（或最终位置，但记录旧文件待删除）
                        safe_name = re.sub(r'[^\w\-]', '_', str(delivery_id))
                        filename = f"delivery_{safe_name}_{datetime.now().strftime('%Y%m%d%H%M%S')}.jpg"
                        file_path = UPLOAD_DIR / filename

                        with open(file_path, "wb") as f:
                            f.write(image_file)

                        temp_new_file = str(file_path)
                        new_image_path = temp_new_file

                        if old_image_path:
                            old_image_to_delete = old_image_path

                    data['delivery_order_image'] = new_image_path

                    # 构建更新SQL
                    fields = [
                        'report_date', 'warehouse', 'target_factory_id', 'target_factory_name',
                        'product_name', 'quantity', 'vehicle_no', 'driver_name', 'driver_phone', 'driver_id_card',
                        'has_delivery_order', 'delivery_order_image', 'source_type',
                        'shipper', 'payee', 'service_fee', 'contract_no', 'contract_unit_price', 'total_amount',
                        'status'
                    ]

                    update_fields = []
                    params = []
                    for f in fields:
                        if f in data:
                            update_fields.append(f"{f} = %s")
                            params.append(data[f])

                    # 关键修复：允许仅删除图片或仅上传图片
                    if not update_fields and not delete_image and not image_file:
                        return {"success": False, "error": "没有要更新的字段"}

                    params.append(delivery_id)
                    sql = f"UPDATE pd_deliveries SET {', '.join(update_fields)} WHERE id = %s"
                    cur.execute(sql, tuple(params))

                    # 数据库更新成功后，再处理文件删除
                    if old_image_to_delete and os.path.exists(old_image_to_delete):
                        try:
                            os.remove(old_image_to_delete)
                        except Exception as e:
                            logger.warning(f"删除旧图片失败: {e}")

                    return {
                        "success": True,
                        "message": "更新成功",
                        "data": {
                            "id": delivery_id,
                            "has_delivery_order": has_order,
                            "delivery_order_image": new_image_path
                        }
                    }

        except Exception as e:
            # 如果数据库失败，删除新上传的临时文件
            if temp_new_file and os.path.exists(temp_new_file):
                try:
                    os.remove(temp_new_file)
                except:
                    pass
            logger.error(f"更新报货订单失败: {e}")
            return {"success": False, "error": str(e)}

    def get_delivery(self, delivery_id: int) -> Optional[Dict]:
        """获取订单详情"""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM pd_deliveries WHERE id = %s", (delivery_id,))
                    row = cur.fetchone()
                    if not row:
                        return None

                    columns = [desc[0] for desc in cur.description]
                    data = dict(zip(columns, row))

                    for key in ['report_date', 'created_at', 'updated_at']:
                        if data.get(key):
                            data[key] = str(data[key])

                    data["delivery_order_upload_status"] = self._get_upload_status(
                        data.get("delivery_order_image")
                    )

                    return data

        except Exception as e:
            logger.error(f"查询订单失败: {e}")
            return None

    def list_deliveries(
            self,
            exact_factory_name: str = None,
            exact_status: str = None,
            exact_vehicle_no: str = None,
            exact_driver_name: str = None,
            exact_driver_phone: str = None,
            fuzzy_keywords: str = None,
            date_from: str = None,
            date_to: str = None,
            page: int = 1,
            page_size: int = 20
    ) -> Dict[str, Any]:
        """查询订单列表"""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    where_clauses = []
                    params = []

                    if exact_factory_name:
                        where_clauses.append("target_factory_name = %s")
                        params.append(exact_factory_name)

                    if exact_status:
                        where_clauses.append("status = %s")
                        params.append(exact_status)

                    if exact_vehicle_no:
                        where_clauses.append("vehicle_no = %s")
                        params.append(exact_vehicle_no)

                    if exact_driver_name:
                        where_clauses.append("driver_name = %s")
                        params.append(exact_driver_name)

                    if exact_driver_phone:
                        where_clauses.append("driver_phone = %s")
                        params.append(exact_driver_phone)

                    if fuzzy_keywords:
                        tokens = [t for t in fuzzy_keywords.split() if t]
                        or_clauses = []
                        for token in tokens:
                            like = f"%{token}%"
                            or_clauses.append(
                                "(vehicle_no LIKE %s OR driver_name LIKE %s OR driver_phone LIKE %s "
                                "OR target_factory_name LIKE %s OR product_name LIKE %s)"
                            )
                            params.extend([like, like, like, like, like])
                        if or_clauses:
                            where_clauses.append("(" + " OR ".join(or_clauses) + ")")

                    if date_from:
                        where_clauses.append("report_date >= %s")
                        params.append(date_from)

                    if date_to:
                        where_clauses.append("report_date <= %s")
                        params.append(date_to)

                    where_sql = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

                    cur.execute(f"SELECT COUNT(*) FROM pd_deliveries {where_sql}", tuple(params))
                    total = cur.fetchone()[0]

                    offset = (page - 1) * page_size
                    cur.execute(f"""
                        SELECT * FROM pd_deliveries 
                        {where_sql}
                        ORDER BY created_at DESC
                        LIMIT %s OFFSET %s
                    """, tuple(params + [page_size, offset]))

                    columns = [desc[0] for desc in cur.description]
                    rows = cur.fetchall()
                    data = []
                    for row in rows:
                        item = dict(zip(columns, row))
                        for key in ['report_date', 'created_at', 'updated_at']:
                            if item.get(key):
                                item[key] = str(item[key])
                        item["delivery_order_upload_status"] = self._get_upload_status(
                            item.get("delivery_order_image")
                        )
                        data.append(item)

                    return {
                        "success": True,
                        "data": data,
                        "total": total,
                        "page": page,
                        "page_size": page_size
                    }

        except Exception as e:
            logger.error(f"查询列表失败: {e}")
            return {"success": False, "error": str(e), "data": [], "total": 0}

    def delete_delivery(self, delivery_id: int) -> Dict[str, Any]:
        """删除订单"""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT delivery_order_image FROM pd_deliveries WHERE id = %s", (delivery_id,))
                    row = cur.fetchone()

                    if row and row[0]:
                        image_path = row[0]
                        cur.execute("DELETE FROM pd_deliveries WHERE id = %s", (delivery_id,))

                        # 数据库删除成功后，再删除文件
                        if os.path.exists(image_path):
                            try:
                                os.remove(image_path)
                            except Exception as e:
                                logger.warning(f"删除图片文件失败: {e}")
                    else:
                        cur.execute("DELETE FROM pd_deliveries WHERE id = %s", (delivery_id,))

                    return {"success": True, "message": "删除成功"}

        except Exception as e:
            logger.error(f"删除订单失败: {e}")
            return {"success": False, "error": str(e)}


_delivery_service = None


def get_delivery_service():
    global _delivery_service
    if _delivery_service is None:
        _delivery_service = DeliveryService()
    return _delivery_service