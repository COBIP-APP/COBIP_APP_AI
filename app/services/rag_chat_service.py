"""실무 RAG 챗봇 서비스 (/ai/chat).

흐름:
- classify_chat_intent + useRag 로 RAG 시도 여부 결정
  (useRag=False → 미사용, True → 항상 시도, None → TECHNICAL 일 때만 시도)
- 검색 결과는 CHAT_RAG_MIN_SCORE 이상만 prompt/references 에 사용
- 관련 문서가 없으면 일반 LLM, LLM 실패/빈 응답이면 고정 fallback 문구

응답 source: "rag"(RAG+LLM) / "ollama"(일반 LLM) / "fallback"(LLM 실패)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.core.config import settings
from app.schemas.chat import ChatRequest
from app.services.chat_query_policy import (
    CHAT_INTENT_SMALL_TALK,
    ChatIntent,
    classify_chat_intent,
)
from app.services.llm_service import LLMService
from app.services.retriever_service import RetrieverService

__all__ = ["CHAT_FALLBACK_ANSWER", "RagChatService"]

logger = logging.getLogger(__name__)

_MAX_CHUNK_CHARS = 700  # 모바일 컨텍스트 초과 방지
_REFERENCE_CONTENT_MAX_CHARS = 300
_LOG_ERROR_MAX_CHARS = 300

CHAT_FALLBACK_ANSWER = (
    "현재 답변을 생성할 수 없습니다. 잠시 후 다시 시도해 주세요."
)

_SMALL_TALK_SYSTEM_PROMPT = """\
너는 COBIP 개발 학습 앱의 친근한 챗봇 도우미다.
사용자의 인사, 감사, 가벼운 잡담에 답한다.

### 응답 규칙
1. 한국어로 1~2문장 이내로 짧고 친근하게 답한다.
2. 질문받지 않은 기술 개념이나 문서 내용을 임의로 꺼내지 않는다.
3. 무엇을 할 수 있는지 물으면 프로그래밍, 백엔드, 네트워크, 데이터베이스, 인프라 등
   개발 개념 질문에 답하고 학습을 도울 수 있다고 간단히 소개한다.
