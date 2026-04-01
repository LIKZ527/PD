"""
分配规划路由
支持生成调度计划、查看优化结果、测试数据管理
"""
from datetime import datetime, timedelta
from typing import Optional
import random
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, Body
from pydantic import BaseModel, Field

from app.services.allocation_service import (
    get_active_contracts,
    get_warehouses,
    get_warehouse_daily_capacity,
    solve_dispatch_plan,
    save_predictions_to_db,
    get_predictions,
    get_filter_options
)
from app.services.contract_service import get_conn


router = APIRouter(prefix="/allocation", tags=["分配规划"])


# ============ 响应模型 ============

class AllocationPlanResponse(BaseModel):
    """分配计划响应"""
    success: bool = True
    message: str = "调度计划生成成功"
    plan: dict  # {仓库: {合同编号: {冶炼厂: {日期: 车数}}}}
    meta: dict  # 元数据


class ContractStatusResponse(BaseModel):
    """合同状态响应"""
    contract_no: str
    smelter_company: str
    total_quantity: float
    total_trucks: int
    delivered_trucks: int
    remaining_trucks: int


class ContractsStatusResponse(BaseModel):
    """合同状态列表响应"""
    success: bool = True
    contracts: list[ContractStatusResponse]


class SetupTestDataRequest(BaseModel):
    """设置测试数据请求"""
    num_contracts: int = Field(5, ge=1, le=20, description="合同数量")
    num_deliveries_per_contract: int = Field(2, ge=0, le=5, description="每个合同的报单数量")
    num_weighbills_per_contract: int = Field(1, ge=0, le=3, description="每个合同的磅单数量")
    contract_prefix: str = Field("TEST", description="合同编号前缀")


class SetupTestDataResponse(BaseModel):
    """设置测试数据响应"""
    success: bool = True
    message: str
    inserted_contracts: int
    inserted_deliveries: int
    inserted_weighbills: int


class CleanupTestDataResponse(BaseModel):
    """清理测试数据响应"""
    success: bool = True
    message: str
    deleted_contracts: int
    deleted_deliveries: int
    deleted_weighbills: int


# ============ 辅助函数 ============

def _get_db_conn():
    """获取数据库连接（兼容旧代码）"""
    return get_conn()


def _setup_warehouses():
    """设置仓库"""
    warehouses = [
        ('河南金铅仓库', '张经理'),
        ('河北仓库', '李经理'),
        ('山东仓库', '王经理'),
        ('山西仓库', '赵经理')
    ]

    with _get_db_conn() as conn:
        with conn.cursor() as cur:
            for name, manager in warehouses:
                try:
                    cur.execute(
                        'INSERT INTO pd_warehouses (warehouse_name, regional_manager, is_active, created_at, updated_at) '
                        'VALUES (%s, %s, 1, NOW(), NOW())',
                        (name, manager)
                    )
                except Exception as e:
                    if 'Duplicate entry' not in str(e):
                        raise


def _insert_test_contracts(num_contracts: int, prefix: str) -> list:
    """插入测试合同"""
    smelters = [
        "河南金利金铅集团有限公司",
        "河北金铅冶炼有限公司",
        "山东再生铅有限公司",
        "山西铅业集团"
    ]
    products = ["电动车", "黑皮", "新能源", "通信", "摩托车"]

    inserted = []

    with _get_db_conn() as conn:
        with conn.cursor() as cur:
            for i in range(num_contracts):
                contract_no = f"{prefix}_{datetime.now().strftime('%Y%m%d')}_{i+1:03d}"
                smelter = random.choice(smelters)
                contract_date = (datetime.now() - timedelta(days=random.randint(0, 1))).date()
                end_date = contract_date + timedelta(days=random.randint(5, 10))
                total_quantity = random.randint(100, 500)
                truck_count = total_quantity // 35

                cur.execute("""
                    INSERT INTO pd_contracts
                    (contract_no, contract_date, end_date, smelter_company,
                     total_quantity, truck_count, arrival_payment_ratio, final_payment_ratio,
                     status, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                """, (
                    contract_no, contract_date, end_date, smelter,
                    total_quantity, truck_count, Decimal("0.9"), Decimal("0.1"), "生效中"
                ))

                contract_id = cur.lastrowid

                num_products = random.randint(1, 3)
                selected_products = random.sample(products, num_products)
                for j, product_name in enumerate(selected_products):
                    unit_price = Decimal(str(random.randint(15000, 18000))) + Decimal("0.00")
                    cur.execute("""
                        INSERT INTO pd_contract_products
                        (contract_id, product_name, unit_price, sort_order)
                        VALUES (%s, %s, %s, %s)
                    """, (contract_id, product_name, unit_price, j))

                inserted.append({
                    "contract_no": contract_no,
                    "smelter": smelter,
                    "total_quantity": total_quantity,
                    "truck_count": truck_count,
                    "contract_date": contract_date,
                    "end_date": end_date
                })

    return inserted


