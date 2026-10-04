"""/ai/quiz/grade rule + AI criteria 혼합 채점 테스트 (Fake LLM, 실제 Ollama 호출 없음)."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import settings
from app.main import app
from app.models.enums import QuestionType
from app.prompts.quiz_grade_prompts import (
    QUIZ_ANSWER_GRADING_SYSTEM_PROMPT,
    QUIZ_CRITERIA_SYSTEM_PROMPT,
)
from app.schemas.evaluation import QuizGradeCriterion, QuizGradeRequest
from app.services import evaluation_service as evaluation_service_module
from app.services.evaluation_service import EvaluationService

_LEGACY_FIELDS = {"isCorrect", "score", "feedback", "correctAnswer", "explanation", "relatedSection"}
_USER_SECRET = "학생답안-원문-로그금지"


class FakeLLM:
    """순서대로 응답(str) 또는 예외를 돌려주고 호출 인자를 기록한다."""

    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def generate_text(
        self,
        prompt: str,
        system_prompt: str | None = None,
        *,
        timeout_seconds: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        self.calls.append(
            {
                "prompt": prompt,
                "system_prompt": system_prompt,
                "timeout_seconds": timeout_seconds,
                "max_tokens": max_tokens,
            }
        )
        if not self.responses:
            raise AssertionError("unexpected LLM call")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return str(response)


@pytest.fixture(autouse=True)
def _ai_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "QUIZ_GRADE_AI_ENABLED", True)
    monkeypatch.setattr(settings, "OLLAMA_BASE_URL", "http://fake-ollama:11434/v1")
    monkeypatch.setattr(settings, "LLM_TIMEOUT_SECONDS", 60)


def _criteria_json(*items: tuple[str, object]) -> str:
    return json.dumps(
        {
            "criteria": [
                {"id": i, "description": desc, "weight": weight}
                for i, (desc, weight) in enumerate(items, start=1)
            ]
        },
        ensure_ascii=False,
    )


CRITERIA_3 = _criteria_json(
    ("정수형 변수 score에 65를 저장했는가", 30),
    ("if문으로 score가 60보다 큰지 비교했는가", 40),
    ("조건이 참이면 합격을 출력했는가", 30),
)


def _grade_json(scores: dict[int, int], *, total: int | None = None, result: str = "CORRECT",
                feedback: str = "AI 피드백입니다.") -> str:
    return json.dumps(
        {
            "criteriaResults": [
                {"id": cid, "passed": True, "score": score, "feedback": f"기준 {cid} 평가"}
                for cid, score in scores.items()
            ],
            "totalScore": sum(scores.values()) if total is None else total,
            "result": result,
            "feedback": feedback,
        },
        ensure_ascii=False,
    )


def _request(
    q_type: str = "descriptive",
    answer: str = "int score = 65; if (score > 60) 합격 출력",
    user_answer: str = "score에 65를 넣고 60보다 크면 합격을 출력한다",
    **extra: object,
) -> QuizGradeRequest:
    question = {
        "questionId": "Q-1",
        "type": q_type,
        "question": "score가 60보다 크면 합격을 출력하는 코드를 설명하라",
        "choices": extra.pop("choices", None),
        "answer": answer,
        "explanation": "65 > 60 이므로 합격",
        "difficulty": "beginner",
    }
    return QuizGradeRequest(featureName="Java 기초", question=question, userAnswer=user_answer, **extra)


def _grade(request: QuizGradeRequest, *responses: object) -> tuple[SimpleNamespace, FakeLLM]:
    """외부 응답(6개 필드) + 내부 판정(gradingMethod/result/criteriaResults)을 함께 돌려준다."""
    fake = FakeLLM(*responses)
    outcome = EvaluationService(llm_service=fake)._grade_quiz_outcome(request)
    graded = SimpleNamespace(
        **outcome.response.model_dump(),
        gradingMethod=outcome.grading_method,
        result=outcome.result,
        criteriaResults=outcome.criteria_results,
    )
    return graded, fake


# ---------------------------------------------------------------------------
# 라우팅 / rule
# ---------------------------------------------------------------------------
class TestRouting:
    def test_multiple_choice_is_rule_without_llm(self) -> None:
        result, fake = _grade(
            _request("multiple_choice", answer="2", user_answer="2. TreeMap",
                     choices=["1. HashMap", "2. TreeMap"])
        )
        assert result.gradingMethod == "rule"
        assert fake.calls == []

    @pytest.mark.parametrize("user_answer", ["O", "o", "1", "1번", "1. O", "참", "true"])
    def test_ox_correct_variants(self, user_answer: str) -> None:
        result, fake = _grade(
            _request("ox", answer="1", user_answer=user_answer, choices=["1. O", "2. X"])
        )
        assert (result.isCorrect, result.score, result.gradingMethod) == (True, 100, "rule")
        assert fake.calls == []

    @pytest.mark.parametrize("user_answer", ["X", "2", "거짓", "2. X"])
    def test_ox_wrong_variants(self, user_answer: str) -> None:
        result, fake = _grade(
            _request("ox", answer="1", user_answer=user_answer, choices=["1. O", "2. X"])
        )
        assert (result.isCorrect, result.score, result.gradingMethod) == (False, 0, "rule")
        assert fake.calls == []

    def test_ox_letter_answer_without_choices(self) -> None:
        result, _ = _grade(_request("ox", answer="O", user_answer="맞다"))
        assert result.score == 100

    def test_blank_exact_match_skips_ai(self) -> None:
        result, fake = _grade(_request("fill_blank", answer="MVCC", user_answer=" mvcc "))
        assert (result.isCorrect, result.score, result.gradingMethod) == (True, 100, "rule")
        assert result.result is None
        assert fake.calls == []

    def test_multi_blank_exact_match_per_blank(self) -> None:
        result, fake = _grade(
            _request("fill_blank", answer="MVCC, 스냅샷", user_answer="mvcc,스냅샷")
        )
        assert result.gradingMethod == "rule"
        assert fake.calls == []

    def test_multi_blank_swapped_order_goes_to_ai(self) -> None:
        _, fake = _grade(
            _request("fill_blank", answer="MVCC, 스냅샷", user_answer="스냅샷, MVCC"),
            _criteria_json(("첫 번째 빈칸에 MVCC를 채웠는가", 50), ("두 번째 빈칸에 스냅샷을 채웠는가", 50)),
            _grade_json({1: 0, 2: 0}),
        )
        assert len(fake.calls) == 2

    @pytest.mark.parametrize("q_type", ["descriptive", "output_prediction", "code_fill"])
    def test_exact_match_skips_ai_for_descriptive_and_code(self, q_type: str) -> None:
        result, fake = _grade(_request(q_type, answer="합격", user_answer="합격."))
        assert (result.score, result.gradingMethod) == (100, "rule")
        assert fake.calls == []

    def test_empty_user_answer_is_zero_without_llm(self) -> None:
        result, fake = _grade(_request("descriptive", user_answer="   "))
        assert (result.isCorrect, result.score, result.gradingMethod) == (False, 0, "rule")
        assert fake.calls == []

    def test_ai_disabled_uses_rule(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "QUIZ_GRADE_AI_ENABLED", False)
        result, fake = _grade(_request("descriptive"))
        assert result.gradingMethod == "rule"
        assert fake.calls == []

    def test_empty_ollama_url_uses_rule(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "OLLAMA_BASE_URL", "")
        result, fake = _grade(_request("descriptive"))
        assert result.gradingMethod == "rule"
        assert fake.calls == []

    @pytest.mark.parametrize(
        "q_type",
        ["short_answer", "fill_blank", "descriptive", "output_prediction", "code_error_find", "code_fill"],
    )
    def test_ai_types_use_two_stage_ai(self, q_type: str) -> None:
        result, fake = _grade(_request(q_type), CRITERIA_3, _grade_json({1: 30, 2: 40, 3: 30}))
        assert result.gradingMethod == "ai"
        assert len(fake.calls) == 2
        assert fake.calls[0]["system_prompt"] == QUIZ_CRITERIA_SYSTEM_PROMPT
        assert fake.calls[1]["system_prompt"] == QUIZ_ANSWER_GRADING_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# 생성 응답 형식 수용 (normalizer)
# ---------------------------------------------------------------------------
class TestGeneratedPayload:
    def test_generated_item_shape_is_accepted(self) -> None:
        req = QuizGradeRequest(
            category="DB",
            difficulty="중급",
            question={
                "id": 3,
                "type": "blank",
                "question": "PostgreSQL의 동시성 제어 방식은 ____ 이다.",
                "options": None,
                "answer": "MVCC",
                "explanation": None,
                "references": ["db.md"],
            },
            userAnswer="mvcc",
        )
        assert req.featureName == "DB"
        assert req.question.questionId == "3"
        assert req.question.type == QuestionType.FILL_BLANK
        assert req.question.difficulty.value == "intermediate"
        assert req.question.explanation == ""

    def test_options_become_choices(self) -> None:
        req = QuizGradeRequest(
            question={"id": 1, "type": "OX", "question": "q", "options": ["1. O", "2. X"], "answer": "1"},
            userAnswer="1",
        )
        assert req.question.type == QuestionType.OX
        assert req.question.choices == ["1. O", "2. X"]

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("DESCRIPTIVE", QuestionType.DESCRIPTIVE),
            ("서술형", QuestionType.DESCRIPTIVE),
            ("essay", QuestionType.DESCRIPTIVE),
            ("MULTIPLE_CHOICE", QuestionType.MULTIPLE_CHOICE),
            ("빈칸", QuestionType.FILL_BLANK),
            ("code_error_find", QuestionType.CODE_ERROR_FIND),
        ],
    )
    def test_type_aliases(self, raw: str, expected: QuestionType) -> None:
        req = QuizGradeRequest(
            featureName="f", question={"id": 1, "type": raw, "question": "q", "answer": "a"}, userAnswer="a"
        )
        assert req.question.type == expected

    def test_unknown_type_still_rejected(self) -> None:
        with pytest.raises(ValidationError):
            QuizGradeRequest(
                featureName="f", question={"id": 1, "type": "matching", "question": "q", "answer": "a"},
                userAnswer="a",
            )

    def test_grading_keywords_lifted_and_used_as_reference(self) -> None:
        request = QuizGradeRequest(
            featureName="DevOps",
            question={
                "id": 1, "type": "descriptive", "question": "장애 점검 순서를 설명하시오",
                "answer": "상태 확인 후 로그와 포트를 점검한다", "explanation": "",
                "gradingKeywords": ["상태 확인", "로그", " "],
            },
            userAnswer=f"로그를 본다 {_USER_SECRET}",
        )
        assert request.gradingKeywords == ["상태 확인", "로그"]
        _, fake = _grade(request, CRITERIA_3, _grade_json({1: 30, 2: 40, 3: 30}))
        criteria_prompt = fake.calls[0]["prompt"]
        assert "참고 키워드" in criteria_prompt
        assert "상태 확인, 로그" in criteria_prompt
        assert _USER_SECRET not in criteria_prompt

    def test_legacy_feature_template_payload_unchanged(self) -> None:
        req = QuizGradeRequest(
            featureName="회원가입",
            question={
                "questionId": "Q-001", "type": "short_answer", "question": "q",
                "correctAnswer": "BCrypt 해시", "explanation": "e", "difficulty": "beginner",
            },
            answer="BCrypt 해시",
        )
        assert req.question.answer == "BCrypt 해시"
        assert req.userAnswer == "BCrypt 해시"
        assert req.question.questionId == "Q-001"
        assert req.gradingKeywords is None


# ---------------------------------------------------------------------------
# criteria 검증
# ---------------------------------------------------------------------------
class TestCriteriaValidation:
    def test_weights_normalized_to_100(self) -> None:
        criteria = EvaluationService._validate_criteria(
            json.loads(_criteria_json(("A 값을 저장했는가", 30), ("B 조건을 비교했는가", 30), ("C 결과를 출력했는가", 30)))
        )
        assert sum(c.weight for c in criteria) == 100
        assert sorted(c.weight for c in criteria) == [33, 33, 34]

    def test_vague_empty_and_invalid_criteria_removed(self) -> None:
        criteria = EvaluationService._validate_criteria(
            json.loads(
                _criteria_json(
                    ("문제를 잘 이해했는가", 20),
                    ("코드를 잘 작성했는가", 20),
                    ("", 20),
                    ("score에 65를 저장했는가", "abc"),
                    ("score에 65를 저장했는가", 0),
                    ("if문으로 score가 60보다 큰지 비교했는가", 40),
                    ("조건이 참이면 합격을 출력했는가", 20),
                )
            )
        )
        assert [c.description for c in criteria] == [
            "if문으로 score가 60보다 큰지 비교했는가",
            "조건이 참이면 합격을 출력했는가",
        ]
        assert [c.id for c in criteria] == [1, 2]
        assert sum(c.weight for c in criteria) == 100

    def test_duplicate_criteria_removed(self) -> None:
        criteria = EvaluationService._validate_criteria(
            json.loads(_criteria_json(("합격을 출력했는가", 50), ("합격을 출력했는가.", 50)))
        )
        assert len(criteria) == 1
        assert criteria[0].weight == 100

    def test_max_five_criteria(self) -> None:
        items = [(f"{i}번째 요구사항을 충족했는가", 10) for i in range(1, 8)]
        criteria = EvaluationService._validate_criteria(json.loads(_criteria_json(*items)))
        assert len(criteria) == 5
        assert sum(c.weight for c in criteria) == 100

    def test_extreme_weights_keep_minimum_one(self) -> None:
        weights = EvaluationService._normalize_weights([1, 1000])
        assert sum(weights) == 100
        assert min(weights) >= 1

    def test_no_valid_criteria_raises(self) -> None:
        with pytest.raises(ValueError):
            EvaluationService._validate_criteria({"criteria": [{"description": "잘 했는가", "weight": 100}]})
        with pytest.raises(ValueError):
            EvaluationService._validate_criteria({"criteria": "none"})

    def test_all_vague_criteria_falls_back_after_retry(self) -> None:
        vague = _criteria_json(("문제를 잘 이해했는가", 50), ("코드를 잘 작성했는가", 50))
        result, fake = _grade(_request("descriptive"), vague, vague)
        assert result.gradingMethod == "rule_fallback"
        assert len(fake.calls) == 2


# ---------------------------------------------------------------------------
# 채점 결과 검증 (서버 재계산)
# ---------------------------------------------------------------------------
class TestGradeValidation:
    def test_server_recomputes_total_and_result(self) -> None:
        result, _ = _grade(
            _request(), CRITERIA_3, _grade_json({1: 30, 2: 0, 3: 30}, total=100, result="CORRECT")
        )
        assert result.score == 60
        assert result.result == "PARTIAL"
        assert result.isCorrect is False

    def test_scores_clamped_and_passed_recomputed(self) -> None:
        payload = json.dumps(
            {
                "criteriaResults": [
                    {"id": 1, "passed": False, "score": 999, "feedback": ""},
                    {"id": 2, "passed": True, "score": -5, "feedback": ""},
                    {"id": 3, "passed": True, "feedback": ""},
                ],
                "feedback": "f",
            }
        )
        result, _ = _grade(_request(), CRITERIA_3, payload)
        by_id = {r.id: r for r in result.criteriaResults}
        assert (by_id[1].score, by_id[1].passed) == (30, True)
        assert (by_id[2].score, by_id[2].passed) == (0, False)
        assert (by_id[3].score, by_id[3].passed) == (30, True)
        assert result.score == 60

    def test_missing_id_scores_zero_and_unknown_id_ignored(self) -> None:
        payload = json.dumps(
            {"criteriaResults": [{"id": 1, "score": 30}, {"id": 2, "score": 40}, {"id": 99, "score": 100}]}
        )
        result, _ = _grade(_request(), CRITERIA_3, payload)
        assert result.score == 70
        missing = next(r for r in result.criteriaResults if r.id == 3)
        assert (missing.score, missing.passed) == (0, False)
        assert "누락" in missing.feedback
        assert {r.id for r in result.criteriaResults} == {1, 2, 3}

    @pytest.mark.parametrize(
        ("score", "expected"),
        [(100, "CORRECT"), (80, "CORRECT"), (79, "PARTIAL"), (40, "PARTIAL"), (39, "INCORRECT"), (0, "INCORRECT")],
    )
    def test_result_thresholds(self, score: int, expected: str) -> None:
        assert EvaluationService._result_from_score(score) == expected

    def test_correct_boundary_end_to_end(self) -> None:
        criteria = _criteria_json(("A 값을 저장했는가", 80), ("B 결과를 출력했는가", 20))
        result, _ = _grade(_request(), criteria, _grade_json({1: 80, 2: 0}))
        assert (result.score, result.result, result.isCorrect) == (80, "CORRECT", True)

    def test_public_response_has_only_legacy_fields(self) -> None:
        fake = FakeLLM(CRITERIA_3, _grade_json({1: 30, 2: 0, 3: 30}))
        response = EvaluationService(llm_service=fake).grade_quiz(_request())
        assert set(response.model_dump()) == _LEGACY_FIELDS
        assert (response.score, response.isCorrect) == (60, False)

    def test_ai_response_fields(self) -> None:
        result, _ = _grade(_request(), CRITERIA_3, _grade_json({1: 30, 2: 40, 3: 30}))
        assert result.gradingMethod == "ai"
        assert result.result == "CORRECT"
        assert result.feedback == "AI 피드백입니다."
        assert result.correctAnswer == "int score = 65; if (score > 60) 합격 출력"
        assert result.explanation == "65 > 60 이므로 합격"
        first = result.criteriaResults[0]
        assert (first.description, first.weight, first.score) == ("정수형 변수 score에 65를 저장했는가", 30, 30)

    def test_empty_ai_feedback_uses_rule_feedback(self) -> None:
        result, _ = _grade(_request(), CRITERIA_3, _grade_json({1: 30, 2: 40, 3: 30}, feedback=""))
        assert result.feedback
        assert result.gradingMethod == "ai"


# ---------------------------------------------------------------------------
# JSON / 재시도 / fallback
# ---------------------------------------------------------------------------
class TestRetryAndFallback:
    def test_code_fenced_json_is_parsed(self) -> None:
        result, _ = _grade(
            _request(), f"```json\n{CRITERIA_3}\n```", "설명입니다\n" + _grade_json({1: 30, 2: 40, 3: 30})
        )
        assert result.gradingMethod == "ai"

    def test_invalid_json_retried_once_then_success(self) -> None:
        result, fake = _grade(_request(), "JSON 아님", CRITERIA_3, _grade_json({1: 30, 2: 40, 3: 30}))
        assert result.gradingMethod == "ai"
        assert len(fake.calls) == 3
        assert "[주의" in fake.calls[1]["prompt"]
        assert "[주의" not in fake.calls[0]["prompt"]

    def test_invalid_json_twice_falls_back_to_rule(self) -> None:
        request = _request()
        result, fake = _grade(request, "JSON 아님", "여전히 아님")
        assert result.gradingMethod == "rule_fallback"
        assert result.result is None
        assert result.criteriaResults == []
        assert len(fake.calls) == 2
        expected_correct, expected_score = EvaluationService(llm_service=FakeLLM())._grade_answer(
            correct_answer=request.question.answer,
            user_answer=request.userAnswer,
            question_type=request.question.type,
            choices=request.question.choices,
        )
        assert (result.isCorrect, result.score) == (expected_correct, expected_score)

    def test_criteria_timeout_not_retried(self) -> None:
        result, fake = _grade(_request(), RuntimeError("LLM 호출 타임아웃 (60s 초과)"))
        assert result.gradingMethod == "rule_fallback"
        assert len(fake.calls) == 1

    def test_grade_network_error_not_retried(self) -> None:
        result, fake = _grade(_request(), CRITERIA_3, RuntimeError("네트워크 오류: connection refused"))
        assert result.gradingMethod == "rule_fallback"
        assert len(fake.calls) == 2

    def test_grade_validation_failure_twice_falls_back(self) -> None:
        bad = json.dumps({"criteriaResults": [{"id": 99, "score": 10}]})
        result, fake = _grade(_request(), CRITERIA_3, bad, bad)
        assert result.gradingMethod == "rule_fallback"
        assert len(fake.calls) == 3

    def test_unexpected_exception_falls_back(self) -> None:
        result, _ = _grade(_request(), KeyError("boom"))
        assert result.gradingMethod == "rule_fallback"

    def test_failure_log_has_reason_without_user_answer(self, caplog: pytest.LogCaptureFixture) -> None:
        long_message = "LLM 호출 타임아웃 (60s 초과) " + "x" * 1000
        with caplog.at_level(logging.INFO, logger="app.services.evaluation_service"):
            _grade(_request(user_answer=f"답안 {_USER_SECRET}"), RuntimeError(long_message))
        warning = next(r.getMessage() for r in caplog.records if "AI failed" in r.getMessage())
        assert "stage=criteria" in warning
        assert "errorType=RuntimeError" in warning
        assert "LLM 호출 타임아웃 (60s 초과)" in warning
        assert "timeout=60" in warning
        assert "x" * 301 not in warning
        assert all(_USER_SECRET not in r.getMessage() for r in caplog.records)

    def test_llm_called_with_settings_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "LLM_TIMEOUT_SECONDS", 75)
        _, fake = _grade(_request(), CRITERIA_3, _grade_json({1: 30, 2: 40, 3: 30}))
        assert [c["timeout_seconds"] for c in fake.calls] == [75, 75]
        assert [c["max_tokens"] for c in fake.calls] == [512, 1024]

    def test_grading_prompt_contains_criteria_and_delimited_answer(self) -> None:
        _, fake = _grade(
            _request(user_answer=_USER_SECRET), CRITERIA_3, _grade_json({1: 30, 2: 40, 3: 30})
        )
        criteria_prompt, grading_prompt = fake.calls[0]["prompt"], fake.calls[1]["prompt"]
        assert _USER_SECRET not in criteria_prompt
        assert "id=2 (weight 40): if문으로 score가 60보다 큰지 비교했는가" in grading_prompt
        assert f"[학생 답안 시작]\n{_USER_SECRET}\n[학생 답안 끝]" in grading_prompt

    def test_validate_grade_result_requires_list(self) -> None:
        criteria = [QuizGradeCriterion(id=1, description="합격을 출력했는가", weight=100)]
        with pytest.raises(ValueError):
            EvaluationService._validate_grade_result({"criteriaResults": {}}, criteria)


# ---------------------------------------------------------------------------
# API 계약
# ---------------------------------------------------------------------------
class TestQuizGradeAiApi:
    def _client_with(self, monkeypatch: pytest.MonkeyPatch, fake: FakeLLM) -> TestClient:
        monkeypatch.setattr(evaluation_service_module, "LLMService", lambda: fake)
        return TestClient(app)

    def _payload(self) -> dict:
        return {
            "category": "Java",
            "difficulty": "초급",
            "question": {
                "id": 1,
                "type": "code_fill",
                "question": "int score = 65; if (score ____ 60) { System.out.println(\"합격\"); }",
                "options": None,
                "answer": ">",
                "explanation": "60보다 큰지 비교",
            },
            "userAnswer": ">=",
        }

    def test_ai_success_contract(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeLLM(
            _criteria_json(("score가 60보다 큰지 비교하는 연산자를 썼는가", 100)),
            _grade_json({1: 50}, feedback=">=는 60도 합격 처리합니다."),
        )
        resp = self._client_with(monkeypatch, fake).post("/ai/quiz/grade", json=self._payload())
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert set(data) == _LEGACY_FIELDS
        assert data["score"] == 50
        assert data["isCorrect"] is False
        assert data["feedback"] == ">=는 60도 합격 처리합니다."
        assert len(fake.calls) == 2

    def test_ai_failure_still_200(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeLLM(RuntimeError("HTTP 500"))
        resp = self._client_with(monkeypatch, fake).post("/ai/quiz/grade", json=self._payload())
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert set(body["data"]) == _LEGACY_FIELDS
        assert body["data"]["correctAnswer"] == ">"
        assert len(fake.calls) == 1
