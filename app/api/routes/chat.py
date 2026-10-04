"""실무 RAG Q&A 챗봇 라우터.

질문 유형(SMALL_TALK/TECHNICAL)과 similarity threshold 에 따라
일반 LLM 또는 RAG + LLM 으로 답변한다.
"""

from fastapi import APIRouter

from app.schemas.chat import ChatRequest, ChatResponseData
from app.schemas.common import ApiResponse
from app.services.rag_chat_service import RagChatService

router = APIRouter(prefix="/ai", tags=["chat"])

_SUCCESS_MESSAGE = "챗봇 답변이 생성되었습니다."
_FALLBACK_MESSAGE = "챗봇 답변 생성에 실패하여 기본 안내 문구를 반환했습니다."


@router.post("/chat", response_model=ApiResponse)
async def chat(request: ChatRequest) -> ApiResponse:
    """개발 학습 Q&A 챗봇.

    - 인사·잡담: RAG 없이 일반 LLM
    - 기술 질문: Qdrant 검색 → CHAT_RAG_MIN_SCORE 이상 문서가 있으면 RAG + LLM, 없으면 일반 LLM
    - LLM 실패: source="fallback" + 고정 문구 (HTTP 200, success=true 유지)
    """
    service = RagChatService()
    result = await service.answer(request)
    data = ChatResponseData.model_validate(result).model_dump(exclude={"agent"})
    return ApiResponse(
        success=True,
        message=_FALLBACK_MESSAGE if data["source"] == "fallback" else _SUCCESS_MESSAGE,
        data=data,
    )
