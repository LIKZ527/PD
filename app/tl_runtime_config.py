"""
TL 比价 / VLM / 采购建议 运行时配置（环境变量）。
与 app.core.config.Settings 分离，避免与现有 DATABASE_URL 体系混用。
"""
import os
from pathlib import Path

from dotenv import load_dotenv

from app.paths import PROJECT_ROOT

load_dotenv(PROJECT_ROOT / ".env")

# 文件上传目录（相对路径相对项目根）
_raw_upload = (os.getenv("UPLOAD_DIR") or "uploads").strip() or "uploads"
_up_path = Path(_raw_upload)
UPLOAD_DIR = (
    str(_up_path.resolve())
    if _up_path.is_absolute()
    else str(PROJECT_ROOT / _raw_upload)
)

# VLM（报价图识别）
VLM_API_KEY = os.getenv("VLM_API_KEY", "")
VLM_BASE_URL = os.getenv("VLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
VLM_MODEL = os.getenv("VLM_MODEL", "qwen-vl-max-latest")


def _optional_positive_int(name: str):
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        v = int(raw)
        return v if v > 0 else None
    except ValueError:
        return None


VLM_IMAGE_MAX_EDGE = _optional_positive_int("VLM_IMAGE_MAX_EDGE")
_vlm_mt = os.getenv("VLM_MAX_TOKENS", "8192").strip()
try:
    VLM_MAX_TOKENS = max(1024, min(32768, int(_vlm_mt)))
except ValueError:
    VLM_MAX_TOKENS = 8192
try:
    VLM_JPEG_QUALITY = max(60, min(100, int(os.getenv("VLM_JPEG_QUALITY", "88"))))
except ValueError:
    VLM_JPEG_QUALITY = 88

# LLM（采购建议等，OpenAI 兼容协议）
_explicit_llm_key = os.getenv("LLM_API_KEY", "").strip()
LLM_API_KEY = (
    _explicit_llm_key
    or os.getenv("DASHSCOPE_API_KEY", "").strip()
    or os.getenv("QWEN_API_KEY", "").strip()
    or os.getenv("VLM_API_KEY", "").strip()
)
_llm_base_env = os.getenv("LLM_BASE_URL", "").strip()
if _llm_base_env:
    LLM_BASE_URL = _llm_base_env
elif _explicit_llm_key:
    LLM_BASE_URL = "https://api.anthropic.com"
else:
    LLM_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_llm_model_env = os.getenv("LLM_MODEL", "").strip()
if _llm_model_env:
    LLM_MODEL = _llm_model_env
elif _explicit_llm_key:
    LLM_MODEL = "claude-sonnet-4-6"
else:
    LLM_MODEL = "qwen-plus"
