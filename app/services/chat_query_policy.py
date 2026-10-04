"""챗봇 질문 유형 판별 — RAG 시도 여부·일반 개념 질문 구분."""

from __future__ import annotations

import re
from typing import Literal

from app.core.config import settings

__all__ = [
    "CHAT_INTENT_SMALL_TALK",
    "CHAT_INTENT_TECHNICAL",
    "ChatIntent",
    "classify_chat_intent",
    "has_rag_signal_in_message",
    "is_simple_concept_query",
    "should_attempt_rag_for_general_chat",
]

_RAG_HINTS = (
    "문서",
    "검색",
    "찾아",
    "근거",
    "자료",
    "참고",
    "rag",
    "프로젝트",
    "cobip",
    "기능템플릿",
    "기능 템플릿",
    "문서 기준",
    "문서기준",
)

_PROJECT_DOC_HINTS = (
    "우리 프로젝트",
    "프로젝트에서",
    "프로젝트 기준",
    "cobip",
    "cobip_ai",
)

_CONCEPT_QUESTION_RE = re.compile(
    r"(뭐야|뭔가요|무엇인가요|무엇인지|무엇이|이란|란\?|설명해|설명해줘|알려줘|정의|what\s+is|what's)",
    re.IGNORECASE,
)


def has_rag_signal_in_message(message: str) -> bool:
    lower = (message or "").lower()
    lower_nospace = lower.replace(" ", "")
    for hint in _RAG_HINTS:
        h = hint.lower()
        if h in lower or h.replace(" ", "") in lower_nospace:
            return True
    return False


def _has_project_doc_signal(message: str) -> bool:
    lower = (message or "").lower()
    return any(h in lower for h in _PROJECT_DOC_HINTS)


def is_simple_concept_query(message: str) -> bool:
    """짧은 개념 질문(int, DTO, @RequestBody 등) — RAG 없이 일반 LLM 우선."""

    text = (message or "").strip()
    if not text:
        return False
    if _has_project_doc_signal(text) or has_rag_signal_in_message(text):
        return False
    if _CONCEPT_QUESTION_RE.search(text):
        return True
    compact = text.replace(" ", "")
    if len(compact) <= 16 and " " not in text:
        return True
    if len(text) <= 24 and _CONCEPT_QUESTION_RE.search(text):
        return True
    return False


def should_attempt_rag_for_general_chat(message: str, use_rag: bool | None) -> bool:
    """GENERAL_CHAT에서 Retriever를 시도할지 여부 (RAG_ENABLED 유지)."""

    if not settings.RAG_ENABLED:
        return False
    if is_simple_concept_query(message):
        return False
    if use_rag is True:
        return True
    return has_rag_signal_in_message(message)


# ---------------------------------------------------------------------------
# /ai/chat 전용 질문 유형 판별 (SMALL_TALK / TECHNICAL)
# SMALL_TALK 는 보수적으로만 판정하고, 그 외는 모두 TECHNICAL 이다.
# ---------------------------------------------------------------------------

ChatIntent = Literal["SMALL_TALK", "TECHNICAL"]
CHAT_INTENT_SMALL_TALK: ChatIntent = "SMALL_TALK"
CHAT_INTENT_TECHNICAL: ChatIntent = "TECHNICAL"

_SMALL_TALK_MAX_COMPACT_LEN = 20

_SMALL_TALK_RE = re.compile(
    r"(안녕|하이|헬로|반가|반갑|ㅎㅇ|좋은\s*아침|잘\s*자|"
    r"고마|감사|땡큐|ㄱㅅ|"
    r"잘\s*가|바이|수고|또\s*봐|"
    r"(뭐|뭘)\s*해|누구(야|니|세요)|"
    r"(뭐|뭘|무엇을)\s*할\s*수|할\s*수\s*있는\s*(게|거|것)|도와\s*줄\s*수|"
    r"(뭐|뭘|무엇을)\s*할\s*줄|할\s*줄\s*아는|"
    r"기능(이|은)?\s*(뭐|무엇)|(무슨|어떤)\s*기능|"
    r"\b(hi|hello|hey|thanks|thank\s+you|thx|bye)\b)",
    re.IGNORECASE,
)

_SMALL_TALK_ASCII_WORDS = frozenset(
    {"hi", "hello", "hey", "thanks", "thank", "you", "thx", "bye", "ok", "okay"}
)

_ASCII_TOKEN_RE = re.compile(r"[a-z][a-z0-9+#.\-]*", re.IGNORECASE)

_TECH_KEYWORDS = (
    "설명",
    "차이",
    "방법",
    "어떻게",
    "원리",
    "예시",
    "구현",
    "사용법",
    "알려줘",
    "서버",
    "데이터베이스",
    "쿼리",
    "코드",
    "에러",
    "오류",
    "예외",
    "네트워크",
    "컨테이너",
    "함수",
    "클래스",
    "변수",
    "배포",
    "프로토콜",
    "알고리즘",
    "자료구조",
    "스택",
    "인덱스",
    "트랜잭션",
    "스프링",
    "자바",
    "파이썬",
    "도커",
    "리눅스",
    "운영체제",
    "프로그래밍",
)


def _has_technical_signal(text: str) -> bool:
    if re.search(r"\d", text):
        return True
    for token in _ASCII_TOKEN_RE.findall(text):
        if len(token) >= 2 and token.lower() not in _SMALL_TALK_ASCII_WORDS:
            return True
    return any(keyword in text for keyword in _TECH_KEYWORDS)


def classify_chat_intent(message: str) -> ChatIntent:
    """/ai/chat 질문을 SMALL_TALK(인사·감사·잡담) 또는 TECHNICAL 로 분류한다.

    짧고, 기술 신호가 없고, 인사/감사/작별/능력 질문 패턴과 일치할 때만 SMALL_TALK.
    """

    text = " ".join((message or "").split())
    if not text:
        return CHAT_INTENT_TECHNICAL
    if len(text.replace(" ", "")) > _SMALL_TALK_MAX_COMPACT_LEN:
        return CHAT_INTENT_TECHNICAL
    if _has_technical_signal(text):
        return CHAT_INTENT_TECHNICAL
    if _SMALL_TALK_RE.search(text):
        return CHAT_INTENT_SMALL_TALK
    return CHAT_INTENT_TECHNICAL
