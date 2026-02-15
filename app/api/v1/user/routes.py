from fastapi import HTTPException, APIRouter, Depends
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from enum import IntEnum

from core.database import get_conn
from core.logging import get_logger
from core.table_access import build_dynamic_select, _quote_identifier
from core.auth import create_access_token, get_current_user
from services.pd_auth_service import (
    AuthService, 
    UserStatus, 
    UserRole,
    verify_pwd,
    hash_pwd
)

logger = get_logger(__name__)


# ========== Pydantic 模型定义 ==========

class LoginReq(BaseModel):
    account: str = Field(..., description="登录账号")
    password: str = Field(..., description="密码")


class LoginResp(BaseModel):
    uid: int
    token: str
    expires_in: int
    user: dict


class CreateUserReq(BaseModel):
    name: str = Field(..., description="用户姓名")
    account: str = Field(..., description="登录账号")
    password: str = Field(..., min_length=6, description="初始密码")
    role: str = Field(..., description="角色：管理员/大区经理/自营库管理/财务/会计")
    phone: Optional[str] = Field(None, description="手机号")
    email: Optional[str] = Field(None, description="邮箱")


class UpdateUserReq(BaseModel):
    name: Optional[str] = Field(None, description="用户姓名")
    phone: Optional[str] = Field(None, description="手机号")
    email: Optional[str] = Field(None, description="邮箱")
    role: Optional[str] = Field(None, description="角色")


class UpdatePwdReq(BaseModel):
    old_password: str = Field(..., description="旧密码")
    new_password: str = Field(..., min_length=6, description="新密码")


class ResetPwdReq(BaseModel):
    admin_key: str = Field(..., description="后台管理密钥")
    new_password: str = Field(..., min_length=6, description="新密码")


class UserListQuery(BaseModel):
    page: int = Field(1, ge=1, description="页码")
    size: int = Field(20, ge=1, le=100, description="每页数量")
    role: Optional[str] = Field(None, description="按角色筛选")
    keyword: Optional[str] = Field(None, description="关键词搜索（姓名/账号）")


class UserResp(BaseModel):
    id: int
    name: str
    account: str
    role: str
    phone: Optional[str]
    email: Optional[str]
    status: int
    created_at: datetime
    updated_at: datetime


# ========== 路由定义 ==========

router = APIRouter(tags=["PD用户认证"])


def register_pd_auth_routes(app):
    """注册用户认证路由到主应用"""
    app.include_router(router, prefix="/api/v1")


def _err(msg: str, code: int = 400):
    raise HTTPException(status_code=code, detail=msg)


def check_admin_permission(current_user: dict):
    """检查是否为管理员"""
    if current_user.get("role") != "管理员":
        raise HTTPException(status_code=403, detail="仅管理员可操作")


def check_manager_permission(current_user: dict):
    """检查是否为大区经理及以上权限"""
    if current_user.get("role") not in ["管理员", "大区经理"]:
        raise HTTPException(status_code=403, detail="权限不足")


# ========== 认证接口 ==========

@router.post("/auth/login", summary="用户登录", response_model=LoginResp)
def login(body: LoginReq):
    """
    用户登录接口
    - 验证账号密码
    - 检查用户状态（冻结/注销）
    - 返回 JWT Token
    """
    try:
        user = AuthService.authenticate(body.account, body.password)
        
        # 检查用户状态
        status = user.get("status", 0)
        if status == UserStatus.FROZEN:
            raise HTTPException(status_code=403, detail="账号已冻结，请联系管理员")
        if status == UserStatus.DELETED:
            raise HTTPException(status_code=403, detail="账号已注销")
        
        # 创建 Token
        token = create_access_token(
            user_id=user["id"],
            role=user["role"],
            token_type="pd_auth"
        )
        
        logger.info(f"用户登录成功: {user['account']} (ID: {user['id']})")
        
        return LoginResp(
            uid=user["id"],
            token=token,
            expires_in=3600 * 24,  # 24小时
            user={
                "id": user["id"],
                "name": user["name"],
                "account": user["account"],
                "role": user["role"]
            }
        )
        
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        logger.exception("登录异常")
        raise HTTPException(status_code=500, detail="登录失败")


