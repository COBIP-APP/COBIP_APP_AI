"""도메인 초점어 기반 multi-query 검색 + 재정렬.

기본 질의 1회 검색만으로는 광범위한 카테고리명(예: "Infrastructure")이나 일반 수식어가
임베딩을 지배해 요청 도메인과 무관한 청크가 상위에 오를 수 있다.
초점어(domain/keywords의 각 용어)별 보조 질의를 추가로 검색한 뒤 Reciprocal Rank Fusion으로 합치고
(여러 초점어 질의에 반복 등장한 청크가 우선), payload(카테고리/토픽/파일/제목/본문)에 등장하는
초점어 수만큼 가산점을 준다. 선택 시에는 초점어마다 그 질의가 찾은 최상위 관련 문서를 먼저 포함해
여러 초점어가 고르게 반영되게 하고(초점어 질의 순위 기준, 아직 선택되지 않은 문서만), 한 문서가 결과를 독점하지 않도록 source_file당 개수를 제한한다.
"""

from __future__ import annotations

import logging
import re
from typing import Protocol

from app.schemas.rag import RetrievedReference

__all__ = ["FocusedRetriever", "extract_focus_terms"]

logger = logging.getLogger(__name__)

_MULTI_WORD_SEPARATORS = re.compile(r"[,;|·/]\s|[,;|·]")
_MIN_TERM_LENGTH = 2


class _Retriever(Protocol):
    def retrieve(self, query: str, top_k: int | None = None) -> list[RetrievedReference]: ...


def extract_focus_terms(*sources: str | list[str] | None, max_terms: int = 8) -> list[str]:
    """domain 문자열/키워드 목록에서 중복 없는 초점어를 추출한다.

    쉼표·세미콜론·파이프 등 구분자가 있으면 구분자 단위(다단어 용어 유지), 없으면 공백 단위로 나눈다.
    "CI/CD"처럼 공백 없는 슬래시는 하나의 용어로 유지된다.
    """
    terms: list[str] = []
    seen: set[str] = set()
    for src in sources:
        if not src:
            continue
        chunks = src if isinstance(src, list) else [src]
        for chunk in chunks:
            text = " ".join(str(chunk).split())
            if not text:
                continue
            parts = _MULTI_WORD_SEPARATORS.split(text) if _MULTI_WORD_SEPARATORS.search(text) else text.split(" ")
            for part in parts:
                term = part.strip()
                key = term.lower()
                if len(term) < _MIN_TERM_LENGTH or key in seen:
                    continue
                seen.add(key)
                terms.append(term)
    return terms[:max_terms]


def _source_key(ref: RetrievedReference) -> str:
    meta = ref.metadata or {}
    src = meta.get("source_file")
    if isinstance(src, str) and src:
        return src
    return ref.title or ref.id or ref.content[:40]


def _ref_key(ref: RetrievedReference) -> str:
    return ref.id or f"{_source_key(ref)}#{hash(ref.content)}"


def _searchable_text(ref: RetrievedReference) -> str:
    meta = ref.metadata or {}
    fields = [ref.title or "", ref.content or ""]
    for key in ("category", "topic", "source_file"):
        val = meta.get(key)
        if isinstance(val, str):
            fields.append(val)
    return " ".join(fields).lower()


class FocusedRetriever:
    """기본 질의 + 초점어별 보조 질의 결과를 병합·재정렬한다."""

    def __init__(
        self,
        retriever: _Retriever,
        *,
        per_query_top_k: int = 4,
        base_top_k: int = 10,
        rrf_k: int = 60,
        term_match_weight: float = 0.5,
        max_per_source: int = 2,
    ) -> None:
        """term_match_weight: 초점어 1개 매칭 가산점 (단일 질의 1위 RRF 점수 대비 비율)."""
        self._retriever = retriever
        self._per_query_top_k = per_query_top_k
        self._base_top_k = base_top_k
        self._rrf_k = rrf_k
        self._term_match_weight = term_match_weight
        self._max_per_source = max_per_source

    def retrieve(
        self,
        base_query: str,
        focus_terms: list[str],
        *,
        top_k: int = 5,
        context_hint: str = "",
    ) -> list[RetrievedReference]:
        candidates: dict[str, RetrievedReference] = {}
        fused: dict[str, float] = {}

        def _collect(query: str, k: int) -> list[str]:
            try:
                hits = self._retriever.retrieve(query, top_k=k)
            except Exception as exc:
                logger.warning("focused retrieval sub-query failed errorType=%s", type(exc).__name__)
                return []
            keys: list[str] = []
            for rank, ref in enumerate(hits, 1):
                key = _ref_key(ref)
                fused[key] = fused.get(key, 0.0) + 1.0 / (self._rrf_k + rank)
                prev = candidates.get(key)
                if prev is None or (ref.score or 0.0) > (prev.score or 0.0):
                    candidates[key] = ref
                keys.append(key)
            return keys

        _collect(base_query, self._base_top_k)
        term_hits = [_collect(f"{term} {context_hint}".strip(), self._per_query_top_k) for term in focus_terms]

        if not candidates:
            return []

        lowered_terms = [t.lower() for t in focus_terms]
        match_bonus = self._term_match_weight / (self._rrf_k + 1)

        matched_terms = {
            key: sum(1 for t in lowered_terms if t in _searchable_text(ref)) for key, ref in candidates.items()
        }

        def _rank_key(ref: RetrievedReference) -> tuple[float, float]:
            key = _ref_key(ref)
            return fused[key] + match_bonus * matched_terms[key], ref.score or 0.0

        ranked = sorted(candidates.values(), key=_rank_key, reverse=True)
        rank_pos = {_ref_key(ref): i for i, ref in enumerate(ranked)}

        chosen: set[str] = set()
        per_source: dict[str, int] = {}

        def _take(ref: RetrievedReference) -> None:
            chosen.add(_ref_key(ref))
            src = _source_key(ref)
            per_source[src] = per_source.get(src, 0) + 1

        for hits in term_hits:
            if len(chosen) >= top_k:
                break
            if any(key in chosen for key in hits):
                continue
            for key in hits:
                ref = candidates[key]
                if matched_terms[key] > 0 and per_source.get(_source_key(ref), 0) == 0:
                    _take(ref)
                    break

        overflow: list[RetrievedReference] = []
        for ref in ranked:
            if len(chosen) >= top_k:
                break
            if _ref_key(ref) in chosen:
                continue
            if per_source.get(_source_key(ref), 0) >= self._max_per_source:
                overflow.append(ref)
                continue
            _take(ref)
        for ref in overflow[: max(0, top_k - len(chosen))]:
            _take(ref)

        selected = sorted((candidates[key] for key in chosen), key=lambda r: rank_pos[_ref_key(r)])

        logger.info(
            "focused retrieval complete terms=%d candidates=%d selected=%d",
            len(focus_terms),
            len(candidates),
            len(selected),
        )
        return selected
