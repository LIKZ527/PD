"""
AI 鉴伪：用户提交待鉴别文本，经 Coze 流式调用（stream_run）聚合为纯文本结论；
OpenAPI/Swagger 中见本模块「请求体示例」「响应示例」与字段说明。
"""
from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.services.coze_agent_service import run_coze_agent_chat

router = APIRouter(prefix="/agent", tags=["AI鉴伪"])


class AgentChatRequest(BaseModel):
    """鉴伪请求体：将待分析的一段文字交给托管智能体；具体判定规则由 Coze 工作流与提示词配置。"""

    model_config = ConfigDict(
        json_schema_extra={
            "title": "鉴伪请求",
            "example": {
                "text": (
                    "合同编号 HT-2024-088，扫描件中卖方公章与档案备案印模边缘纹理不一致，"
                    "签订日期与系统录入差一天，请协助判断是否存在篡改或翻拍风险。"
                )
            },
        }
    )

    text: str = Field(
        ...,
        min_length=1,
        title="待鉴别文本",
        description=(
            "需要鉴别的文字内容，例如：合同或磅单关键字段说明、影像疑点描述、"
            "业务人员整理后的摘要等（非空字符串）。"
        ),
    )


class AgentChatResponse(BaseModel):
    """鉴伪响应体：智能体返回的鉴别说明，单一字符串字段，无 Markdown 结构约定。"""

    model_config = ConfigDict(
        json_schema_extra={
            "title": "鉴伪响应",
            "example": {
                "text": (
                    "基于您提供的文字描述：① 公章差异需对照原件与备案印模，扫描压缩可能导致纹理误判；"
                    "② 日期不一致应核对录入来源与纸质合同；③ 本输出为辅助参考，不构成法律或鉴定结论，"
                    "重大事项请以实物鉴定与法务意见为准。"
                )
            },
        }
    )

    text: str = Field(
        ...,
        title="鉴别说明",
        description="智能体生成的完整鉴别说明（纯文本，可直接展示给用户）。",
    )


@router.post(
    "/chat",
    summary="提交待鉴别文本并获取说明",
    description=(
        "将一段待鉴别的中文（或中英文混合）描述提交给 Coze 托管智能体，"
        "服务端通过流式接口拉取片段并拼接为完整字符串后返回。\n\n"
        "**输入**：JSON 对象，仅含字段 `text`（字符串，至少 1 个字符）。\n\n"
        "**输出（HTTP 200）**：JSON 对象，仅含字段 `text`（字符串），为智能体生成的鉴别说明。\n\n"
        "**错误（HTTP 502）**：JSON `{\"detail\": \"错误说明\"}`，常见于环境变量未配置、"
        "网络超时或 Coze 侧执行失败。\n\n"
        "**说明**：鉴别逻辑、提示词与知识库在 Coze 工作流中配置；本接口只做参数校验、"
        "调用编排与错误码映射，不提供本地规则引擎。"
    ),
    response_description="成功时返回 JSON，字段 `text` 为鉴别说明全文；失败时见 HTTP 502 的 `detail`。",
    response_model=AgentChatResponse,
    responses={
        200: {
            "description": "调用成功，返回智能体聚合后的鉴别说明。",
            "content": {
                "application/json": {
                    "examples": {
                        "典型成功响应": {
                            "summary": "合同疑点类回复示例",
                            "value": {
                                "text": (
                                    "根据描述：公章纹理差异可能受扫描分辨率与压缩影响，"
                                    "建议调取原件与备案印模比对；签订日期应以纸质合同为准并与录入系统核对。"
                                    "以上为辅助分析，不构成司法鉴定结论。"
                                )
                            },
                        }
                    }
                }
            },
        },
        502: {
            "description": "智能体不可用：配置缺失、上游错误或执行失败。",
            "content": {
                "application/json": {
                    "example": {"detail": "COZE 配置缺失或智能体调用失败"},
                    "examples": {
                        "配置问题": {
                            "summary": "常见配置类错误",
                            "value": {"detail": "缺少 COZE_API_TOKEN 或工作流未就绪"},
                        }
                    },
                }
            },
        },
    },
)
async def agent_chat(
    body: AgentChatRequest = Body(
        openapi_examples={
            "合同公章疑点": {
                "summary": "合同扫描件与备案信息不一致",
                "description": "描述编号、公章、日期等疑点，请求风险判断。",
                "value": {
                    "text": (
                        "合同编号 HT-2024-088，扫描件中卖方公章与档案备案印模边缘纹理不一致，"
                        "签订日期与系统录入差一天，请协助判断是否存在篡改或翻拍风险。"
                    )
                },
            },
            "磅单字段核对": {
                "summary": "磅单关键信息摘要",
                "description": "用户提供整理后的磅单文字信息，请求一致性或异常提示。",
                "value": {
                    "text": (
                        "过磅单号 WB-3312，车牌 鲁A·12345，净重 48.6 吨，"
                        "与报货计划单号 DH-20240401-07 是否逻辑相符？有无明显涂改痕迹描述？"
                    )
                },
            },
        }
    ),
) -> AgentChatResponse:
    """
    执行一次鉴伪对话：请求体中的 `text` 原样传入 Coze 智能体，响应中的 `text` 为完整回复。

    Swagger UI 中可在「请求体」下拉选择不同示例；响应区可查看 200/502 的示例结构。
    """
    result = run_coze_agent_chat(body.text)
    if result.get("success"):
        return AgentChatResponse(text=result["text"])
    raise HTTPException(
        status_code=502,
        detail=result.get("error", "智能体调用失败"),
    )
