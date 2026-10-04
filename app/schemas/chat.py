from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

__all__ = [
    "AgentPayload",
    "AgentTrace",
    "ChatRequest",
    "ChatResponseData",
]


class AgentTrace(BaseModel):
    """에이전트 관측·디버깅용 trace (/ai/chat data.agent.trace, 선택)."""

    classifier: str = Field(..., description="사용한 intent classifier 이름")
    handler: str = Field(..., description="실행한 handler 클래스 이름")
    llmIntentUsed: bool = Field(
        default=False,
        description="LLM intent classifier 호출 여부",
    )
    steps: list[str] = Field(
        default_factory=list,
        description="처리 단계 식별자 목록",
    )
    latencyMs: int = Field(
        default=0,
        ge=0,
        description="오케스트레이터 기준 전체 처리 시간(ms)",
    )
    toolCandidates: list[str] = Field(
        default_factory=list,
        description="intent에 매핑된 tool 이름 후보(실행 전 단계)",
    )


class AgentPayload(BaseModel):
    """룰 기반 에이전트 메타 (/ai/chat)."""

    enabled: bool = Field(default=True, description="에이전트 오케스트레이션 사용 여부")
    intent: str = Field(..., description="분류된 intent (GENERAL_CHAT 등)")
    mode: Literal["rule_based", "llm_assisted", "hybrid"] = Field(
        default="rule_based",
        description="intent 분류: rule_based 기본, LLM 보조 시 llm_assisted 또는 hybrid",
    )
    trace: AgentTrace | None = Field(
        default=None,
        description="관측용 trace; 구 클라이언트 호환을 위해 생략 가능",
    )


class ChatRequest(BaseModel):
    """챗봇 요청 (선택 context·useRag)."""

    message: str = Field(..., description="사용자 질문")
    context: str | None = Field(
        default=None,
        description="선택 참고 문맥 (사용자 직접 입력; 검색 결과 아님)",
    )
    useRag: bool | None = Field(
        default=None,
        description=(
            "false: RAG 미사용 / true: 질문 유형과 무관하게 검색 시도(threshold 적용) / "
            "null: TECHNICAL 질문일 때만 검색 시도"
        ),
    )

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("message는 비어 있을 수 없습니다.")
        return stripped


class ChatResponseData(BaseModel):
    answer: str
    source: Literal["ollama", "rag", "fallback"] = Field(
        ...,
        description=(
            "ollama: RAG 없이 LLM 생성 / rag: threshold 통과 문서 + LLM 생성 / "
            "fallback: LLM 실패·빈 응답으로 고정 문구 반환"
        ),
    )
    ragUsed: bool = Field(
        default=False,
        description="threshold 를 통과한 RAG context 로 LLM 답변이 생성된 경우에만 true",
    )
    references: list[Any] = Field(
        default_factory=list,
        description="실제로 prompt 에 들어간 RAG 근거(제목·내용·score 등); 미사용 시 빈 배열",
    )
    intent: Literal["SMALL_TALK", "TECHNICAL"] | None = Field(
        default=None,
        description="/ai/chat 질문 유형 판별 결과",
    )
    agent: AgentPayload | None = Field(
        default=None,
        description="(레거시) 에이전트 메타 (intent·mode 등)",
    )
