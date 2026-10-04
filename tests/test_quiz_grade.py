"""Quiz grade normalizer + grader + API contract tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.models.enums import DifficultyLevel, QuestionType
from app.schemas.evaluation import QuizGradeRequest
from app.services.evaluation_service import EvaluationService

_RESPONSE_FIELDS = frozenset(
    {
        "isCorrect",
        "score",
        "feedback",
        "correctAnswer",
        "explanation",
        "relatedSection",
    }
)


@pytest.fixture(autouse=True)
def _rule_grading_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """이 파일은 기존 rule 채점 계약을 검증한다. AI 채점 경로는 test_quiz_grade_ai.py."""
    monkeypatch.setattr(settings, "QUIZ_GRADE_AI_ENABLED", False)


def _quiz_payload(**overrides: object) -> dict:
    base = {
        "featureName": "회원가입",
        "question": {
            "questionId": "Q-001",
            "type": QuestionType.SHORT_ANSWER.value,
            "question": "비밀번호는 어떻게 저장하나?",
            "choices": None,
            "answer": "BCrypt 해시",
            "explanation": "평문 금지",
            "relatedSection": "requirements",
            "difficulty": DifficultyLevel.BEGINNER.value,
        },
        "userAnswer": "BCrypt 해시",
        "relatedApiSpecs": [
            {
                "apiName": "회원가입",
                "method": "POST",
                "endpoint": "/api/auth/signup",
                "description": "signup",
                "requestBody": {},
                "responseBody": {},
                "status": 201,
                "requestHeaders": {"Content-Type": "application/json"},
                "requestFields": [{"fieldName": "email", "type": "string", "required": True}],
                "responseFields": [{"fieldName": "userId", "type": "number", "required": True}],
                "errorResponses": [
                    {"statusCode": 409, "errorCode": "DUPLICATE_EMAIL", "message": "duplicate"}
                ],
            }
        ],
    }
    base.update(overrides)
    return base


class TestQuizGradeService:
    def test_exact_match_correct(self) -> None:
        req = QuizGradeRequest(**_quiz_payload())
        result = EvaluationService().grade_quiz(req)
        assert result.isCorrect is True
        assert result.score == 100

    def test_case_and_space_insensitive(self) -> None:
        req = QuizGradeRequest(**_quiz_payload(userAnswer="  bcrypt   HASH "))
        result = EvaluationService().grade_quiz(req)
        assert result.isCorrect is True

    def test_keyword_partial_credit(self) -> None:
        req = QuizGradeRequest(**_quiz_payload(userAnswer="BCrypt로 해시 저장"))
        result = EvaluationService().grade_quiz(req)
        assert result.isCorrect is True
        assert result.score >= 70

    def test_wrong_answer_fails(self) -> None:
        req = QuizGradeRequest(**_quiz_payload(userAnswer="평문 저장"))
        result = EvaluationService().grade_quiz(req)
        assert result.isCorrect is False
        assert "오답" in result.feedback
        assert "평문 저장" in result.feedback

    def test_feedback_includes_section_context(self) -> None:
        req = QuizGradeRequest(**_quiz_payload())
        result = EvaluationService().grade_quiz(req)
        assert "requirements" in result.feedback

    def test_explanation_fallback_uses_correct_answer_and_section(self) -> None:
        payload = _quiz_payload()
        payload["question"] = dict(payload["question"])
        payload["question"]["explanation"] = ""
        req = QuizGradeRequest(**payload)
        result = EvaluationService().grade_quiz(req)
        assert "BCrypt 해시" in result.explanation
        assert "requirements" in result.explanation

    def test_fe_related_api_specs_shape_no_422(self) -> None:
        QuizGradeRequest(**_quiz_payload())

    def test_wrong_feedback_is_coaching_style(self) -> None:
        """완전 오답: 오답 사유 + 정답 핵심 + 보완 포인트 + 개선 답안 예시 포함."""
        req = QuizGradeRequest(**_quiz_payload(userAnswer="그냥 저장한다"))
        result = EvaluationService().grade_quiz(req)
        feedback = result.feedback
        assert result.isCorrect is False
        # 본문에 사용자 답변과 정답이 함께 언급된다.
        assert "그냥 저장한다" in feedback
        assert result.correctAnswer in feedback
        # 코칭 구조 블록이 모두 존재한다.
        assert "[보완할 포인트]" in feedback
        assert "[개선 답안 예시]" in feedback
        # 보완 포인트는 키워드 나열이 아니라 문장 설명이어야 한다.
        assert "저장하는 방식입니다" in feedback or "사용합니다" in feedback
        # 단순 한 줄 피드백보다 충분히 길다(체감 가능).
        assert len(feedback) > 120

    def test_partial_feedback_acknowledges_and_guides(self) -> None:
        """부분 정답(키워드 일부 포함): 포함 개념 인정 + 빠진 개념 안내."""
        req = QuizGradeRequest(
            **_quiz_payload(
                question={
                    "questionId": "Q-002",
                    "type": QuestionType.OUTPUT_PREDICTION.value,
                    "question": "로그인 흐름의 계층 역할을 설명하라",
                    "choices": None,
                    "answer": "controller service repository dto",
                    "explanation": "계층 분리",
                    "relatedSection": "flow",
                    "difficulty": DifficultyLevel.BEGINNER.value,
                },
                userAnswer="controller 와 service 사용",
            )
        )
        result = EvaluationService().grade_quiz(req)
        # 부분 정답 분기(불합격이지만 일부 키워드 일치)
        assert result.isCorrect is False
        assert result.score >= 50
        assert "부분 정답" in result.feedback
        assert "[보완할 포인트]" in result.feedback
        assert "[개선 답안 예시]" in result.feedback

    def test_correct_feedback_explains_and_extends(self) -> None:
        req = QuizGradeRequest(**_quiz_payload())
        result = EvaluationService().grade_quiz(req)
        assert result.isCorrect is True
        assert "정답" in result.feedback
        # 정답일 때도 왜 좋은지 + 추가 학습 포인트를 제공한다.
        assert "[추가로 알면 좋은 포인트]" in result.feedback

    def test_explanation_fallback_uses_correct_answer_and_section(self) -> None:
        payload = _quiz_payload()
        payload["question"] = dict(payload["question"])
        payload["question"]["explanation"] = ""
        req = QuizGradeRequest(**payload)
        result = EvaluationService().grade_quiz(req)
        assert result.correctAnswer in result.explanation
        assert "requirements" in result.explanation


class TestQuizGradeApi:
    def test_endpoint_contract(self) -> None:
        client = TestClient(app)
        resp = client.post("/ai/quiz/grade", json=_quiz_payload(userAnswer="bcrypt hash"))
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("success") is True
        data = body["data"]
        assert set(data.keys()) == _RESPONSE_FIELDS
        assert data["isCorrect"] is True
