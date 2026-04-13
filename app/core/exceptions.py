"""业务异常（智能预测等模块共用）。"""

from __future__ import annotations

from typing import Any

# 对外 HTTP 500 统一文案，避免将异常细节返回给客户端（详细栈见日志）
INTERNAL_SERVER_ERROR_MESSAGE = "服务器内部错误，请稍后重试"


class BusinessException(Exception):
    """可预期的业务错误，由 FastAPI 异常处理器转为 JSON。"""

    def __init__(
        self,
        message: str,
        *,
        code: str = "BUSINESS_ERROR",
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details or {}


class ValidationBusinessException(BusinessException):
    """数据验证相关业务错误。"""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, code="VALIDATION_ERROR", status_code=422, details=details)


class NotFoundBusinessException(BusinessException):
    """资源不存在。"""

    def __init__(self, message: str = "资源不存在") -> None:
        super().__init__(message, code="NOT_FOUND", status_code=404)


class ServiceUnavailableBusinessException(BusinessException):
    """外部服务不可用。"""

    def __init__(self, message: str = "服务暂时不可用") -> None:
        super().__init__(message, code="SERVICE_UNAVAILABLE", status_code=503)
