"""/ai/chat 질문 유형 판별 및 기존 챗봇 RAG 정책 함수 테스트."""

from __future__ import annotations

import pytest

from app.services.chat_query_policy import (
    classify_chat_intent,
    is_simple_concept_query,
    should_attempt_rag_for_general_chat,
)


@pytest.mark.parametrize(
    "message",
    [
        "안녕?",
        "안녕하세요",
        "고마워",
        "감사합니다",
        "너 뭐할 수 있어?",
        "ㅎㅇ",
        "hi",
        "Hello!",
        "잘가",
        "너 기능이 뭐야?",
        "기능이 뭐야?",
        "무슨 기능 있어?",
        "어떤 기능 있어?",
        "뭘 도와줄 수 있어?",
        "넌 뭘 해?",
        "너는 뭘 해?",
        "뭐 할 줄 알아?",
    ],
)
def test_classify_chat_intent_small_talk(message: str) -> None:
    assert classify_chat_intent(message) == "SMALL_TALK"


@pytest.mark.parametrize(
    "message",
    [
        "Docker 이미지와 컨테이너 차이가 뭐야?",
        "TCP 3-way handshake 설명해줘",
        "안녕 TCP 설명해줘",
        "안녕 스택 설명해줘",
        "고마워 근데 JPA는?",
        "자료구조 스택 설명해줘",
        "템플릿 메서드 패턴이 뭐야?",
        "오늘 날씨 어때?",
        "Docker 기능이 뭐야?",
        "TCP는 무슨 기능이야?",
        "",
    ],
)
def test_classify_chat_intent_technical(message: str) -> None:
    assert classify_chat_intent(message) == "TECHNICAL"


def test_simple_concept_query_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "RAG_ENABLED", True)
    assert is_simple_concept_query("int") is True
    assert is_simple_concept_query("Java int가 뭐야?") is True
    assert is_simple_concept_query("@RequestBody가 뭐야?") is True
    assert is_simple_concept_query("DTO가 뭐야?") is True
    assert is_simple_concept_query("우리 프로젝트에서 RAG 구조 설명해줘") is False
    assert should_attempt_rag_for_general_chat("int", True) is False
    assert should_attempt_rag_for_general_chat("우리 프로젝트 RAG 구조 설명해줘", None) is True
