"""RAG 预留路由（501）。"""

from __future__ import annotations

from fastapi import APIRouter, Response

router = APIRouter()


@router.get("/search")
async def knowledge_search() -> Response:
    return Response(status_code=501, content="knowledge RAG not implemented")


@router.post("/ingest")
async def knowledge_ingest() -> Response:
    return Response(status_code=501, content="knowledge RAG not implemented")
