"""/ai/chat (RagChatService) intent·threshold 라우팅 테스트."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.chat import ChatRequest
from app.schemas.rag import RetrievedReference
from app.services.rag_chat_service import CHAT_FALLBACK_ANSWER, RagChatService

_LLM_ANSWER = "LLM이 생성한 답변입니다."


def _ref(score: float | None, title: str = "문서", content: str | None = None) -> RetrievedReference:
    return RetrievedReference(
        id=title,
        title=title,
        content=content if content is not None else f"{title} 본문 내용입니다. 핵심 개념을 설명합니다.",
        score=score,
        sourceType="manual",
    )


class FakeRetriever:
    def __init__(self, refs: list[RetrievedReference] | None = None, error: Exception | None = None) -> None:
        self.refs = refs or []
        self.error = error
        self.calls: list[tuple[str, int | None]] = []

    def retrieve(self, query: str, top_k: int | None = None) -> list[RetrievedReference]:
        self.calls.append((query, top_k))
        if self.error is not None:
            raise self.error
        return list(self.refs)


class FakeLLM:
    def __init__(self, answer: str = _LLM_ANSWER, error: Exception | None = None) -> None:
        self.answer = answer
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def generate_text(
        self,
        prompt: str,
        system_prompt: str | None = None,
        timeout_seconds: int | None = None,
    ) -> str:
        self.calls.append({"prompt": prompt, "system_prompt": system_prompt})
        if self.error is not None:
            raise self.error
        return self.answer


@pytest.fixture(autouse=True)
def chat_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "CHAT_RAG_MIN_SCORE", 0.55)
    monkeypatch.setattr(settings, "RAG_TOP_K", 3)


def _run(
    message: str,
    *,
    retriever: FakeRetriever | None = None,
    llm: FakeLLM | None = None,
    use_rag: bool | None = None,
) -> tuple[dict[str, Any], FakeRetriever, FakeLLM]:
    retriever = retriever or FakeRetriever([_ref(0.9)])
    llm = llm or FakeLLM()
    service = RagChatService(llm=llm, retriever=retriever)
    result = asyncio.run(service.answer(ChatRequest(message=message, useRag=use_rag)))
    return result, retriever, llm


@pytest.mark.parametrize("message", ["안녕?", "고마워", "너 뭐할 수 있어?"])
def test_small_talk_skips_retriever(message: str) -> None:
    result, retriever, llm = _run(message)

    assert retriever.calls == []
    assert result["source"] == "ollama"
    assert result["ragUsed"] is False
    assert result["references"] == []
    assert result["intent"] == "SMALL_TALK"
    assert result["answer"] == _LLM_ANSWER
    assert len(llm.calls) == 1
    assert "[참고 지식(Context)]" not in llm.calls[0]["prompt"]
    assert "챗봇 도우미" in llm.calls[0]["system_prompt"]


@pytest.mark.parametrize(
    ("message", "score", "title"),
    [
        ("Docker 이미지와 컨테이너 차이가 뭐야?", 0.72, "Docker 이미지와 컨테이너"),
        ("TCP 3-way handshake 설명해줘", 0.68, "TCP 연결 수립"),
    ],
)
def test_technical_question_with_relevant_doc_uses_rag(message: str, score: float, title: str) -> None:
    result, retriever, llm = _run(message, retriever=FakeRetriever([_ref(score, title)]))

    assert retriever.calls == [(message, 3)]
    assert result["source"] == "rag"
    assert result["ragUsed"] is True
    assert result["intent"] == "TECHNICAL"
    assert [r["title"] for r in result["references"]] == [title]
    assert result["references"][0]["score"] == score
    prompt = llm.calls[0]["prompt"]
    assert "[참고 지식(Context)]" in prompt
    assert title in prompt
    assert prompt.endswith(f"[질문]: {message}")
    assert "억지로 사용하지 말고" in llm.calls[0]["system_prompt"]


def test_technical_question_below_threshold_uses_general_llm() -> None:
    message = "Docker 이미지와 컨테이너 차이가 뭐야?"
    result, retriever, llm = _run(message, retriever=FakeRetriever([_ref(0.44, "Network 문서")]))

    assert len(retriever.calls) == 1
    assert result["source"] == "ollama"
    assert result["ragUsed"] is False
    assert result["references"] == []
    assert "[참고 지식(Context)]" not in llm.calls[0]["prompt"]
    assert "Network 문서" not in llm.calls[0]["prompt"]
    assert "일반 개발 지식" in llm.calls[0]["system_prompt"]


def test_mixed_scores_keep_only_docs_above_threshold() -> None:
    refs = [_ref(0.70, "관련 문서"), _ref(0.40, "무관 문서"), _ref(0.55, "경계 문서")]
    result, _, llm = _run("TCP 3-way handshake 설명해줘", retriever=FakeRetriever(refs))

    assert result["source"] == "rag"
    assert [r["title"] for r in result["references"]] == ["관련 문서", "경계 문서"]
    assert "무관 문서" not in llm.calls[0]["prompt"]


def test_none_score_and_empty_content_are_dropped() -> None:
    refs = [_ref(None, "점수 없음"), _ref(0.9, "빈 문서", content="   ")]
    result, _, llm = _run("TCP 3-way handshake 설명해줘", retriever=FakeRetriever(refs))

    assert result["source"] == "ollama"
    assert result["ragUsed"] is False
    assert result["references"] == []
    assert "[참고 지식(Context)]" not in llm.calls[0]["prompt"]


def test_retriever_error_falls_back_to_general_llm() -> None:
    retriever = FakeRetriever(error=RuntimeError("qdrant down"))
    result, _, llm = _run("TCP 3-way handshake 설명해줘", retriever=retriever)

    assert len(retriever.calls) == 1
    assert result["source"] == "ollama"
    assert result["ragUsed"] is False
    assert result["references"] == []
    assert result["answer"] == _LLM_ANSWER
    assert len(llm.calls) == 1


def test_llm_error_returns_fixed_fallback_without_doc_text() -> None:
    refs = [_ref(0.9, "Docker 문서", content="Docker 이미지는 읽기 전용 템플릿이다. 컨테이너는 실행 인스턴스다.")]
    result, _, _ = _run(
        "Docker 이미지와 컨테이너 차이가 뭐야?",
        retriever=FakeRetriever(refs),
        llm=FakeLLM(error=RuntimeError("LLM 호출 네트워크 오류")),
    )

    assert result["source"] == "fallback"
    assert result["ragUsed"] is False
    assert result["references"] == []
    assert result["answer"] == CHAT_FALLBACK_ANSWER
    assert "Docker 이미지는" not in result["answer"]


def test_small_talk_llm_empty_response_returns_fallback() -> None:
    result, _, _ = _run("안녕?", llm=FakeLLM(answer="   "))

    assert result["source"] == "fallback"
    assert result["ragUsed"] is False
    assert result["answer"] == CHAT_FALLBACK_ANSWER


def test_use_rag_false_skips_retriever_for_technical_question() -> None:
    result, retriever, llm = _run("TCP 3-way handshake 설명해줘", use_rag=False)

    assert retriever.calls == []
    assert result["source"] == "ollama"
    assert result["ragUsed"] is False
    assert "[참고 지식(Context)]" not in llm.calls[0]["prompt"]


def test_use_rag_true_small_talk_retrieves_but_applies_threshold() -> None:
    retriever = FakeRetriever([_ref(0.43, "Network 문서"), _ref(0.44, "Network 문서2")])
    result, _, llm = _run("안녕?", retriever=retriever, use_rag=True)

    assert len(retriever.calls) == 1
    assert result["source"] == "ollama"
    assert result["ragUsed"] is False
    assert result["references"] == []
    assert "Network" not in llm.calls[0]["prompt"]
    assert "챗봇 도우미" in llm.calls[0]["system_prompt"]


def test_use_rag_true_small_talk_with_relevant_doc_uses_rag() -> None:
    result, _, _ = _run("안녕?", retriever=FakeRetriever([_ref(0.8, "인사 문서")]), use_rag=True)

    assert result["source"] == "rag"
    assert result["ragUsed"] is True


def test_rag_synthesis_is_removed() -> None:
    import app.services.rag_chat_service as module

    assert not hasattr(module, "_rag_synthesis_answer")


# --- endpoint ---------------------------------------------------------------


def _patch_service(monkeypatch: pytest.MonkeyPatch, retriever: FakeRetriever, llm: FakeLLM) -> None:
    monkeypatch.setattr(
        "app.api.routes.chat.RagChatService",
        lambda: RagChatService(llm=llm, retriever=retriever),
    )


def test_chat_endpoint_small_talk(monkeypatch: pytest.MonkeyPatch) -> None:
    retriever = FakeRetriever([_ref(0.44)])
    _patch_service(monkeypatch, retriever, FakeLLM(answer="안녕하세요! 무엇을 도와드릴까요?"))

    resp = TestClient(app).post("/ai/chat", json={"message": "안녕?"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["message"] == "챗봇 답변이 생성되었습니다."
    assert body["data"] == {
        "answer": "안녕하세요! 무엇을 도와드릴까요?",
        "source": "ollama",
        "ragUsed": False,
        "references": [],
        "intent": "SMALL_TALK",
    }
    assert retriever.calls == []


def test_chat_endpoint_rag(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_service(monkeypatch, FakeRetriever([_ref(0.72, "Docker 문서")]), FakeLLM())

    resp = TestClient(app).post("/ai/chat", json={"message": "Docker 이미지와 컨테이너 차이가 뭐야?"})

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["source"] == "rag"
    assert data["ragUsed"] is True
    assert data["references"][0]["title"] == "Docker 문서"


def test_chat_endpoint_fallback_keeps_success_true(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_service(monkeypatch, FakeRetriever([_ref(0.9)]), FakeLLM(error=RuntimeError("down")))

    resp = TestClient(app).post("/ai/chat", json={"message": "TCP 3-way handshake 설명해줘"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["message"] == "챗봇 답변 생성에 실패하여 기본 안내 문구를 반환했습니다."
    assert body["data"]["source"] == "fallback"
    assert body["data"]["ragUsed"] is False
    assert body["data"]["references"] == []
    assert body["data"]["answer"] == CHAT_FALLBACK_ANSWER


def test_chat_endpoint_blank_message_422() -> None:
    resp = TestClient(app).post("/ai/chat", json={"message": "   "})

    assert resp.status_code == 422