def _insert_test_deliveries(contracts: list, max_per_contract: int) -> int:
    """插入测试报单"""
    statuses = ['已发货', '已装车', '在途', '已签收']
    inserted = 0

    with _get_db_conn() as conn:
        with conn.cursor() as cur:
            for contract in contracts:
                contract_no = contract["contract_no"]
                smelter = contract["smelter"]
                truck_count = contract["truck_count"]

                if not truck_count:
                    continue

                num_delivered = random.randint(0, min(max_per_contract, truck_count))

                for i in range(num_delivered):
                    cur.execute('''
                        INSERT INTO pd_deliveries
                        (contract_no, status, warehouse, target_factory_name, product_name,
                         quantity, vehicle_no, driver_name, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                    ''', (
                        contract_no, random.choice(statuses),
                        random.choice(['河南金铅仓库', '河北仓库']),
                        smelter, '电动车', 35.0,
                        f'豫A{random.randint(10000,99999)}', '测试司机'
                    ))
                    inserted += 1

    return inserted


def _insert_test_weighbills(contracts: list, max_per_contract: int) -> int:
    """插入测试磅单"""
    inserted = 0

    with _get_db_conn() as conn:
        with conn.cursor() as cur:
            for contract in contracts:
                contract_no = contract["contract_no"]

                num_weighbills = random.randint(0, max_per_contract)

                for i in range(num_weighbills):
                    cur.execute('''
                        INSERT INTO pd_weighbills
                        (weigh_date, delivery_time, contract_no, vehicle_no,
                         product_name, gross_weight, tare_weight, net_weight,
                         unit_price, total_amount, upload_status, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                    ''', (
                        datetime.now().date(), datetime.now(), contract_no,
                        f'豫B{random.randint(10000,99999)}', '电动车',
                        random.randint(40, 50), random.randint(10, 15),
                        random.randint(30, 35), 16000.0,
                        random.randint(30, 35) * 16000.0, '已上传'
                    ))
                    inserted += 1

    return inserted


def _cleanup_test_data(prefix: str = "TEST") -> dict:
    """清理测试数据"""
    deleted = {"contracts": 0, "deliveries": 0, "weighbills": 0}

    with _get_db_conn() as conn:
        with conn.cursor() as cur:
            # 删除测试磅单
            cur.execute("DELETE FROM pd_weighbills WHERE contract_no LIKE %s", (f'{prefix}%',))
            deleted["weighbills"] = cur.rowcount

            # 删除测试报单
            cur.execute("DELETE FROM pd_deliveries WHERE contract_no LIKE %s", (f'{prefix}%',))
            deleted["deliveries"] = cur.rowcount

            # 删除测试合同品种
            cur.execute("""
                DELETE FROM pd_contract_products
                WHERE contract_id IN (
                    SELECT id FROM pd_contracts WHERE contract_no LIKE %s
                )
            """, (f'{prefix}%',))

            # 删除测试合同
            cur.execute("DELETE FROM pd_contracts WHERE contract_no LIKE %s", (f'{prefix}%',))
            deleted["contracts"] = cur.rowcount

    return deleted