"""

_MENTOR_RULES = """\
### 응답 규칙 (반드시 준수)
1. **핵심 키워드를 볼드(`**키워드**`)**로 강조한다.
2. 요약은 **3문장 이내**로 압축한다.
3. 코드나 명령어가 필요하면 **1개의 코드 블록만** 포함한다.
4. 일반론(\"아키텍처 이해가 필요합니다\" 류)으로 얼버무리지 않는다.
5. 한국어로 답하며, 모바일 화면에 맞게 간결하게 작성한다.
"""

_GENERAL_SYSTEM_PROMPT = (
    "너는 10년 경력 시니어 백엔드 개발자 멘토다.\n"
    "일반 개발 지식을 바탕으로 질문에 정확하게 답한다. "
    "확실하지 않은 내용은 추측하지 말고 모른다고 말한다.\n\n" + _MENTOR_RULES
)

_RAG_SYSTEM_PROMPT = (
    "너는 10년 경력 시니어 백엔드 개발자 멘토다.\n"
    "아래 [참고 지식(Context)]은 질문과 관련 있을 수 있는 검색 결과다.\n"
    "- Context가 질문과 관련 있으면 그 내용을 직접 인용·요약하여 답한다.\n"
    "- 검색 Context가 질문과 관련 없으면 억지로 사용하지 말고, "
    "일반 개발 지식으로 답한다.\n\n" + _MENTOR_RULES
)


def _build_context_block(refs: list[Any]) -> str:
    """검색된 청크를 LLM 프롬프트용 텍스트 블록으로 조립."""
    if not refs:
        return ""
    lines = ["[참고 지식(Context)]"]
    for i, ref in enumerate(refs, 1):
        content = (ref.content or "").strip()[:_MAX_CHUNK_CHARS]
        title = (ref.title or f"참고 {i}").strip()
        if content:
            lines.append(f"\n[{i}] {title}\n{content}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _should_attempt_rag(intent: ChatIntent, use_rag: bool | None) -> bool:
    if use_rag is False:
        return False
    if use_rag is True:
        return True
    return intent != CHAT_INTENT_SMALL_TALK


def _filter_relevant(refs: list[Any], min_score: float) -> list[Any]:
    """빈 content·score None·min_score 미만 결과를 제외한다."""
    kept: list[Any] = []
    for ref in refs:
        if not (getattr(ref, "content", "") or "").strip():
            continue
        score = getattr(ref, "score", None)
        if score is None or score < min_score:
            continue
        kept.append(ref)
    return kept


def _reference_payload(ref: Any) -> dict[str, Any]:
    return {
        "title": getattr(ref, "title", None),
        "content": (getattr(ref, "content", "") or "")[:_REFERENCE_CONTENT_MAX_CHARS],
        "score": getattr(ref, "score", None),
        "sourceType": getattr(ref, "sourceType", None),
    }


class RagChatService:
    """intent·similarity threshold 기반 RAG 라우팅 챗봇."""

    def __init__(
        self,
        llm: LLMService | None = None,
        retriever: RetrieverService | None = None,
    ) -> None:
        self.llm = llm or LLMService()
        self.retriever = retriever or RetrieverService()

    async def answer(self, request: ChatRequest) -> dict[str, Any]:
        question = request.message.strip()
        intent = classify_chat_intent(question)
        rag_attempted = _should_attempt_rag(intent, request.useRag)

        candidates: list[Any] = []
        refs: list[Any] = []
        if rag_attempted:
            candidates = await self._retrieve(question)
            refs = _filter_relevant(candidates, settings.CHAT_RAG_MIN_SCORE)
        filtered_count = len(refs)

        if refs:
            system_prompt = _RAG_SYSTEM_PROMPT
            user_prompt = f"{_build_context_block(refs)}\n\n[질문]: {question}"
        elif intent == CHAT_INTENT_SMALL_TALK:
            system_prompt = _SMALL_TALK_SYSTEM_PROMPT
            user_prompt = question
        else:
            system_prompt = _GENERAL_SYSTEM_PROMPT
            user_prompt = f"[질문]: {question}"

        answer_text = await self._generate(user_prompt, system_prompt)

        if answer_text is None:
            source = "fallback"
            answer_text = CHAT_FALLBACK_ANSWER
            refs = []
        elif refs:
            source = "rag"
        else:
            source = "ollama"

        scores = [r.score for r in candidates if getattr(r, "score", None) is not None]
        logger.info(
            "chat_answer intent=%s use_rag=%s rag_attempted=%s candidates=%s "
            "top_score=%s kept=%s min_score=%s source=%s query_len=%s",
            intent,
            request.useRag,
            rag_attempted,
            len(candidates),
            f"{max(scores):.4f}" if scores else None,
            filtered_count,
            settings.CHAT_RAG_MIN_SCORE,
            source,
            len(question),
        )

        return {
            "answer": answer_text,
            "source": source,
            "ragUsed": source == "rag",
            "references": [_reference_payload(r) for r in refs] if source == "rag" else [],
            "intent": intent,
        }

    async def _retrieve(self, question: str) -> list[Any]:
        try:
            return await asyncio.to_thread(
                self.retriever.retrieve,
                question,
                top_k=settings.RAG_TOP_K,
            )
        except Exception as exc:
            logger.warning(
                "chat RAG retrieval failed, using general LLM: errorType=%s",
                type(exc).__name__,
            )
            return []

    async def _generate(self, prompt: str, system_prompt: str) -> str | None:
        timeout = settings.LLM_TIMEOUT_SECONDS
        try:
            raw = await asyncio.to_thread(
                self.llm.generate_text,
                prompt=prompt,
                system_prompt=system_prompt,
                timeout_seconds=timeout,
            )
        except Exception as exc:
            logger.warning(
                'chat LLM call failed, using fallback: errorType=%s error="%s" timeout=%s',
                type(exc).__name__,
                str(exc)[:_LOG_ERROR_MAX_CHARS],
                timeout,
            )
            return None
        if raw and raw.strip():
            return raw.strip()
        logger.warning("chat LLM returned empty response, using fallback")
        return None
