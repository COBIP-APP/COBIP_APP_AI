"""퀴즈 자동 생성 및 심층 오답 해설 스키마."""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.rag import RetrievedReference

__all__ = [
    "QuizGenerateRequest",
    "QuizQuestionItem",
    "QuizGenerateResponseData",
    "QuizExplainRequest",
    "QuizExplainResponseData",
]


class QuizGenerateRequest(BaseModel):
    category: str = Field(
        ...,
        description="퀴즈 카테고리 (예: CS, Spring, Database, Network, OS, Java 등)",
        examples=["Database"],
    )
    difficulty: str = Field(
        default="중급",
        description="난이도 (초급/중급/고급 또는 beginner/intermediate/advanced)",
        examples=["중급"],
    )
    count: int = Field(
        default=3,
        ge=1,
        le=10,
        description="생성할 문제 수 (1~10)",
        examples=[3],
    )
    type: str = Field(
        default="multiple_choice",
        description="문제 유형 (multiple_choice/객관식, ox/OX, short_answer/단답형)",
        examples=["multiple_choice"],
    )
    keywords: list[str] | None = Field(
        default=None,
        description="특정 주제/키워드 (예: ['인덱스', 'B-Tree'])",
        examples=[["트랜잭션 격리수준", "MVCC"]],
    )

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("category는 비어 있을 수 없습니다.")
        return s

    @field_validator("difficulty")
    @classmethod
    def normalize_difficulty(cls, v: str) -> str:
        s = v.strip().lower()
        if s in ("초급", "beginner", "easy", "low"):
            return "초급"
        if s in ("고급", "advanced", "hard", "high"):
            return "고급"
        return "중급"

    @field_validator("type")
    @classmethod
    def normalize_type(cls, v: str) -> str:
        s = v.strip().lower()
        if s in ("객관식", "multiple_choice", "mc"):
            return "multiple_choice"
        if s in ("ox", "o/x", "true_false"):
            return "ox"
        if s in ("단답형", "short_answer", "sa"):
            return "short_answer"
        return "multiple_choice"


class QuizQuestionItem(BaseModel):
    id: int = Field(..., description="문제 번호 (1부터 시작)")
    question: str = Field(..., description="문제 지문")
    type: str = Field(..., description="문제 유형 (multiple_choice, ox, short_answer)")
    options: list[str] | None = Field(
        default=None,
        description="객관식 보기 목록 (4지선다인 경우 4개, OX인 경우 2개, 단답형은 None/빈 리스트)",
    )
    answer: str = Field(..., description="정답 (객관식/OX는 보기 번호 또는 문자열, 단답형은 정답 단어)")
    explanation: str = Field(..., description="실무 관점 핵심 해설 및 개념 설명")
    references: list[str] = Field(
        default_factory=list,
        description="참조한 Qdrant 지식 청크 출처 또는 키워드",
    )


class QuizGenerateResponseData(BaseModel):
    category: str
    difficulty: str
    type: str
    totalCount: int
    quizzes: list[QuizQuestionItem]
    retrievedKnowledgeCount: int = Field(
        default=0,
        description="문제 생성에 주입된 Qdrant 검색 지식 청크 수",
    )


class QuizExplainRequest(BaseModel):
    question: str = Field(..., description="풀이한 문제 내용")
    user_answer: str = Field(
        ...,
        alias="userAnswer",
        description="사용자가 제출한 답안",
    )
    correct_answer: str = Field(
        ...,
        alias="correctAnswer",
        description="실제 정답",
    )
    user_question: str | None = Field(
        default=None,
        alias="userQuestion",
        description="사용자의 추가 질문 또는 심층적으로 알고 싶은 점",
    )
    category: str | None = Field(
        default=None,
        description="문제 카테고리 (예: Spring, Network, Database 등)",
    )

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("question")
    @classmethod
    def validate_question(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("question은 비어 있을 수 없습니다.")
        return s


class QuizExplainResponseData(BaseModel):
    isCorrect: bool = Field(..., description="사용자 정답 일치 여부")
    summary: list[str] = Field(
        ...,
        description="3줄 핵심 요약 해설 (핵심 개념 정리)",
    )
    detailedExplanation: str = Field(
        ...,
        description="정답/오답 심층 분석 및 이론 배경",
    )
    troubleshootingTip: str = Field(
        ...,
        description="실무 트러블슈팅 팁 및 베스트 프랙티스",
    )
    references: list[RetrievedReference] = Field(
        default_factory=list,
        description="답변 근거로 사용된 Qdrant 지식 청크 목록",
    )
