"""퀴즈 자동 생성 및 심층 오답 해설 스키마."""

from __future__ import annotations

from typing import Any

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.schemas.rag import RetrievedReference

__all__ = [
    "QUIZ_TYPES",
    "QuizExplainRequest",
    "QuizExplainResponseData",
    "QuizGenerateRequest",
    "QuizGenerateResponseData",
    "QuizQuestionItem",
    "normalize_quiz_type",
]


QUIZ_TYPES: tuple[str, ...] = ("multiple_choice", "ox", "short_answer", "blank", "descriptive")

_QUIZ_TYPE_ALIASES: dict[str, str] = {
    "multiple_choice": "multiple_choice",
    "multiplechoice": "multiple_choice",
    "mc": "multiple_choice",
    "객관식": "multiple_choice",
    "4지선다": "multiple_choice",
    "ox": "ox",
    "o/x": "ox",
    "true_false": "ox",
    "short_answer": "short_answer",
    "sa": "short_answer",
    "단답형": "short_answer",
    "blank": "blank",
    "fill_in_the_blank": "blank",
    "fill_in_blank": "blank",
    "빈칸": "blank",
    "빈칸채우기": "blank",
    "descriptive": "descriptive",
    "essay": "descriptive",
    "서술형": "descriptive",
}


def normalize_quiz_type(value: Any) -> str | None:
    """퀴즈 유형 별칭(대소문자/한글 포함)을 표준 소문자 유형으로 변환. 알 수 없으면 None."""
    if not isinstance(value, str):
        return None
    key = value.strip().lower().replace("-", "_").replace(" ", "_")
    return _QUIZ_TYPE_ALIASES.get(key)


class QuizGenerateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("user_id", "userId"),
        description="요청 사용자 ID (로깅/추적용)",
    )
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
    domain: str | None = Field(
        default=None,
        description="세부 기술 도메인. 공백 또는 쉼표로 구분된 각 용어가 RAG 검색 초점어로 사용된다",
        examples=["AWS EC2 Docker ECR GitHub Actions CI/CD"],
    )
    quiz_types: list[str] | None = Field(
        default=None,
        validation_alias=AliasChoices("quiz_types", "quizTypes"),
        description="생성할 문제 유형 목록. 지정 시 type/count 대신 유형별 count_per_type개씩 생성",
        examples=[["MULTIPLE_CHOICE", "BLANK", "DESCRIPTIVE"]],
    )
    count_per_type: int | None = Field(
        default=None,
        ge=1,
        le=5,
        validation_alias=AliasChoices("count_per_type", "countPerType"),
        description="quiz_types 각 유형별 생성 문제 수 (1~5, 기본 1)",
        examples=[1],
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
        return normalize_quiz_type(v) or "multiple_choice"

    @field_validator("domain")
    @classmethod
    def normalize_domain(cls, v: str | None) -> str | None:
        if v is None:
            return None
        s = " ".join(v.split())
        return s or None

    @field_validator("quiz_types")
    @classmethod
    def normalize_quiz_types(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        normalized: list[str] = []
        for raw in v:
            t = normalize_quiz_type(raw)
            if t is None:
                raise ValueError(f"지원하지 않는 quiz_type입니다: {raw!r} (지원: {', '.join(QUIZ_TYPES)})")
            if t not in normalized:
                normalized.append(t)
        if not normalized:
            raise ValueError("quiz_types는 비어 있을 수 없습니다.")
        return normalized

    @model_validator(mode="after")
    def validate_plan_size(self) -> QuizGenerateRequest:
        if self.quiz_types and len(self.quiz_types) * (self.count_per_type or 1) > 15:
            raise ValueError("quiz_types × count_per_type은 15문제를 넘을 수 없습니다.")
        return self

    def type_plan(self) -> list[tuple[str, int]]:
        """생성할 (유형, 개수) 목록. quiz_types가 없으면 기존 type/count를 그대로 사용한다."""
        if self.quiz_types:
            per_type = self.count_per_type or 1
            return [(t, per_type) for t in self.quiz_types]
        return [(self.type, self.count)]

    @property
    def total_count(self) -> int:
        return sum(n for _, n in self.type_plan())


class QuizQuestionItem(BaseModel):
    id: int = Field(..., description="문제 번호 (1부터 시작)")
    question: str = Field(..., description="문제 지문")
    type: str = Field(..., description="문제 유형 (multiple_choice, ox, short_answer, blank, descriptive)")
    options: list[str] | None = Field(
        default=None,
        description="객관식 보기 목록 (4지선다인 경우 4개, OX인 경우 2개, 단답형은 None/빈 리스트)",
    )
    answer: str = Field(
        ...,
        description="정답 (객관식/OX는 보기 번호, 단답형/빈칸은 정답 단어(빈칸 여러 개면 ', '로 구분), 서술형은 모범 답안)",
    )
    explanation: str = Field(..., description="실무 관점 핵심 해설 및 개념 설명")
    references: list[str] = Field(
        default_factory=list,
        description="참조한 Qdrant 지식 청크 출처 또는 키워드",
    )
    gradingKeywords: list[str] | None = Field(
        default=None,
        description="서술형 채점 핵심 키워드 (서술형 외 유형은 None)",
    )


class QuizGenerateResponseData(BaseModel):
    category: str
    difficulty: str
    type: str = Field(..., description="단일 유형이면 해당 유형, quiz_types가 여러 개면 'mixed'")
    domain: str | None = None
    quizTypes: list[str] = Field(default_factory=list, description="요청된 문제 유형(표준 소문자) 목록")
    totalCount: int
    quizzes: list[QuizQuestionItem]
    retrievedKnowledgeCount: int = Field(
        default=0,
        description="문제 생성에 주입된 Qdrant 검색 지식 청크 수",
    )
    retrievedSources: list[str] = Field(
        default_factory=list,
        description="문제 생성에 주입된 지식 청크의 source_file(없으면 title) 목록 (검색 순위 순)",
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
