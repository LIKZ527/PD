import os
import urllib.parse
from typing import Optional

from dotenv import load_dotenv
from pydantic import BaseModel


def _build_prediction_async_database_url() -> str:
    """组装智能预测用 mysql+aiomysql URL；优先 PREDICTION_ASYNC_DATABASE_URL。"""
    load_dotenv()
    explicit = os.getenv("PREDICTION_ASYNC_DATABASE_URL")
    if explicit and explicit.strip():
        return explicit.strip()
    host = os.getenv("MYSQL_HOST")
    user = os.getenv("MYSQL_USER")
    password = os.getenv("MYSQL_PASSWORD", "")
    database = os.getenv("MYSQL_DATABASE")
    port = os.getenv("MYSQL_PORT", "3306")
    charset = os.getenv("MYSQL_CHARSET", "utf8mb4")
    if not host or not user or not database:
        return ""
    u = urllib.parse.quote_plus(user)
    p = urllib.parse.quote_plus(password)
    return f"mysql+aiomysql://{u}:{p}@{host}:{port}/{database}?charset={charset}"


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_settings() -> "Settings":
    load_dotenv()
    return Settings(
        app_name=os.getenv("APP_NAME", "采购配送管理接口"),
        jwt_secret=os.getenv("JWT_SECRET", "change-me"),
        jwt_algorithm=os.getenv("JWT_ALGORITHM", "HS256"),
        db_url=os.getenv(
            "DATABASE_URL", "mysql+pymysql://user:pass@localhost:3306/pd"
        ),
        coze_stream_url=os.getenv("Coze_url") or os.getenv("COZE_URL"),
        coze_project_id=os.getenv("project_id") or os.getenv("COZE_PROJECT_ID"),
        coze_session_id=os.getenv("session_id") or os.getenv("COZE_SESSION_ID"),
        coze_bearer_token=os.getenv("YOUR_TOKEN") or os.getenv("COZE_BEARER_TOKEN"),
        prediction_async_db_url=_build_prediction_async_database_url(),
        prediction_redis_url=os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
        celery_broker_url=os.getenv("CELERY_BROKER_URL", "redis://127.0.0.1:6379/1"),
        celery_result_backend=os.getenv("CELERY_RESULT_BACKEND", "redis://127.0.0.1:6379/2"),
        openai_api_key=(os.getenv("OPENAI_API_KEY") or "").strip(),
        openai_api_base=os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1"),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        azure_openai_api_key=(os.getenv("AZURE_OPENAI_API_KEY") or "").strip(),
        azure_openai_endpoint=(os.getenv("AZURE_OPENAI_ENDPOINT") or "").strip(),
        azure_openai_deployment=(os.getenv("AZURE_OPENAI_DEPLOYMENT") or "").strip(),
        azure_openai_api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview"),
        anthropic_api_key=(os.getenv("ANTHROPIC_API_KEY") or "").strip(),
        anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"),
        ai_request_timeout_seconds=_env_float("AI_REQUEST_TIMEOUT_SECONDS", 10.0),
        prediction_redis_ttl_seconds=_env_int("PREDICTION_REDIS_TTL_SECONDS", 3600),
        prompt_memory_ttl_seconds=_env_int("PROMPT_MEMORY_TTL_SECONDS", 300),
        openai_input_price_per_1k=_env_float("OPENAI_INPUT_PRICE_PER_1K", 0.005),
        openai_output_price_per_1k=_env_float("OPENAI_OUTPUT_PRICE_PER_1K", 0.015),
        prediction_prometheus_enabled=_env_bool("PREDICTION_PROMETHEUS_INSTRUMENTATOR", False),
        intelligent_prediction_schedule_enabled=_env_bool(
            "INTELLIGENT_PREDICTION_SCHEDULE_ENABLED", False
        ),
        intelligent_prediction_schedule_horizon_days=_env_int(
            "INTELLIGENT_PREDICTION_SCHEDULE_HORIZON_DAYS", 30
        ),
        intelligent_prediction_schedule_max_items=_env_int(
            "INTELLIGENT_PREDICTION_SCHEDULE_MAX_ITEMS", 50
        ),
        intelligent_prediction_schedule_cron_hour=_env_int(
            "INTELLIGENT_PREDICTION_SCHEDULE_CRON_HOUR", 2
        ),
        intelligent_prediction_schedule_cron_minute=_env_int(
            "INTELLIGENT_PREDICTION_SCHEDULE_CRON_MINUTE", 30
        ),
    )


class Settings(BaseModel):
    app_name: str
    jwt_secret: str
    jwt_algorithm: str
    db_url: str
    coze_stream_url: Optional[str] = None
    coze_project_id: Optional[str] = None
    coze_session_id: Optional[str] = None
    coze_bearer_token: Optional[str] = None

    prediction_async_db_url: str = ""
    prediction_redis_url: str = "redis://127.0.0.1:6379/0"
    celery_broker_url: str = "redis://127.0.0.1:6379/1"
    celery_result_backend: str = "redis://127.0.0.1:6379/2"

    openai_api_key: str = ""
    openai_api_base: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    azure_openai_api_key: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_deployment: str = ""
    azure_openai_api_version: str = "2024-02-15-preview"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-3-5-sonnet-20241022"

    ai_request_timeout_seconds: float = 10.0
    prediction_redis_ttl_seconds: int = 3600
    prompt_memory_ttl_seconds: int = 300
    openai_input_price_per_1k: float = 0.005
    openai_output_price_per_1k: float = 0.015
    prediction_prometheus_enabled: bool = False

    intelligent_prediction_schedule_enabled: bool = False
    intelligent_prediction_schedule_horizon_days: int = 30
    intelligent_prediction_schedule_max_items: int = 50
    intelligent_prediction_schedule_cron_hour: int = 2
    intelligent_prediction_schedule_cron_minute: int = 30


settings = load_settings()
