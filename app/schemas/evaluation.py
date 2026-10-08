from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.feature_template import (
    ApiSpecSchema,
    MissionSchema,
    QuestionSchema,
    RequirementSchema,
)
from app.services.evaluation_payload_normalizer import (
    normalize_mission_feedback_payload,
    normalize_quiz_grade_payload,
)

__all__ = [
    "SubmittedCodeSchema",
    "CodeIssueSchema",
    "QuizGradeRequest",
    "QuizGradeResponse",
    "QuizGradeCriterion",
    "QuizGradeCriterionResult",
    "MissionFeedbackRequest",
    "MissionFeedbackResponse",
    "CodeAnalyzeRequest",
    "CodeAnalyzeResponse",
    "InterviewFeedbackRequest",
    "InterviewFeedbackResponse",
    "JavaCodeGradingRequest",
    "JavaCodeGradingResponse",
]


class SubmittedCodeSchema(BaseModel):
    fileName: str
    filePath: str | None = None
    language: str
    content: str


class CodeIssueSchema(BaseModel):
    fileName: str | None = None
    line: int | None = None
    severity: str
    message: str
    suggestion: str


class QuizGradeRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    templateId: int | None = None
    featureName: str
    question: QuestionSchema
    userAnswer: str
    # 서술형 생성 결과의 채점 키워드. AI criteria 생성 시 참고 정보로만 사용한다.
    gradingKeywords: list[str] | None = None
    relatedRequirements: list[RequirementSchema] | None = None
    relatedApiSpecs: list[ApiSpecSchema] | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_fe_payload(cls, data: Any) -> Any:
        return normalize_quiz_grade_payload(data)


# AI 채점 내부 전용 모델 (외부 응답 QuizGradeResponse 에는 포함하지 않는다)
class QuizGradeCriterion(BaseModel):
    id: int
    description: str
    weight: int


class QuizGradeCriterionResult(BaseModel):
    id: int
    description: str
    weight: int
    passed: bool
    score: int
    feedback: str = ""


class QuizGradeResponse(BaseModel):
    isCorrect: bool
    score: int
    feedback: str
    correctAnswer: str
    explanation: str
    relatedSection: str | None = None


class MissionFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    templateId: int | None = None
    featureName: str
    mission: MissionSchema
    submittedCode: list[SubmittedCodeSchema]
    requirements: list[RequirementSchema]
    apiSpecs: list[ApiSpecSchema] = Field(
        default_factory=list,
        validation_alias="apiSpecs",
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_fe_payload(cls, data: Any) -> Any:
        return normalize_mission_feedback_payload(data)


class MissionFeedbackResponse(BaseModel):
    passed: bool
    score: int
    summary: str
    satisfiedRequirements: list[str]
    missingRequirements: list[str]
    apiSpecIssues: list[str]
    codeIssues: list[CodeIssueSchema]
    improvementSuggestions: list[str]
    nextAction: str


class CodeAnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: str
    language: str
    context: str | None = None
    # 제출 전 분석 강화를 위한 optional 컨텍스트 (없어도 기존 동작 유지).
    requirements: list[str] = Field(default_factory=list)
    missionTitle: str | None = None
    missionDescription: str | None = None
    successCriteria: list[str] = Field(default_factory=list)
    submittedCode: list[SubmittedCodeSchema] = Field(default_factory=list)


class CodeAnalyzeResponse(BaseModel):
    summary: str
    explanation: str
    potentialIssues: list[str]
    improvementSuggestions: list[str]
    # 오답피드백 optional 필드 (요구사항/successCriteria 미제공 시 빈 배열).
    satisfiedRequirements: list[str] = Field(default_factory=list)
    missingRequirements: list[str] = Field(default_factory=list)
    incorrectParts: list[str] = Field(default_factory=list)
    wrongAnswerFeedback: list[str] = Field(default_factory=list)


class InterviewFeedbackRequest(BaseModel):
    question: str
    keyPoints: list[str]
    userAnswer: str


class InterviewFeedbackResponse(BaseModel):
    score: int
    includedKeyPoints: list[str]
    missingKeyPoints: list[str]
    feedback: str
    improvedAnswer: str


class JavaCodeGradingRequest(BaseModel):
    """자바 코딩테스트 채점 요청 스키마."""

    code: str = Field(..., description="채점할 자바 코드")
    question: str | None = Field(
        default=None,
        description="코딩테스트 문제. criteria 미제공 시 이 텍스트로 평가 기준을 자동 생성한다."
    )
    criteria: list[str] = Field(
        default_factory=list,
        description="평가 기준 리스트 (예: 정수형 변수 선언, 조건문 사용 등). "
                    "비어 있으면 question 기반으로 자동 생성한다."
    )

    @field_validator("code")
    @classmethod
    def validate_code(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("code는 비어 있을 수 없습니다.")
        return v.strip()

    @field_validator("question")
    @classmethod
    def normalize_question(cls, v: str | None) -> str | None:
        return (v or "").strip() or None

    @field_validator("criteria", mode="before")
    @classmethod
    def normalize_criteria(cls, v: Any) -> Any:
        if v is None:
            return []
        if isinstance(v, list):
            return [item.strip() for item in v if isinstance(item, str) and item.strip()]
        return v

    @model_validator(mode="after")
    def require_question_or_criteria(self) -> "JavaCodeGradingRequest":
        if not self.criteria and not self.question:
            raise ValueError("criteria가 비어 있으면 question이 필요합니다.")
        return self


class JavaCodeGradingResponse(BaseModel):
    """자바 코딩테스트 채점 응답 스키마."""
    
    is_correct: bool = Field(..., description="코드가 평가 기준을 모두 충족하는지 여부")
    score: int = Field(..., ge=0, le=100, description="0~100 사이의 점수")
    feedback: str = Field(..., description="채점 피드백 및 개선 제안")
    formatted_code: str = Field(..., description="포맷팅된 코드")
