"""/ai/java/grade 채점 테스트 (Fake LLM, 실제 Ollama 호출 없음).

question → criteria 자동 생성 → 기존 Java criteria 채점 흐름을 검증한다.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.prompts.code_analyze_prompts import (
    JAVA_CODE_GRADING_SYSTEM_PROMPT,
    JAVA_CRITERIA_SYSTEM_PROMPT,
    build_java_code_grading_prompt,
    build_java_criteria_prompt,
)
from app.schemas.evaluation import JavaCodeGradingRequest
from app.services import evaluation_service as evaluation_service_module
from app.services.evaluation_service import EvaluationService

QUESTION = (
    "정수형 변수 score에 65를 저장하고, if문으로 60보다 큰지 비교한 뒤, "
    "조건이 참이면 '합격'을 출력하세요."
)
CODE = (
    "public class Main { public static void main(String[] args) { "
    'int score = 65; if (score > 60) { System.out.println("합격"); } } }'
)
CODE_NO_IF = (
    "public class Main { public static void main(String[] args) { "
    "int score = 65; } }"
)
# if 와 출력이 주석 안에만 있고 실제로는 "불합격"만 출력하는 잘못된 코드
CODE_WRONG = (
    "public class Main {\n    public static void main(String[] args) {\n"
    "        int score = 70;\n"
    '        // if (score > 60) System.out.println("합격");\n'
    '        System.out.println("불합격");\n    }\n}'
)

_RESPONSE_FIELDS = {"is_correct", "score", "feedback", "formatted_code"}
_LOGGER = "app.services.evaluation_service"


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
        temperature: float | None = None,
    ) -> str:
        self.calls.append(
            {
                "prompt": prompt,
                "system_prompt": system_prompt,
                "timeout_seconds": timeout_seconds,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        if not self.responses:
            raise AssertionError("unexpected LLM call")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return str(response)


def _criteria_json(*items: object) -> str:
    return json.dumps({"criteria": list(items)}, ensure_ascii=False)


CRITERIA_3 = (
    "정수형 변수 score에 65를 저장했는가",
    "if문으로 score가 60보다 큰지 비교했는가",
    "조건이 참이면 합격을 출력했는가",
)
CRITERIA_JSON = _criteria_json(*CRITERIA_3)


def _grade_json(passed: list[bool], *, score: int | None = None, is_correct: bool | None = None,
                feedback: str = "LLM 피드백입니다.", formatted_code: str = "formatted") -> str:
    return json.dumps(
        {
            "criteria_results": [
                {"index": i, "passed": p, "reason": f"기준 {i} 근거"}
                for i, p in enumerate(passed, start=1)
            ],
            "is_correct": all(passed) if is_correct is None else is_correct,
            "score": round(100 * sum(passed) / len(passed)) if score is None else score,
            "feedback": feedback,
            "formatted_code": formatted_code,
        },
        ensure_ascii=False,
    )


GRADE_ALL_PASS = _grade_json([True, True, True])


def _grade(request: JavaCodeGradingRequest, *responses: object):
    fake = FakeLLM(*responses)
    return EvaluationService(llm_service=fake).grade_java_code(request), fake


def _criteria_section(grading_prompt: str) -> str:
    return grading_prompt.split("평가 기준:\n", 1)[1].split("\n\n[제출 코드 시작]", 1)[0]


def _has_loop_criterion(criteria: list[str] | str) -> bool:
    text = " ".join(criteria) if isinstance(criteria, list) else criteria
    return any(word in text.lower() for word in ("반복", "for", "while"))


def _logged(caplog: pytest.LogCaptureFixture, text: str) -> bool:
    return any(text in record.getMessage() for record in caplog.records)


# ---------------------------------------------------------------------------
# 요청 스키마 / 외부 계약
# ---------------------------------------------------------------------------
class TestRequestSchema:
    def test_requires_question_or_criteria(self) -> None:
        with pytest.raises(ValidationError):
            JavaCodeGradingRequest(code=CODE)
        with pytest.raises(ValidationError):
            JavaCodeGradingRequest(code=CODE, question="   ", criteria=[" "])

    def test_criteria_none_and_empty_accepted_with_question(self) -> None:
        assert JavaCodeGradingRequest(code=CODE, question=QUESTION, criteria=None).criteria == []
        assert JavaCodeGradingRequest(code=CODE, question=QUESTION, criteria=[]).criteria == []

    def test_criteria_whitespace_items_filtered(self) -> None:
        req = JavaCodeGradingRequest(code=CODE, criteria=["  if문 사용  ", " ", "출력 확인"])
        assert req.criteria == ["if문 사용", "출력 확인"]

    def test_legacy_criteria_only_request_valid(self) -> None:
        req = JavaCodeGradingRequest(code=CODE, criteria=["조건문 사용 여부"])
        assert req.question is None
        assert req.criteria == ["조건문 사용 여부"]


# ---------------------------------------------------------------------------
# criteria 결정 경로 / LLM 호출 횟수
# ---------------------------------------------------------------------------
class TestCriteriaSource:
    def test_question_only_generates_criteria_then_grades(self, caplog: pytest.LogCaptureFixture) -> None:
        """테스트 1: criteria 미전달 + question → 생성 1회 + 채점 1회."""
        request = JavaCodeGradingRequest(question=QUESTION, code=CODE)
        with caplog.at_level(logging.INFO, logger=_LOGGER):
            result, fake = _grade(request, CRITERIA_JSON, GRADE_ALL_PASS)

        assert len(fake.calls) == 2
        criteria_call, grading_call = fake.calls
        assert JAVA_CRITERIA_SYSTEM_PROMPT in criteria_call["prompt"]
        assert QUESTION in criteria_call["prompt"]
        assert CODE not in criteria_call["prompt"]
        assert JAVA_CODE_GRADING_SYSTEM_PROMPT in grading_call["prompt"]
        section = _criteria_section(grading_call["prompt"])
        assert section.splitlines() == [f"{i}. {c}" for i, c in enumerate(CRITERIA_3, start=1)]
        assert all(call["temperature"] == 0.0 for call in fake.calls)

        assert (result.is_correct, result.score) == (True, 100)
        assert "LLM 피드백입니다." in result.feedback
        assert set(result.model_dump()) == _RESPONSE_FIELDS
        assert _logged(caplog, "java_code_grade criteria_source=llm count=3")

    def test_request_criteria_skips_generation(self, caplog: pytest.LogCaptureFixture) -> None:
        """테스트 2: criteria 직접 전달 → 생성 호출 0회, 채점 1회."""
        request = JavaCodeGradingRequest(code=CODE, criteria=["조건문 사용 여부", "합격 출력 여부"])
        with caplog.at_level(logging.INFO, logger=_LOGGER):
            result, fake = _grade(request, _grade_json([True, True]))

        assert len(fake.calls) == 1
        assert JAVA_CODE_GRADING_SYSTEM_PROMPT in fake.calls[0]["prompt"]
        assert JAVA_CRITERIA_SYSTEM_PROMPT not in fake.calls[0]["prompt"]
        assert "1. 조건문 사용 여부" in _criteria_section(fake.calls[0]["prompt"])
        assert result.score == 100
        assert _logged(caplog, "java_code_grade criteria_source=request count=2")

    def test_request_criteria_with_question_still_skips_generation(self) -> None:
        request = JavaCodeGradingRequest(code=CODE, question=QUESTION, criteria=["합격을 출력했는가"])
        _, fake = _grade(request, _grade_json([True]))
        assert len(fake.calls) == 1


# ---------------------------------------------------------------------------
# LLM criteria 검증
# ---------------------------------------------------------------------------
class TestCriteriaValidation:
    def test_if_only_question_drops_llm_loop_criteria(self) -> None:
        """테스트 3: question 이 if만 요구 → LLM 이 섞은 반복문 기준은 제거된다."""
        data = {"criteria": [*CRITERIA_3, "for문으로 반복했는가", "while문을 사용했는가"]}
        criteria = EvaluationService._validate_java_criteria(data, QUESTION)
        assert criteria == list(CRITERIA_3)
        assert not _has_loop_criterion(criteria)

    def test_unrequested_constructs_and_foreign_values_dropped(self) -> None:
        question = "정수형 변수 a에 3을 저장하고 a를 출력하세요."
        data = {
            "criteria": [
                "정수형 변수 a에 3을 저장했는가",
                "a의 값을 출력했는가",
                "if문으로 a가 0보다 큰지 비교했는가",       # 문제에 조건 없음
                "else로 거짓인 경우를 처리했는가",           # 문제에 else 없음
                "변수 score에 65를 저장했는가",              # 예시 복붙(문제에 없는 변수)
                '"합격"을 출력했는가',                       # 문제에 없는 출력 문자열
                "Scanner로 값을 입력받았는가",               # 문제에 입력 없음
            ]
        }
        assert EvaluationService._validate_java_criteria(data, question) == [
            "정수형 변수 a에 3을 저장했는가",
            "a의 값을 출력했는가",
        ]

    def test_empty_short_vague_criteria_removed(self) -> None:
        data = {
            "criteria": [
                "",
                "   ",
                "출력",
                "코드를 올바르게 구현했는가",
                "코드가 적절한가",
                "잘 작성했는가",
                None,
                123,
                *CRITERIA_3,
            ]
        }
        assert EvaluationService._validate_java_criteria(data, QUESTION) == list(CRITERIA_3)

    def test_duplicate_and_near_duplicate_removed(self) -> None:
        data = {
            "criteria": [
                "if문으로 score가 60보다 큰지 비교했는가",
                "if문으로 score가 60보다 큰지 비교했는가.",
                "1. if문으로 score가 60보다 큰지를 비교했는가",
                "조건이 참이면 합격을 출력했는가",
            ]
        }
        assert EvaluationService._validate_java_criteria(data, QUESTION) == [
            "if문으로 score가 60보다 큰지 비교했는가",
            "조건이 참이면 합격을 출력했는가",
        ]

    def test_different_values_are_not_duplicates(self) -> None:
        question = "정수형 변수 a에 3을, b에 5를 저장하고 a와 b를 더한 값을 출력하세요."
        data = {"criteria": ["정수형 변수 a에 3을 저장했는가", "정수형 변수 b에 5를 저장했는가"]}
        assert len(EvaluationService._validate_java_criteria(data, question)) == 2

    def test_more_than_five_limited(self) -> None:
        question = "정수형 변수 a에 1, b에 2, c에 3, d에 4, e에 5, f에 6, g에 7을 저장하세요."
        items = [f"정수형 변수 {n}에 {v}을 저장했는가" for n, v in zip("abcdefg", range(1, 8), strict=True)]
        criteria = EvaluationService._validate_java_criteria({"criteria": items}, question)
        assert criteria == items[:5]

    def test_dict_items_normalized(self) -> None:
        data = {"criteria": [{"description": CRITERIA_3[0]}, {"criterion": CRITERIA_3[2]}]}
        assert EvaluationService._validate_java_criteria(data, QUESTION) == [CRITERIA_3[0], CRITERIA_3[2]]

    @pytest.mark.parametrize(
        "data",
        [{}, {"items": list(CRITERIA_3)}, {"criteria": "if문 사용"}, {"criteria": []},
         {"criteria": ["", "잘 작성했는가"]}],
    )
    def test_invalid_payload_raises(self, data: dict) -> None:
        with pytest.raises(ValueError):
            EvaluationService._validate_java_criteria(data, QUESTION)

    def test_criteria_prompt_rules(self) -> None:
        prompt = build_java_criteria_prompt(question=QUESTION)
        assert "문제에 없는" in prompt
        assert "2~5개" in prompt
        assert "추상적인 기준은 금지" in prompt
        assert f"[문제 시작]\n{QUESTION}\n[문제 끝]" in prompt

    def test_grading_prompt_does_not_force_loop_or_indentation(self) -> None:
        prompt = build_java_code_grading_prompt(code=CODE, criteria=list(CRITERIA_3))
        assert "반복문 사용 (for, while)" not in prompt
        assert "기준에 없는 요소" in prompt
        assert "criteria_results" in prompt


# ---------------------------------------------------------------------------
# criteria 생성 실패 → question 기반 fallback
# ---------------------------------------------------------------------------
class TestCriteriaFallback:
    @pytest.mark.parametrize(
        "bad_response",
        [
            RuntimeError("LLM 호출 타임아웃 (60s 초과)"),
            "JSON 아님",
            '{"criteria": ["깨진 JSON"',
            '{"items": ["if문을 썼는가"]}',
            '{"criteria": []}',
            '{"criteria": "if문 사용"}',
            '["정수형 변수 score에 65를 저장했는가"]',
        ],
    )
    def test_generation_failure_uses_fallback(
        self, bad_response: object, caplog: pytest.LogCaptureFixture
    ) -> None:
        """테스트 4: 실패/malformed/key 없음/빈 배열 → 재시도 없이 fallback, 총 2회 호출."""
        request = JavaCodeGradingRequest(question=QUESTION, code=CODE)
        with caplog.at_level(logging.INFO, logger=_LOGGER):
            result, fake = _grade(request, bad_response, GRADE_ALL_PASS)

        assert len(fake.calls) == 2
        assert _logged(caplog, "java_code_grade criteria_source=fallback count=3")
        section = _criteria_section(fake.calls[1]["prompt"])
        assert "score" in section and "65" in section
        assert "if문" in section
        assert '"합격"' in section
        assert not _has_loop_criterion(section)
        assert result.score == 100

    def test_code_fenced_json_is_parsed(self) -> None:
        request = JavaCodeGradingRequest(question=QUESTION, code=CODE)
        raw = f"기준입니다.\n```json\n{CRITERIA_JSON}\n```\n끝."
        _, fake = _grade(request, raw, GRADE_ALL_PASS)
        assert "1. 정수형 변수 score에 65를 저장했는가" in _criteria_section(fake.calls[1]["prompt"])

    def test_all_llm_criteria_unrequested_uses_fallback(self, caplog: pytest.LogCaptureFixture) -> None:
        request = JavaCodeGradingRequest(question=QUESTION, code=CODE)
        loops = _criteria_json("for문으로 반복했는가", "while문을 사용했는가")
        with caplog.at_level(logging.INFO, logger=_LOGGER):
            _, fake = _grade(request, loops, GRADE_ALL_PASS)
        assert _logged(caplog, "criteria_source=fallback")
        assert not _has_loop_criterion(_criteria_section(fake.calls[1]["prompt"]))


class TestFallbackExtraction:
    def test_if_question_has_no_loop_criteria(self) -> None:
        criteria = EvaluationService._fallback_java_criteria(QUESTION)
        assert criteria == [
            "정수형 변수 score에 65을(를) 저장했는가",
            "if문으로 score > 60 조건을 비교했는가",
            '"합격"을(를) 출력했는가',
        ]
        assert not _has_loop_criterion(criteria)

    def test_no_if_when_question_has_no_condition(self) -> None:
        criteria = EvaluationService._fallback_java_criteria("두 정수 3과 5를 더한 결과를 출력하세요.")
        assert criteria == ["덧셈(+) 연산으로 값을 계산했는가", "System.out.println으로 결과를 출력했는가"]
        assert not any("if" in c for c in criteria)
        assert not _has_loop_criterion(criteria)

    def test_no_print_when_question_has_no_output(self) -> None:
        criteria = EvaluationService._fallback_java_criteria("정수형 변수 count에 10을 저장하세요.")
        assert criteria == ["정수형 변수 count에 10을(를) 저장했는가"]

    def test_loop_question(self) -> None:
        criteria = EvaluationService._fallback_java_criteria("for문으로 1부터 5까지 출력하세요.")
        assert criteria == ["for문으로 1부터 5까지 반복했는가", "System.out.println으로 결과를 출력했는가"]

    def test_even_question(self) -> None:
        criteria = EvaluationService._fallback_java_criteria("정수 n이 짝수인지 검사해 결과를 출력하세요.")
        assert criteria[0] == "% 연산자로 n이(가) 짝수인지 검사했는가"
        assert not any("if" in c for c in criteria)

    def test_output_strings_and_else(self) -> None:
        criteria = EvaluationService._fallback_java_criteria(
            '정수형 변수 score에 65를 저장하고 60 이상이면 "합격", 그렇지 않으면 "불합격"을 출력하세요.'
        )
        assert '"합격"을(를) 출력했는가' in criteria
        assert '"불합격"을(를) 출력했는가' in criteria
        assert "else로 조건이 거짓인 경우를 처리했는가" in criteria
        assert "score >= 60 조건을 비교했는가" in criteria
        assert len(criteria) <= 5

    def test_unextractable_question_uses_question_clauses_not_hardcoded(self) -> None:
        criteria = EvaluationService._fallback_java_criteria("Main 클래스를 만들어 보세요.")
        assert criteria == ["문제 요구사항(Main 클래스를 만들어 보세요)을 구현했는가"]
        assert not any(word in " ".join(criteria) for word in ("int", "if문", "반복", "들여쓰기"))


# ---------------------------------------------------------------------------
# 채점 안정성 (보수적 점수)
# ---------------------------------------------------------------------------
class TestGradingSafety:
    def test_wrong_code_not_full_score_even_if_llm_says_pass(self) -> None:
        """LLM 이 전부 통과라고 해도 코드 근거가 없으면 만점이 되지 않는다."""
        request = JavaCodeGradingRequest(question=QUESTION, code=CODE_WRONG)
        result, _ = _grade(request, CRITERIA_JSON, GRADE_ALL_PASS)
        assert result.is_correct is False
        assert result.score == 0
        assert "[부족한 요구사항]" in result.feedback
        assert "근거를 찾지 못했습니다" in result.feedback

    def test_server_recomputes_score_from_criteria_results(self) -> None:
        request = JavaCodeGradingRequest(question=QUESTION, code=CODE)
        raw = _grade_json([True, False, True], score=100, is_correct=True)
        result, _ = _grade(request, CRITERIA_JSON, raw)
        assert (result.score, result.is_correct) == (67, False)
        assert "평가 기준 3개 중 2개를 충족했습니다." in result.feedback
        assert "- if문으로 score가 60보다 큰지 비교했는가 (기준 2 근거)" in result.feedback

    def test_missing_criteria_results_caps_llm_score(self) -> None:
        request = JavaCodeGradingRequest(question=QUESTION, code=CODE_NO_IF)
        raw = json.dumps({"is_correct": True, "score": 100, "feedback": "f", "formatted_code": "x"})
        result, _ = _grade(request, CRITERIA_JSON, raw)
        assert result.score == 33
        assert result.is_correct is False

    def test_llm_is_correct_false_never_returns_100(self) -> None:
        request = JavaCodeGradingRequest(question=QUESTION, code=CODE)
        raw = json.dumps({"is_correct": False, "score": 100, "feedback": "f", "formatted_code": "x"})
        result, _ = _grade(request, CRITERIA_JSON, raw)
        assert result.score < 100
        assert result.is_correct is False

    @pytest.mark.parametrize("llm_score", [250, -30, "abc"])
    def test_score_clamped(self, llm_score: object) -> None:
        request = JavaCodeGradingRequest(code=CODE, criteria=list(CRITERIA_3))
        raw = json.dumps({"is_correct": True, "score": llm_score, "feedback": "f", "formatted_code": "x"})
        result, _ = _grade(request, raw)
        assert 0 <= result.score <= 100
        assert result.is_correct == (result.score == 100)

    def test_grading_failure_rule_scores_by_criteria(self) -> None:
        """채점 LLM 실패 → criteria 기반 규칙 채점 (if/출력 없는 코드는 부분 점수)."""
        request = JavaCodeGradingRequest(question=QUESTION, code=CODE_NO_IF)
        result, _ = _grade(request, CRITERIA_JSON, RuntimeError("grading failed"))
        assert (result.is_correct, result.score) == (False, 33)
        assert "규칙 기반" in result.feedback
        assert result.formatted_code == CODE_NO_IF

    def test_grading_failure_correct_code_full_score(self) -> None:
        request = JavaCodeGradingRequest(question=QUESTION, code=CODE)
        result, _ = _grade(request, CRITERIA_JSON, RuntimeError("grading failed"))
        assert (result.is_correct, result.score) == (True, 100)

    def test_grading_malformed_json_falls_back_without_retry(self) -> None:
        request = JavaCodeGradingRequest(code=CODE, criteria=list(CRITERIA_3))
        result, fake = _grade(request, "채점 결과: 좋아요")
        assert len(fake.calls) == 1
        assert "규칙 기반" in result.feedback

    def test_unverifiable_criterion_not_auto_passed_in_rule_fallback(self) -> None:
        request = JavaCodeGradingRequest(code=CODE, criteria=["문제 의도에 맞게 구현했는가"])
        result, _ = _grade(request, RuntimeError("down"))
        assert (result.score, result.is_correct) == (0, False)
        assert "규칙으로 확인할 수 없어" in result.feedback

    def test_commented_out_code_not_counted(self) -> None:
        request = JavaCodeGradingRequest(code=CODE_WRONG, criteria=["if문으로 조건을 검사했는가"])
        result, _ = _grade(request, RuntimeError("down"))
        assert result.score == 0


# ---------------------------------------------------------------------------
# API 계약
# ---------------------------------------------------------------------------
class TestJavaGradeApi:
    def _client(self, monkeypatch: pytest.MonkeyPatch, fake: FakeLLM) -> TestClient:
        monkeypatch.setattr(evaluation_service_module, "LLMService", lambda: fake)
        return TestClient(app)

    def test_question_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeLLM(CRITERIA_JSON, GRADE_ALL_PASS)
        resp = self._client(monkeypatch, fake).post("/ai/java/grade", json={"question": QUESTION, "code": CODE})
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert set(body["data"]) == _RESPONSE_FIELDS
        assert (body["data"]["is_correct"], body["data"]["score"]) == (True, 100)
        assert len(fake.calls) == 2

    def test_legacy_criteria_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeLLM(_grade_json([True]))
        resp = self._client(monkeypatch, fake).post(
            "/ai/java/grade", json={"code": CODE, "criteria": ["조건문 사용 여부"]}
        )
        assert resp.status_code == 200
        assert set(resp.json()["data"]) == _RESPONSE_FIELDS
        assert len(fake.calls) == 1

    def test_llm_down_still_200(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeLLM(RuntimeError("down"), RuntimeError("down"))
        resp = self._client(monkeypatch, fake).post("/ai/java/grade", json={"question": QUESTION, "code": CODE})
        assert resp.status_code == 200
        assert set(resp.json()["data"]) == _RESPONSE_FIELDS

    def test_422_without_question_and_criteria(self) -> None:
        resp = TestClient(app).post("/ai/java/grade", json={"code": CODE})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 범위 보호: quiz grading 관련 파일에 diff 가 없어야 한다
# ---------------------------------------------------------------------------
def test_quiz_grade_files_untouched() -> None:
    repo = Path(__file__).resolve().parents[1]
    protected = [
        "app/prompts/quiz_grade_prompts.py",
        "app/api/routes/evaluation.py",
        "app/services/code_analyze_service.py",
        "app/services/mission_feedback_service.py",
        "app/services/evaluation_payload_normalizer.py",
        "tests/test_quiz_grade.py",
        "tests/test_quiz_grade_ai.py",
    ]
    try:
        diff = subprocess.run(
            ["git", "diff", "--name-only", "HEAD", "--", *protected],
            cwd=repo, capture_output=True, text=True, check=True, timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git 을 사용할 수 없는 환경")
    assert diff == ""
