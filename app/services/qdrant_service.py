"""Qdrant client service (연결·헬스·컬렉션·벡터 검색·upsert).

인덱싱은 `POST /ai/rag/index` 등에서 호출한다. `/ai/chat` 자동 주입은 하지 않는다.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    MatchValue,
    PointStruct,
    VectorParams,
)

from app.core.config import settings

__all__ = ["QdrantService"]

logger = logging.getLogger(__name__)

# resource_type → 컬렉션 이름 (미등록 시 settings.QDRANT_COLLECTION)
_COLLECTION_MAP: dict[str, str] = {}


class QdrantService:
    """QdrantClient 래퍼. 연결 실패 시에도 앱 기동을 막지 않는다."""

    def __init__(self) -> None:
        self._client: QdrantClient | None = None
        self._client_init_error: str | None = None

    def _get_client(self) -> QdrantClient | None:
        if self._client_init_error is not None and self._client is None:
            return None
        if self._client is not None:
            return self._client

        # 1. 원격 Qdrant 서버 연결 시도
        try:
            client = QdrantClient(
                url=settings.QDRANT_URL,
                timeout=3,
                check_compatibility=False,
            )
            client.get_collections()
            self._client = client
            host = urlparse(settings.QDRANT_URL).hostname or "unknown"
            logger.info("qdrant client ready (remote host=%s)", host)
            return self._client
        except Exception as remote_exc:
            logger.info("remote qdrant unreachable (%s), falling back to local persistent embedded storage", remote_exc)

        # 2. 로컬 임베디드 모드 fallback (data/qdrant_storage)
        try:
            import os
            os.makedirs("data/qdrant_storage", exist_ok=True)
            self._client = QdrantClient(path="data/qdrant_storage")
            logger.info("qdrant client ready (local embedded path=data/qdrant_storage)")
            return self._client
        except Exception as exc:
            self._client_init_error = type(exc).__name__
            self._client = None
            logger.warning(
                "qdrant client init failed errorType=%s",
                self._client_init_error,
            )
            return None

    def health_check(self) -> dict[str, Any]:
        """Qdrant reachability 확인 (검색 미수행)."""
        client = self._get_client()
        if client is None:
            return {
                "ok": False,
                "reachable": False,
                "errorType": self._client_init_error or "client_unavailable",
            }
        try:
            client.get_collections()
            return {"ok": True, "reachable": True}
        except Exception as exc:
            logger.warning("qdrant health failed errorType=%s", type(exc).__name__)
            return {
                "ok": False,
                "reachable": False,
                "errorType": type(exc).__name__,
            }

    def collection_exists(self, collection_name: str | None = None) -> bool:
        name = collection_name or settings.QDRANT_COLLECTION
        client = self._get_client()
        if client is None:
            return False
        try:
            client.get_collection(name)
            return True
        except Exception as exc:
            logger.warning(
                "qdrant collection_exists error errorType=%s",
                type(exc).__name__,
            )
            return False

    def ensure_collection(
        self,
        vector_size: int,
        collection_name: str | None = None,
    ) -> bool:
        """컬렉션이 없으면 Cosine 거리로 생성한다. 이미 있으면 그대로 둔다."""
        name = collection_name or settings.QDRANT_COLLECTION
        client = self._get_client()
        if client is None:
            logger.warning("qdrant ensure_collection skipped errorType=no_client")
            return False

        if self.collection_exists(name):
            logger.info("qdrant collection exists name=%s", name)
            return True

        try:
            client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
            )
            logger.info(
                "qdrant collection created name=%s vector_size=%s distance=cosine",
                name,
                vector_size,
            )
            return True
        except Exception as exc:
            logger.warning(
                "qdrant ensure_collection failed errorType=%s",
                type(exc).__name__,
            )
            return False

    def upsert(self, points: list[dict], collection_name: str | None = None) -> bool:
        """Point dict(id, vector, payload) 목록을 upsert. 실패 시 False."""
        name = collection_name or settings.QDRANT_COLLECTION
        client = self._get_client()
        if client is None:
            logger.warning("qdrant upsert skipped errorType=no_client")
            return False
        if not points:
            return True

        structs: list[PointStruct] = []
        try:
            for p in points:
                structs.append(
                    PointStruct(
                        id=p["id"],
                        vector=p["vector"],
                        payload=p.get("payload") or {},
                    )
                )
            client.upsert(collection_name=name, points=structs)
            logger.info("qdrant upsert ok point_count=%s collection=%s", len(structs), name)
            return True
        except Exception as exc:
            logger.warning("qdrant upsert failed errorType=%s", type(exc).__name__)
            return False

    @staticmethod
    def _dict_filter_to_model(payload_filter: dict) -> Filter | None:
        """build_filter() 결과 dict → qdrant Filter."""
        must_raw = payload_filter.get("must")
        if not must_raw or not isinstance(must_raw, list):
            return None
        must: list[FieldCondition] = []
        for cond in must_raw:
            if not isinstance(cond, dict):
                continue
            key = cond.get("key")
            match = cond.get("match") or {}
            if not key:
                continue
            if "value" in match:
                must.append(
                    FieldCondition(key=key, match=MatchValue(value=match["value"]))
                )
            elif "any" in match and isinstance(match["any"], list):
                must.append(FieldCondition(key=key, match=MatchAny(any=match["any"])))
        if not must:
            return None
        return Filter(must=must)

    def search(
        self,
        query_vector: list[float],
        top_k: int,
        collection_name: str | None = None,
        query_filter: Any | None = None,
    ) -> list[dict]:
        """벡터 유사도 검색. 컬렉션 없음·오류 시 빈 리스트."""
        name = collection_name or settings.QDRANT_COLLECTION
        client = self._get_client()
        if client is None:
            logger.warning("qdrant search skipped errorType=no_client")
            return []

        if not self.collection_exists(name):
            return []

        qf: Filter | None = None
        if query_filter is None:
            qf = None
        elif isinstance(query_filter, Filter):
            qf = query_filter
        elif isinstance(query_filter, dict):
            qf = self._dict_filter_to_model(query_filter)

        try:
            response = client.query_points(
                collection_name=name,
                query=query_vector,
                limit=top_k,
                query_filter=qf,
            )
        except Exception as exc:
            logger.warning("qdrant search failed errorType=%s", type(exc).__name__)
            return []

        out: list[dict[str, Any]] = []
        for r in response.points:
            payload = dict(r.payload) if r.payload else {}
            out.append(
                {
                    "id": r.id,
                    "score": r.score,
                    "payload": payload,
                }
            )
        return out

    def build_filter(self, payload: dict) -> dict:
        """Qdrant 호환 filter (must AND) 구조."""
        if not payload:
            return {}

        conditions: list[dict] = []
        for key, value in payload.items():
            if value is None:
                continue
            if isinstance(value, list):
                conditions.append({"key": key, "match": {"any": value}})
            else:
                conditions.append({"key": key, "match": {"value": value}})

        if not conditions:
            return {}
        return {"must": conditions}

    def get_collection_name(self, resource_type: str) -> str:
        return _COLLECTION_MAP.get(resource_type, settings.QDRANT_COLLECTION)
