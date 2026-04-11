from fastapi import APIRouter

from app.intelligent_prediction.api.v1.router import intelligent_prediction_router

from app.api.v1.routes import (
    agent_chat,
    allocation,
    t1_compat,
    balances,
    contracts,
    customers,
    deliveries,
    delivery_contract_prices,
    delivery_plans,
    exception_reports,
    exception_types,
    order_plans,
    payment,
    product_categories,
    weighbills,
)

api_router = APIRouter()
# 挂载在 main 时不加全局 HTTPBearer，与需登录的 v1 接口分离
public_api_router = APIRouter()
public_api_router.include_router(allocation.public_router)

api_router.include_router(agent_chat.router)
api_router.include_router(contracts.router, tags=["合同管理"])
api_router.include_router(customers.router, tags=["客户管理"])
api_router.include_router(deliveries.router, tags=["销售台账/报货订单"])
api_router.include_router(delivery_contract_prices.router)
api_router.include_router(delivery_plans.router, tags=["报货计划"])
api_router.include_router(order_plans.router, tags=["订货计划"])
api_router.include_router(weighbills.router, tags=["磅单管理"])
api_router.include_router(balances.router, tags=["磅单结余管理"])
api_router.include_router(payment.router, tags=["收款明细管理"])
api_router.include_router(product_categories.router, tags=["品类管理"])
api_router.include_router(exception_types.router, tags=["异常审核"])
api_router.include_router(exception_reports.router, tags=["异常审核"])
api_router.include_router(allocation.router, tags=["分配规划"])
api_router.include_router(t1_compat.router)
api_router.include_router(intelligent_prediction_router, tags=["智能预测模块"])

