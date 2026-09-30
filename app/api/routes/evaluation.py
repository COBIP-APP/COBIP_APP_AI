from fastapi import APIRouter

from app.schemas.common import ApiResponse
from app.schemas.evaluation import (
    JavaCodeGradingRequest,
    JavaCodeGradingResponse,
    MissionFeedbackRequest,
    QuizGradeRequest,
)
from app.services.evaluation_service import (
    CodeAnalyzeRequest,
    EvaluationService,
)
from app.services.interview_feedback_service import (
    InterviewFeedbackRequest,
    InterviewFeedbackService,
)
from app.services.mission_feedback_service import MissionFeedbackService

router = APIRouter(prefix="/ai", tags=["evaluation"])


@router.post("/quiz/grade", response_model=ApiResponse)
def grade_quiz(request: QuizGradeRequest) -> ApiResponse:
    result = EvaluationService().grade_quiz(request)
    return ApiResponse(
        success=True,
        message="기본 문제 채점이 완료되었습니다.",
        data=result.model_dump(),
    )


@router.post("/mission/feedback", response_model=ApiResponse)
def mission_feedback(request: MissionFeedbackRequest) -> ApiResponse:
    result = MissionFeedbackService().generate_feedback(request)
    return ApiResponse(
        success=True,
        message="실습 미션 피드백이 완료되었습니다.",
        data=result.model_dump(),
    )


@router.post("/interview/feedback", response_model=ApiResponse)
def interview_feedback(request: InterviewFeedbackRequest) -> ApiResponse:
    result = InterviewFeedbackService().generate_feedback(request)
    return ApiResponse(
        success=True,
        message="면접 피드백이 완료되었습니다.",
        data=result.model_dump(),
    )


@router.post("/code/analyze", response_model=ApiResponse)
def analyze_code(request: CodeAnalyzeRequest) -> ApiResponse:
    result = EvaluationService().analyze_code(request)
    return ApiResponse(
        success=True,
        message="코드 분석이 완료되었습니다.",
        data=result.model_dump(),
    )


@router.post("/java/grade", response_model=ApiResponse)
def grade_java_code(request: JavaCodeGradingRequest) -> ApiResponse:
    """자바 코딩테스트 채점 API - LLM 기반 정적 분석"""
    result = EvaluationService().grade_java_code(request)
    return ApiResponse(
        success=True,
        message="자바 코드 채점이 완료되었습니다.",
        data=result.model_dump(),
    )
