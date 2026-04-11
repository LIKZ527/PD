"""智能预测接口：审计主体（可选 Bearer，不强制登录）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Optional

import jwt
from fastapi import Header, Request

from app.core.config import settings
from core.auth import get_user_identity_from_authorization


@dataclass
class AuditActor:
    user_id: Optional[int]
    user_label: str
    client_ip: Optional[str]


def try_decode_uid(authorization: Optional[str]) -> Optional[int]:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.PyJWTError:
        return None
    uid = payload.get("uid") or payload.get("sub")
    if uid is None:
        return None
    try:
        return int(uid)
    except (TypeError, ValueError):
        return None


def get_audit_actor(
    request: Request,
    authorization: Annotated[Optional[str], Header()] = None,
) -> AuditActor:
    return AuditActor(
        user_id=try_decode_uid(authorization),
        user_label=get_user_identity_from_authorization(authorization),
        client_ip=request.client.host if request.client else None,
    )
