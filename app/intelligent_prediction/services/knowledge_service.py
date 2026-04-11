"""方案二 RAG 知识库扩展点（空实现）。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class KnowledgeService(ABC):
    """知识检索服务抽象类（预留 RAG）。"""

    @abstractmethod
    async def retrieve(self, query: str, *, top_k: int = 5) -> list[dict[str, Any]]:
        """按查询获取知识片段。"""
        raise NotImplementedError


class NullKnowledgeService(KnowledgeService):
    """默认空实现。"""

    async def retrieve(self, query: str, *, top_k: int = 5) -> list[dict[str, Any]]:
        _ = query, top_k
        return []


def get_knowledge_service() -> KnowledgeService:
    """依赖注入工厂。"""
    return NullKnowledgeService()
