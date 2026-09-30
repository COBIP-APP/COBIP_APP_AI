"""실무 CS 및 기술 퀴즈 생성/오답 해설 API 라우터."""

import logging
from fastapi import APIRouter, HTTPException, status

from app.schemas.common import ApiResponse
from app.schemas.quiz import (
    QuizExplainRequest,
    QuizGenerateRequest,
)
from app.services.quiz_service import QuizService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai/quiz", tags=["quiz"])


@router.post("/generate", response_model=ApiResponse, summary="실무 CS/기술 퀴즈 자동 생성")
def generate_quizzes(request: QuizGenerateRequest) -> ApiResponse:
    """Qdrant 실무 지식 기반으로 맞춤형 기술 퀴즈를 생성합니다."""
    try:
        service = QuizService()
        result = service.generate_quizzes(request)
        return ApiResponse(
            success=True,
            message="퀴즈 생성이 완료되었습니다.",
            data=result.model_dump(),
        )
    except Exception as exc:
        logger.exception("Quiz generation endpoint error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"퀴즈 생성 중 오류가 발생했습니다: {exc}",
        )


@router.post("/explain", response_model=ApiResponse, summary="오답 분석 및 실무 심층 해설")
def explain_quiz(request: QuizExplainRequest) -> ApiResponse:
    """사용자 답안을 분석하고 Qdrant 검색 근거 기반 3줄 요약 및 실무 트러블슈팅 팁을 제공합니다."""
    try:
        service = QuizService()
        result = service.explain_quiz(request)
        return ApiResponse(
            success=True,
            message="오답 분석 및 심층 해설이 완료되었습니다.",
            data=result.model_dump(),
        )
    except Exception as exc:
        logger.exception("Quiz explain endpoint error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"해설 생성 중 오류가 발생했습니다: {exc}",
        )
