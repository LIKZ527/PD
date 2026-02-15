from fastapi import FastAPI
from app.api.v1.api import api_router
from app.core.config import settings
from fastapi.middleware.cors import CORSMiddleware


from api.user.routes import register_routes as register_user_routes


app = FastAPI(
    title="综合管理系统API",
    description="财务管理系统 + 用户中心 + 订单系统 + 商品管理",
    version="1.0.0",
    docs_url="/docs",  # 自定义 docs 路由以支持搜索过滤
    redoc_url="/redoc",  # ReDoc 文档地址
    openapi_url="/openapi.json",  # OpenAPI Schema 地址
    default_response_class=DecimalJSONResponse
)
app.include_router(api_router, prefix="/api/v1")

register_user_routes(app)

@app.get("/healthz")
def health_check() -> dict:
    return {"status": "ok"}