@router.post("/auth/logout", summary="用户登出")
def logout(current_user: dict = Depends(get_current_user)):
    """
    用户登出（前端清除token即可，后端可加入黑名单）
    """
    logger.info(f"用户登出: {current_user.get('account')}")
    return {"msg": "登出成功"}


@router.post("/auth/refresh", summary="刷新Token")
def refresh_token(current_user: dict = Depends(get_current_user)):
    """
    刷新访问令牌
    """
    new_token = create_access_token(
        user_id=current_user["id"],
        role=current_user.get("role"),
        token_type="pd_auth"
    )
    return {
        "token": new_token,
        "expires_in": 3600 * 24
    }


# ========== 当前用户接口 ==========

@router.get("/me", summary="获取当前用户信息", response_model=UserResp)
def get_me(current_user: dict = Depends(get_current_user)):
    """
    获取当前登录用户的详细信息
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            select_sql = build_dynamic_select(
                cur,
                "pd_users",
                where_clause="id=%s",
                select_fields=["id", "name", "account", "role", "phone", "email", "status", "created_at", "updated_at"]
            )
            cur.execute(select_sql, (current_user["id"],))
            user = cur.fetchone()
            
            if not user:
                raise HTTPException(status_code=404, detail="用户不存在")
            
            return UserResp(**user)


@router.put("/me", summary="更新当前用户信息")
def update_me(body: UpdateUserReq, current_user: dict = Depends(get_current_user)):
    """
    用户自主更新个人信息（不能修改角色）
    """
    # 不允许自主修改角色
    update_data = body.model_dump(exclude_none=True)
    update_data.pop("role", None)
    
    if not update_data:
        return {"msg": "无更新内容"}
    
    try:
        AuthService.update_user(current_user["id"], **update_data)
        return {"msg": "更新成功"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/me/password", summary="修改密码")
def change_password(body: UpdatePwdReq, current_user: dict = Depends(get_current_user)):
    """
    用户自主修改密码
    """
    try:
        AuthService.change_password(
            current_user["id"],
            body.old_password,
            body.new_password
        )
        return {"msg": "密码修改成功"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ========== 用户管理接口（管理员/大区经理） ==========

@router.post("/users", summary="创建用户")
def create_user(
    body: CreateUserReq,
    current_user: dict = Depends(get_current_user)
):
    """
    创建新用户
    - 管理员：可创建任何角色
    - 大区经理：只能创建自营库管理、财务、会计
    """
    # 权限检查
    current_role = current_user.get("role")
    
    if current_role == "大区经理":
        if body.role in ["管理员", "大区经理"]:
            raise HTTPException(status_code=403, detail="大区经理不能创建管理员或其他大区经理")
    elif current_role != "管理员":
        raise HTTPException(status_code=403, detail="无权创建用户")
    
    # 验证角色合法性
    if body.role not in UserRole.VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"无效的角色，可选: {UserRole.VALID_ROLES}")
    
    try:
        user_id = AuthService.create_user(
            name=body.name,
            account=body.account,
            password=body.password,
            role=body.role,
            phone=body.phone,
            email=body.email,
            created_by=current_user["id"]
        )
        return {"msg": "创建成功", "user_id": user_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/users", summary="用户列表")
def list_users(
    page: int = 1,
    size: int = 20,
    role: Optional[str] = None,
    keyword: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """
    获取用户列表（支持分页、筛选）
    """
    # 权限检查
    if current_user.get("role") not in ["管理员", "大区经理"]:
        raise HTTPException(status_code=403, detail="无权查看用户列表")
    
    result = AuthService.list_users(
        page=page,
        size=size,
        role=role,
        keyword=keyword
    )
    return result


@router.get("/users/{user_id}", summary="用户详情")
def get_user(
    user_id: int,
    current_user: dict = Depends(get_current_user)
):
    """
    获取指定用户详情
    """
    user = AuthService.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


@router.put("/users/{user_id}", summary="更新用户信息")
def update_user(
    user_id: int,
    body: UpdateUserReq,
    current_user: dict = Depends(get_current_user)
):
    """
    更新指定用户信息
    """
    # 权限检查：只能管理下级角色
    target_user = AuthService.get_user_by_id(user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    current_role = current_user.get("role")
    target_role = target_user.get("role")
    
    # 管理员可以修改任何人
    if current_role != "管理员":
        # 大区经理只能修改比自己低级的
        if current_role == "大区经理":
            if target_role in ["管理员", "大区经理"]:
                raise HTTPException(status_code=403, detail="无权修改该用户")
        else:
            raise HTTPException(status_code=403, detail="无权修改用户")
    
    # 不能修改自己的角色
    if user_id == current_user["id"] and body.role:
        raise HTTPException(status_code=400, detail="不能修改自己的角色")
    
    try:
        AuthService.update_user(user_id, **body.model_dump(exclude_none=True))
        return {"msg": "更新成功"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/users/{user_id}", summary="删除用户")
def delete_user(
    user_id: int,
    current_user: dict = Depends(get_current_user)
):
    """
    删除用户（软删除，设置状态为已注销）
    """
    # 不能删除自己
    if user_id == current_user["id"]:
        raise HTTPException(status_code=400, detail="不能删除自己")
    
    check_admin_permission(current_user)
    
    try:
        AuthService.delete_user(user_id)
        return {"msg": "用户已删除"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/users/{user_id}/reset-password", summary="重置密码")
def admin_reset_password(
    user_id: int,
    body: ResetPwdReq,
    current_user: dict = Depends(get_current_user)
):
    """
    管理员重置用户密码
    """
    # 权限检查
    if current_user.get("role") not in ["管理员", "大区经理"]:
        raise HTTPException(status_code=403, detail="无权重置密码")
    
    # 密钥验证（双重验证）
    if body.admin_key != "pd_admin_2025":
        raise HTTPException(status_code=403, detail="管理密钥错误")
    
    try:
        AuthService.admin_reset_password(user_id, body.new_password)
        return {"msg": "密码已重置"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/users/{user_id}/freeze", summary="冻结用户")
def freeze_user(
    user_id: int,
    current_user: dict = Depends(get_current_user)
):
    """
    冻结用户账号
    """
    # 不能冻结自己
    if user_id == current_user["id"]:
        raise HTTPException(status_code=400, detail="不能冻结自己")
    
    check_manager_permission(current_user)
    
    try:
        AuthService.set_user_status(user_id, UserStatus.FROZEN)
        return {"msg": "用户已冻结"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/users/{user_id}/unfreeze", summary="解冻用户")
def unfreeze_user(
    user_id: int,
    current_user: dict = Depends(get_current_user)
):
    """
    解冻用户账号
    """
    check_manager_permission(current_user)
    
    try:
        AuthService.set_user_status(user_id, UserStatus.NORMAL)
        return {"msg": "用户已解冻"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ========== 角色相关接口 ==========

@router.get("/roles", summary="获取角色列表")
def get_roles():
    """
    获取系统预定义的角色列表
    """
    return {
        "roles": [
            {"code": "管理员", "name": "管理员", "description": "系统管理员，拥有所有权限"},
            {"code": "大区经理", "name": "大区经理", "description": "可管理下级用户和数据"},
            {"code": "自营库管理", "name": "自营库管理", "description": "管理库存和物流"},
            {"code": "财务", "name": "财务", "description": "处理财务相关操作"},
            {"code": "会计", "name": "会计", "description": "查看财务数据"}
        ]
    }