def _save_predictions_to_db(plan: dict, prediction_date: str, is_test: bool = False):
    """保存预测结果到数据库"""
    save_predictions_to_db(plan, prediction_date, is_test)


def run_test_prediction(num_contracts: int = 5, H: int = 10):
    """
    测试预测函数（后端定时任务）

    生成测试数据并运行预测，保存结果到数据库
    """
    try:
        prefix = "TESTPLAN"
        _cleanup_test_data(prefix=prefix)
        _setup_warehouses()
        _insert_test_contracts(num_contracts=num_contracts, prefix=prefix)

        window_start = datetime.now().strftime("%Y-%m-%d")
        contracts = get_active_contracts(as_of_date=window_start)
        warehouses = get_warehouses()
        daily_cap = get_warehouse_daily_capacity()
        window_end = (datetime.now() + timedelta(days=H - 1)).strftime("%Y-%m-%d")

        plan, status = solve_dispatch_plan(
            contracts=contracts,
            warehouses=warehouses,
            daily_cap=daily_cap,
            window_start=window_start,
            window_end=window_end,
            solver_msg=False
        )

        if status in ("Optimal", "Feasible"):
            _save_predictions_to_db(plan, window_start, is_test=True)
            print(f"测试预测完成: {window_start}, 状态: {status}")
        else:
            print(f"测试预测失败: {status}")

    except Exception as e:
        print(f"测试预测异常: {e}")


def run_daily_prediction(H: int = 10):
    """
    每日预测函数（后端定时任务）

    读取真实合同数据并运行预测，保存结果到数据库
    """
    try:
        window_start = datetime.now().strftime("%Y-%m-%d")
        contracts = get_active_contracts(as_of_date=window_start)

        if not contracts:
            print("无生效中的合同，跳过预测")
            return

        warehouses = get_warehouses()
        daily_cap = get_warehouse_daily_capacity()
        window_end = (datetime.now() + timedelta(days=H - 1)).strftime("%Y-%m-%d")

        plan, status = solve_dispatch_plan(
            contracts=contracts,
            warehouses=warehouses,
            daily_cap=daily_cap,
            window_start=window_start,
            window_end=window_end,
            solver_msg=False
        )

        if status in ("Optimal", "Feasible"):
            _save_predictions_to_db(plan, window_start, is_test=False)
            print(f"每日预测完成: {window_start}, 状态: {status}")
        else:
            print(f"每日预测失败: {status}")

    except Exception as e:
        print(f"每日预测异常: {e}")


# ============ 路由 ============

@router.get("/predictions")
async def get_predictions_route(
    regional_managers: Optional[str] = Query(None, description="大区经理列表，逗号分隔"),
    smelters: Optional[str] = Query(None, description="冶炼厂列表，逗号分隔"),
    contract_nos: Optional[str] = Query(None, description="合同编号列表，逗号分隔")
):
    """查询预测结果"""
    try:
        manager_list = [m.strip() for m in regional_managers.split(',')] if regional_managers else None
        smelter_list = [s.strip() for s in smelters.split(',')] if smelters else None
        contract_list = [c.strip() for c in contract_nos.split(',')] if contract_nos else None

        predictions, prediction_date, total_trucks = get_predictions(manager_list, smelter_list, contract_list)

        return {
            "success": True,
            "predictions": predictions,
            "meta": {
                "total_trucks": total_trucks,
                "prediction_date": prediction_date,
                "filtered_managers": manager_list or [],
                "filtered_smelters": smelter_list or [],
                "filtered_contracts": contract_list or []
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询预测结果失败: {str(e)}")


@router.get("/filter-options")
async def get_filter_options_route():
    """获取筛选选项"""
    try:
        managers, smelters, contracts = get_filter_options()
        return {
            "success": True,
            "regional_managers": managers,
            "smelters": smelters,
            "contracts": contracts
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取筛选选项失败: {str(e)}")

