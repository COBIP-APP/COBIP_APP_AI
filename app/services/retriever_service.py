"""Qdrant + Embedding 기반 문서 검색 (Retriever).

이 단계에서는 `/ai/chat`에 연결하지 않고, 검색 결과만 반환한다.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from pydantic import BaseModel, Field

from app.core.config import settings
from app.schemas.rag import RetrievedReference
from app.services.embedding_service import EmbeddingService
from app.services.qdrant_service import QdrantService

__all__ = ["RetrieverService", "RetrieverTimingResult"]

logger = logging.getLogger(__name__)


def _effective_top_k(top_k: int | None) -> int:
    k = top_k if top_k is not None else settings.RAG_TOP_K
    return max(1, min(10, k))


def _payload_text(payload: dict[str, Any]) -> str:
    """payload에서 본문 후보를 순서대로 꺼낸다."""
    if not payload:
        return ""
    for key in ("content", "text", "body", "description"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


class RetrieverTimingResult(BaseModel):
    references: list[RetrievedReference] = Field(default_factory=list)
    embeddingMs: int = 0
    qdrantSearchMs: int = 0


class RetrieverService:
    """질의 임베딩 후 Qdrant 검색 → RetrievedReference 리스트."""

    def __init__(self) -> None:
        self._embed = EmbeddingService()
        self._qdrant = QdrantService()

    def retrieve(self, query: str, top_k: int | None = None) -> list[RetrievedReference]:
        return self.retrieve_with_timing(query, top_k).references

    def retrieve_with_timing(
        self, query: str, top_k: int | None = None
    ) -> RetrieverTimingResult:
        normalized = " ".join((query or "").split())
        if not normalized:
            raise ValueError("query must not be empty")

        k = _effective_top_k(top_k)
        q_len = len(normalized)
        logger.info("retriever retrieve start query_len=%s top_k=%s", q_len, k)

        embedding_ms = 0
        qdrant_search_ms = 0

        try:
            t0 = time.perf_counter()
            vector = self._embed.embed_query(normalized)
            embedding_ms = max(0, int((time.perf_counter() - t0) * 1000))
        except Exception as exc:
            logger.warning(
                "retriever embed failed errorType=%s",
                type(exc).__name__,
            )
            return RetrieverTimingResult(
                references=[],
                embeddingMs=embedding_ms,
                qdrantSearchMs=qdrant_search_ms,
            )

        try:
            t0 = time.perf_counter()
            hits = self._qdrant.search(
                query_vector=vector,
                top_k=k,
                collection_name=None,
                query_filter=None,
            )
            qdrant_search_ms = max(0, int((time.perf_counter() - t0) * 1000))
        except Exception as exc:
            logger.warning(
                "retriever qdrant search failed errorType=%s",
                type(exc).__name__,
            )
            return RetrieverTimingResult(
                references=[],
                embeddingMs=embedding_ms,
                qdrantSearchMs=qdrant_search_ms,
            )

        refs: list[RetrievedReference] = []
        for hit in hits:
            payload = hit.get("payload") if isinstance(hit.get("payload"), dict) else {}
            content = _payload_text(payload)
            if not content:
                continue
            rid = hit.get("id")
            refs.append(
                RetrievedReference(
                    id=str(rid) if rid is not None else None,
                    title=payload.get("title") if isinstance(payload.get("title"), str) else None,
                    content=content,
                    score=float(hit["score"]) if hit.get("score") is not None else None,
                    sourceType=payload.get("sourceType")
                    if isinstance(payload.get("sourceType"), str)
                    else None,
                    metadata={
                        kk: vv
                        for kk, vv in payload.items()
                        if kk
                        not in ("title", "content", "text", "body", "description", "sourceType")
                    },
                )
            )

        logger.info(
            "retriever retrieve complete count=%s embedding_ms=%s qdrant_ms=%s",
            len(refs),
            embedding_ms,
            qdrant_search_ms,
        )
        return RetrieverTimingResult(
            references=refs,
            embeddingMs=embedding_ms,
            qdrantSearchMs=qdrant_search_ms,
        )
