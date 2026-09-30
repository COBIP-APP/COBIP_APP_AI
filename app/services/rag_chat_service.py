"""실무 RAG 챗봇 서비스.

페르소나: 10년 경력 시니어 백엔드 개발자 멘토
- Qdrant Top-3 검색 후 지식 청크 주입
- 모바일 친화 마크다운: **핵심 키워드** 볼드, 3문장 이내 요약, 코드 예시 1개
- Ollama 오프라인 시 → RAG 청크 직접 합성 Fallback
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.schemas.chat import ChatRequest
from app.services.llm_service import LLMService
from app.services.retriever_service import RetrieverService

__all__ = ["RagChatService"]

logger = logging.getLogger(__name__)

_TOP_K = 3
_MAX_CHUNK_CHARS = 700  # 모바일 컨텍스트 초과 방지

_SYSTEM_PROMPT = """\
너는 10년 경력 시니어 백엔드 개발자 멘토다.
아래 [참고 지식(Context)] 청크를 바탕으로 질문에 답한다.

### 응답 규칙 (반드시 준수)
1. **핵심 키워드를 볼드(`**키워드**`)**로 강조한다.
2. 요약은 **3문장 이내**로 압축한다.
3. 코드나 명령어가 필요하면 **1개의 코드 블록만** 포함한다.
4. 일반론(\"아키텍처 이해가 필요합니다\" 류)으로 얼버무리지 않는다.
5. [참고 지식(Context)]에 있는 내용을 **직접 인용·요약**하여 답한다.
6. 한국어로 답하며, 모바일 화면에 맞게 간결하게 작성한다.
7. 컨텍스트가 없을 경우에는 일반 지식으로 위 규칙을 지키며 답한다.
"""


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


def _rag_synthesis_answer(question: str, refs: list[Any]) -> str:
    """Ollama 없이 Qdrant 청크를 직접 합성하여 답변 생성."""
    if not refs:
        return (
            f"**{question}**에 대한 관련 지식을 찾지 못했습니다.\n"
            "더 구체적인 키워드로 다시 질문해 주세요."
        )

    sorted_refs = sorted(refs, key=lambda r: getattr(r, "score", 0.0) or 0.0, reverse=True)
    top = sorted_refs[0]
    top_content = (top.content or "").strip()
    top_title = (top.title or "").strip()

    # 문장 단위 분리 (한국어·영어 혼용)
    sentences = [
        s.strip()
        for s in re.split(r"(?<=[.。!?\n])\s*", top_content)
        if len(s.strip()) > 15
    ]
    key_sentences = sentences[:3] if sentences else [top_content[:400]]

    # 추가 청크에서 보충 문장 수집
    extra: list[str] = []
    for ref in sorted_refs[1:]:
        c = (ref.content or "").strip()
        sents = [s.strip() for s in re.split(r"(?<=[.。!?\n])\s*", c) if len(s.strip()) > 15]
        if sents:
            extra.append(sents[0])

    answer_parts = [f"**[{top_title} 기반 답변]**\n"]
    answer_parts.extend(key_sentences)
    if extra:
        answer_parts.append("")
        answer_parts.append("📌 추가 참고:")
        answer_parts.extend(f"- {e[:150]}" for e in extra[:2])

    return "\n".join(answer_parts)


class RagChatService:
    """Qdrant Top-3 검색 + 시니어 개발자 페르소나 RAG 챗봇."""

    def __init__(self) -> None:
        self.llm = LLMService()
        self.retriever = RetrieverService()

    async def answer(self, request: ChatRequest) -> dict[str, Any]:
        question = request.message.strip()

        # 1) Qdrant 검색
        refs: list[Any] = []
        try:
            refs = self.retriever.retrieve(question, top_k=_TOP_K)
            refs = [r for r in refs if (r.content or "").strip()]
        except Exception as exc:
            logger.warning("RAG retrieval failed: %s", exc)

        # 2) 프롬프트 조립
        context_block = _build_context_block(refs)
        user_prompt = (
            f"{context_block}\n\n" if context_block else ""
        ) + f"[질문]: {question}"

        # 3) LLM 호출
        answer_text: str | None = None
        source = "fallback"
        try:
            raw = self.llm.generate_text(
                prompt=user_prompt,
                system_prompt=_SYSTEM_PROMPT,
                timeout_seconds=35,
            )
            if raw and raw.strip():
                answer_text = raw.strip()
                source = "ollama"
        except Exception as exc:
            logger.warning("LLM call failed, using RAG synthesis fallback: %s", exc)

        # 4) LLM 실패 시 → RAG 청크 직접 합성
        if not answer_text:
            answer_text = _rag_synthesis_answer(question, refs)
            source = "rag_synthesis" if refs else "fallback"

        return {
            "answer": answer_text,
            "source": source,
            "ragUsed": bool(refs),
            "references": [
                {
                    "title": getattr(r, "title", None),
                    "content": (getattr(r, "content", "") or "")[:300],
                    "score": getattr(r, "score", None),
                    "sourceType": getattr(r, "sourceType", None),
                }
                for r in refs
            ],
        }
