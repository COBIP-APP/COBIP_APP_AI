"""실무 RAG Q&A 챗봇 라우터.

Qdrant Top-3 검색 기반 → 모바일 친화적 마크다운 응답.
페르소나: 10년 경력 시니어 개발자 멘토
"""

from fastapi import APIRouter

from app.schemas.chat import ChatRequest
from app.schemas.common import ApiResponse
from app.services.rag_chat_service import RagChatService

router = APIRouter(prefix="/ai", tags=["chat"])


@router.post("/chat", response_model=ApiResponse)
async def chat(request: ChatRequest) -> ApiResponse:
    """Qdrant 지식 기반 실무 Q&A 챗봇.

    - Qdrant에서 관련 청크 Top-3 검색
    - 시니어 개발자 멘토 페르소나로 응답
    - 모바일 친화 마크다운: 핵심 키워드 볼드, 3문장 요약, 코드 예시 1개
    """
    service = RagChatService()
    result = await service.answer(request)
    return ApiResponse(
        success=True,
        message="챗봇 답변이 생성되었습니다.",
        data=result,
    )
