"""
分配规划路由
支持生成调度计划、查看优化结果、测试数据管理
"""
from datetime import datetime, timedelta
from typing import Optional
import random
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, Body
from pydantic import BaseModel, ConfigDict, Field

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
    """分配计划响应：含排产方案与求解元数据。"""

    model_config = ConfigDict(title="分配计划响应")

    success: bool = Field(True, description="是否成功")
    message: str = Field("调度计划生成成功", description="提示信息")
    plan: dict = Field(
        ...,
        description="排产方案：仓库 → 合同编号 → 冶炼厂 → 日期 → 车数",
    )
    meta: dict = Field(..., description="元数据（求解状态、时间窗口、车数汇总等）")


class ContractStatusResponse(BaseModel):
    """单份生效合同的进度概况。"""

    model_config = ConfigDict(title="合同状态项")

    contract_no: str = Field(..., description="合同编号")
    smelter_company: str = Field(..., description="冶炼厂名称")
    total_quantity: float = Field(..., description="合同总吨位")
    total_trucks: int = Field(..., description="需求总车数")
    delivered_trucks: int = Field(..., description="已发车数（报单条数）")
    remaining_trucks: int = Field(..., description="剩余车数")


class ContractsStatusResponse(BaseModel):
    """生效合同状态列表。"""

    model_config = ConfigDict(title="合同状态列表响应")

    success: bool = Field(True, description="是否成功")
    contracts: list[ContractStatusResponse] = Field(..., description="合同状态列表")


class SetupTestDataRequest(BaseModel):
    """写入分配规划联调用的测试合同/报单/磅单。"""

    model_config = ConfigDict(title="设置测试数据请求")

    num_contracts: int = Field(5, ge=1, le=20, description="要生成的测试合同数量")
    num_deliveries_per_contract: int = Field(
        2, ge=0, le=5, description="每个合同最多生成的报货（销售台账）条数"
    )
    num_weighbills_per_contract: int = Field(
        1, ge=0, le=3, description="每个合同最多生成的磅单条数"
    )
    contract_prefix: str = Field("TEST", description="合同编号前缀，用于区分测试数据")


class SetupTestDataResponse(BaseModel):
    """设置测试数据后的统计结果。"""

    model_config = ConfigDict(title="设置测试数据响应")

    success: bool = Field(True, description="是否成功")
    message: str = Field(..., description="结果说明")
    inserted_contracts: int = Field(..., description="新插入的合同数")
    inserted_deliveries: int = Field(..., description="新插入的报单数")
    inserted_weighbills: int = Field(..., description="新插入的磅单数")


class CleanupTestDataResponse(BaseModel):
    """按前缀清理测试数据后的统计结果。"""

    model_config = ConfigDict(title="清理测试数据响应")

    success: bool = Field(True, description="是否成功")
    message: str = Field(..., description="结果说明")
    deleted_contracts: int = Field(..., description="删除的合同数")
    deleted_deliveries: int = Field(..., description="删除的报单数")
    deleted_weighbills: int = Field(..., description="删除的磅单数")


class WarehousesListResponse(BaseModel):
    """仓库名称列表。"""

    model_config = ConfigDict(title="仓库列表响应")

    success: bool = Field(True, description="是否成功")
    warehouses: list[str] = Field(..., description="仓库名称列表")
    count: int = Field(..., description="仓库数量")


class WarehouseCapacityResponse(BaseModel):
    """各仓库每日最大可发车数（当前为服务层模拟值）。"""

    model_config = ConfigDict(title="仓库日产能响应")

    success: bool = Field(True, description="是否成功")
    daily_capacity: dict[str, int] = Field(
        ...,
        description="仓库名称 → 每日最大车数",
    )


class ActiveContractItemResponse(BaseModel):
    """参与排产的单条合同（已按截至日扣减已发车）。"""

    model_config = ConfigDict(title="生效合同项")

    contract_no: str = Field(..., description="合同编号")
    smelter: str = Field(..., description="冶炼厂")
    total_tons: float = Field(..., description="剩余需求吨位")
    total_trucks: int = Field(..., description="剩余需求车数")
    start_date: str = Field(..., description="合同开始日期")
    end_date: str = Field(..., description="合同结束日期")


class ActiveContractsListResponse(BaseModel):
    """生效合同列表（供排产读取）。"""

    model_config = ConfigDict(title="生效合同列表响应")

    success: bool = Field(True, description="是否成功")
    contracts: list[ActiveContractItemResponse] = Field(..., description="合同列表")
    count: int = Field(..., description="合同条数")


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

@router.post(
    "/test-data/setup",
    summary="写入分配规划测试数据",
    response_description="返回本次插入的合同、报单、磅单数量",
    response_model=SetupTestDataResponse,
)
async def setup_test_data(request: SetupTestDataRequest = Body(...)):
    """
    设置测试数据

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

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"设置测试数据失败: {str(e)}")


@router.post(
    "/test-data/cleanup",
    summary="清理分配规划测试数据",
    response_description="按合同编号前缀删除测试合同及关联报单、磅单",
    response_model=CleanupTestDataResponse,
)
async def cleanup_test_data(
    prefix: str = Query(
        "TEST",
        title="合同编号前缀",
        description="测试合同编号前缀，仅删除合同编号以此前缀开头的数据",
    ),
):
    """
    清理测试数据

    删除所有以指定前缀开头的测试数据:
    - 测试合同
    - 测试报单
    - 测试磅单
    """
    try:
        deleted = _cleanup_test_data(prefix=prefix)

        return CleanupTestDataResponse(
            success=True,
            message=f"测试数据清理成功: 删除{deleted['contracts']}个合同, {deleted['deliveries']}个报单, {deleted['weighbills']}个磅单",
            deleted_contracts=deleted["contracts"],
            deleted_deliveries=deleted["deliveries"],
            deleted_weighbills=deleted["weighbills"]
        )

    except Exception as e:
        print(f"测试预测异常: {e}")


@router.get(
    "/status",
    summary="查询生效合同进度",
    response_description="各合同总车数、已发车、剩余车数",
    response_model=ContractsStatusResponse,
)
async def get_contracts_status():
    """
    每日预测函数（后端定时任务）

    读取真实合同数据并运行预测，保存结果到数据库
    """
    try:
        with _get_db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT contract_no, smelter_company, total_quantity, truck_count
                    FROM pd_contracts
                    WHERE status = '生效中'
                    ORDER BY contract_no
                """)
                rows = cur.fetchall()

                for row in rows:
                    contract_no = row[0]
                    smelter_company = row[1]
                    total_quantity = row[2]
                    truck_count = row[3] or 0

                    cur.execute("""
                        SELECT COUNT(*) as count
                        FROM pd_deliveries
                        WHERE contract_no = %s
                    """, (contract_no,))
                    delivery_row = cur.fetchone()
                    delivered_trucks = delivery_row[0] if delivery_row else 0

                    contracts_status.append(ContractStatusResponse(
                        contract_no=contract_no,
                        smelter_company=smelter_company,
                        total_quantity=total_quantity,
                        total_trucks=truck_count,
                        delivered_trucks=delivered_trucks,
                        remaining_trucks=max(0, truck_count - delivered_trucks)
                    ))

        return ContractsStatusResponse(
            success=True,
            contracts=contracts_status
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取合同状态失败: {str(e)}")


@router.get(
    "/plan",
    summary="生成调度分配计划",
    response_description="线性规划排产结果及 meta 元数据",
    response_model=AllocationPlanResponse,
)
async def generate_allocation_plan(
    window_start: Optional[str] = Query(
        None,
        title="规划窗口起始日",
        description="规划窗口起始日期，格式 YYYY-MM-DD；不传则默认为当天",
    ),
    H: int = Query(
        10,
        ge=1,
        le=30,
        title="规划窗口天数",
        description="从起始日起连续规划的天数，取值 1～30",
    ),
    as_of_date: Optional[str] = Query(
        None,
        title="已发车统计截至日",
        description="计算已发车数时截至该日；不传则与规划窗口起始日相同",
    ),
    include_solver_log: bool = Query(
        False,
        title="返回求解器日志",
        description="为 true 时在求解过程中附带求解器输出（便于排查不可行等问题）",
    ),
):
    """
    生成调度分配计划

    功能:
    - 从数据库读取生效中的合同
    - 统计每个合同的已发车数(从报单和磅单)
    - 动态调整剩余需求
    - 使用线性规划优化调度
    - 最小化各冶炼厂每日到货车数的方差

    返回:
    - plan: {仓库: {合同编号: {冶炼厂: {日期: 车数}}}}
    - meta: 元数据(求解状态、窗口、总车数等)
    """
    try:
        if window_start is None:
            window_start = datetime.now().strftime("%Y-%m-%d")

        if as_of_date is None:
            as_of_date = window_start

        contracts = get_active_contracts(as_of_date=as_of_date)

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


@router.get(
    "/warehouses",
    summary="获取仓库列表",
    response_description="当前启用仓库名称及数量",
    response_model=WarehousesListResponse,
)
async def get_warehouses_list():
    """获取所有仓库列表"""
    try:
        warehouses = get_warehouses()
        return WarehousesListResponse(
            success=True,
            warehouses=warehouses,
            count=len(warehouses),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取仓库列表失败: {str(e)}")


@router.get(
    "/capacity",
    summary="获取各仓库日产能",
    response_description="各仓库每日最大可发车数",
    response_model=WarehouseCapacityResponse,
)
async def get_warehouse_capacity():
    """获取各仓库每日发货能力"""
    try:
        capacity = get_warehouse_daily_capacity()
        return WarehouseCapacityResponse(success=True, daily_capacity=capacity)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取仓库产能失败: {str(e)}")

        predictions, prediction_date, total_trucks = get_predictions(manager_list, smelter_list, contract_list)

@router.get(
    "/contracts",
    summary="获取生效合同列表（排产输入）",
    response_description="已按默认规则扣减已发车的合同需求，供排产使用",
    response_model=ActiveContractsListResponse,
)
async def get_active_contracts_list():
    """获取所有生效中的合同(含已发车调整)"""
    try:
        contracts = get_active_contracts()
        contracts_data = [
            ActiveContractItemResponse(
                contract_no=c.contract_no,
                smelter=c.smelter,
                total_tons=c.total_tons,
                total_trucks=c.total_trucks,
                start_date=c.start_date,
                end_date=c.end_date,
            )
            for c in contracts
        ]
        return ActiveContractsListResponse(
            success=True,
            contracts=contracts_data,
            count=len(contracts),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询预测结果失败: {str(e)}")


@router.post(
    "/test_plan",
    summary="一键联调：造数并生成计划",
    response_description="含测试数据统计、排产 plan 与 meta",
)
async def test_plan(
    num_contracts: int = Query(
        3,
        ge=1,
        le=10,
        title="测试合同数量",
        description="本次插入的测试合同条数",
    ),
    H: int = Query(
        7,
        ge=1,
        le=30,
        title="规划窗口天数",
        description="排产覆盖的天数",
    ),
):
    """
    测试完整流程: 插入测试数据 + 生成调度计划

    自动执行:
    1. 清理旧的TESTPLAN测试数据
    2. 插入新的测试合同
    3. 生成调度计划
    """
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

