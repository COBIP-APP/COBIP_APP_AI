"""/ai/quiz/grade CODE_ERROR_FIND false-negative 보정 테스트 (Fake LLM·Fake 임베딩).

1.5B LLM 이 causeCorrect/fixCorrect=false 를 잘못 반환해도, 학생 답안에 모범답안에 명시된
수정 코드가 정확히 있으면 서버가 보수적으로 보정하는지 검증한다.
유사도 값은 실제 BGE-M3 측정값을 사용한다.
"""

from __future__ import annotations

import json
import logging
import math

import pytest

from app.core.config import settings
from app.schemas.evaluation import QuizGradeRequest
from app.services import evaluation_service as evaluation_service_module
from app.services.evaluation_service import (
    EvaluationService,
    _cef_contradiction_cue,
    _cef_fix_code_evidence,
)

QUESTION = '다음 Java 코드에서 오류를 찾고 원인과 수정 방법을 설명하세요.\nint x = "10";'
ANSWER = (
    '문자열 값을 int 변수에 직접 대입할 수 없어 타입 오류가 발생한다. '
    'int x = 10; 또는 Integer.parseInt("10")을 사용해 수정할 수 있다.'
)
# (학생 답안, 실측 BGE-M3 유사도)
CASE_A = (ANSWER, 1.0)
CASE_B = ('int x = "10" 부분이 오류입니다. 문자열을 int에 바로 넣을 수 없으므로 int x = 10;으로 수정해야 합니다.', 0.8660)
CASE_C = ('문자열을 int에 직접 대입할 수 없으므로 Integer.parseInt("10")으로 변환해서 넣어야 합니다.', 0.8611)
CASE_D = ("문자열을 int 변수에 바로 대입할 수 없어서 오류가 납니다.", 0.7852)
CASE_E = ("문자열을 int에 넣을 수 없으므로 int x = 20;으로 수정해야 합니다.", 0.7732)
CASE_F = ("네트워크 연결 문제 때문에 오류가 발생합니다.", 0.5615)
CASE_G = ('문자열은 int 변수에 직접 넣을 수 있으므로 int x = "10" 그대로 두면 됩니다.', 0.7870)

_LOGGER = "app.services.evaluation_service"
_LEGACY_FIELDS = {"isCorrect", "score", "feedback", "correctAnswer", "explanation", "relatedSection"}


def _llm(cause: bool, fix: bool, contradiction: bool = False) -> str:
    return json.dumps({"causeCorrect": cause, "fixCorrect": fix, "hasContradiction": contradiction})


LLM_ALL_FALSE = _llm(False, False)  # 실측: 1.5B 가 B/C 에 반복적으로 반환한 값


class FakeLLM:
    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def generate_text(self, prompt: str, system_prompt: str | None = None, *, timeout_seconds=None,
                      max_tokens=None, temperature=None) -> str:
        self.calls.append({"prompt": prompt})
        if not self.responses:
            raise AssertionError("unexpected LLM call")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return str(response)


class FakeEmbedding:
    similarity: float | None = 0.8

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if FakeEmbedding.similarity is None:
            raise RuntimeError("embedding unavailable")
        s = FakeEmbedding.similarity
        return [[1.0, 0.0], [s, math.sqrt(max(0.0, 1 - s * s))]]


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "QUIZ_GRADE_AI_ENABLED", True)
    monkeypatch.setattr(settings, "OLLAMA_BASE_URL", "http://fake-ollama:11434/v1")
    monkeypatch.setattr(evaluation_service_module, "EmbeddingService", FakeEmbedding)


def _grade(case: tuple[str, float | None], *responses: object, answer: str = ANSWER, question: str = QUESTION):
    user_answer, similarity = case
    FakeEmbedding.similarity = similarity
    fake = FakeLLM(*responses)
    request = QuizGradeRequest(
        question={"id": 1, "type": "code_error_find", "question": question, "answer": answer, "explanation": ""},
        userAnswer=user_answer,
    )
    return EvaluationService(llm_service=fake)._grade_quiz_outcome(request), fake


def _score(outcome) -> tuple[int, str | None]:
    return outcome.response.score, outcome.result


# ---------------------------------------------------------------------------
# A~G (LLM 은 1.5B 실측처럼 전부 false 반환)
# ---------------------------------------------------------------------------
class TestCases:
    def test_a_model_answer_exact(self) -> None:
        outcome, fake = _grade(CASE_A)
        assert outcome.response.score == 100
        assert fake.calls == []

    def test_b_same_fix_code_corrected_from_all_false(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO, logger=_LOGGER):
            outcome, fake = _grade(CASE_B, LLM_ALL_FALSE)
        assert _score(outcome) == (100, "CORRECT")
        assert outcome.response.isCorrect is True
        assert len(fake.calls) == 1
        messages = [r.getMessage() for r in caplog.records]
        assert any("fix corrected by code evidence snippet='intx=10'" in m for m in messages)
        assert any("cause corrected by code evidence" in m for m in messages)

    def test_b_fix_false_negative_corrected_when_llm_cause_true(self) -> None:
        outcome, _ = _grade(CASE_B, _llm(True, False))
        assert _score(outcome) == (100, "CORRECT")

    def test_c_alternative_fix_listed_in_model_answer(self) -> None:
        outcome, _ = _grade(CASE_C, LLM_ALL_FALSE)
        assert _score(outcome) == (100, "CORRECT")

    def test_c_not_accepted_when_model_answer_lacks_that_fix(self) -> None:
        only_literal = "문자열 값을 int 변수에 직접 대입할 수 없어 타입 오류가 발생한다. int x = 10;으로 수정한다."
        outcome, _ = _grade(CASE_C, LLM_ALL_FALSE, answer=only_literal)
        assert outcome.response.score == 0  # 모범답안에 없는 수정법은 서버가 정답 처리하지 않음

    def test_d_cause_only_never_100(self) -> None:
        outcome, _ = _grade(CASE_D, LLM_ALL_FALSE)
        assert outcome.response.score != 100
        outcome, _ = _grade(CASE_D, _llm(True, False))
        assert _score(outcome) == (60, "PARTIAL")
        outcome, _ = _grade(CASE_D, _llm(True, True))  # 기존 정책: 수정 근거 단어 없으면 fix=false
        assert _score(outcome) == (60, "PARTIAL")

    def test_e_wrong_number_not_corrected(self) -> None:
        for llm in (LLM_ALL_FALSE, _llm(True, False)):
            outcome, _ = _grade(CASE_E, llm)
            assert outcome.response.score != 100
            assert outcome.result != "CORRECT"

    def test_f_unrelated_answer(self) -> None:
        outcome, fake = _grade(CASE_F)
        assert _score(outcome) == (0, "INCORRECT")
        assert fake.calls == []  # 유사도 0.68 미만 필터

    def test_g_direct_contradiction(self) -> None:
        for llm in (LLM_ALL_FALSE, _llm(False, False, True)):
            outcome, _ = _grade(CASE_G, llm)
            assert _score(outcome) == (0, "INCORRECT")


# ---------------------------------------------------------------------------
# 과대보정 방어
# ---------------------------------------------------------------------------
class TestNoOverCorrection:
    @pytest.mark.parametrize(
        "case",
        [
            ("네트워크 연결 문제 때문에 오류가 나므로 int x = 10;으로 수정해야 합니다.", 0.7642),
            ('int x = "10"은 메모리가 부족해서 오류가 나므로 int x = 10;으로 수정해야 합니다.', 0.7755),
            ("int x = 10;으로 수정하세요.", 0.7442),
            ("문자열은 int에 넣을 수 있지만 int x = 10;으로 바꿔도 됩니다.", 0.7899),
        ],
    )
    def test_fix_code_with_wrong_or_missing_cause_not_correct(self, case: tuple[str, float]) -> None:
        """수정 코드만 맞고 원인이 틀리거나 없으면 cause 는 보정하지 않는다 (실측 유사도 < 0.85)."""
        outcome, _ = _grade(case, LLM_ALL_FALSE)
        assert outcome.result != "CORRECT"
        assert outcome.response.score != 100

    def test_contradiction_flag_blocks_all_correction(self) -> None:
        outcome, _ = _grade(CASE_B, _llm(False, False, True))
        assert outcome.result != "CORRECT"
        assert outcome.response.score == 0

    def test_contradiction_cue_blocks_correction(self) -> None:
        case = ('int x = 10;으로 바꿔도 되지만 원래 코드도 문제없습니다.', 0.87)
        outcome, _ = _grade(case, LLM_ALL_FALSE)
        assert outcome.result != "CORRECT"

    def test_negated_fix_not_evidence(self) -> None:
        case = ("문자열을 int에 넣을 수 없지만 int x = 10;으로 바꾸면 안 됩니다.", 0.8123)
        outcome, _ = _grade(case, LLM_ALL_FALSE)
        assert outcome.result != "CORRECT"

    def test_embedding_failure_does_not_enable_cause_correction(self) -> None:
        """임베딩 실패 시 similarity=1.0 대체값은 cause 보정 근거로 쓰지 않는다."""
        outcome, _ = _grade((CASE_B[0], None), LLM_ALL_FALSE)
        assert outcome.result != "CORRECT"

    def test_no_server_evidence_keeps_llm_judgement(self) -> None:
        case = ("문자열은 int에 바로 못 넣으니 Integer.valueOf(\"10\")을 쓰면 됩니다.", 0.86)
        outcome, _ = _grade(case, LLM_ALL_FALSE)
        assert outcome.response.score == 0  # 모범답안에 없는 수정법 → 기존 LLM 판정 유지

    def test_public_response_fields_unchanged(self) -> None:
        outcome, _ = _grade(CASE_B, LLM_ALL_FALSE)
        assert set(outcome.response.model_dump()) == _LEGACY_FIELDS


# ---------------------------------------------------------------------------
# 수정 코드 근거 추출
# ---------------------------------------------------------------------------
class TestFixCodeEvidence:
    @pytest.mark.parametrize(
        ("user_answer", "expected"),
        [
            ("int x = 10;으로 수정합니다.", "intx=10"),
            ("int x=10 으로 바꾸세요", "intx=10"),                       # 공백·세미콜론 차이 무시
            ("Integer.parseInt('10')로 변환", 'Integer.parseInt("10")'),  # 따옴표 종류 무시
            ("int x = 20;으로 수정합니다.", None),                         # 숫자 다름
            ("int x = 100;으로 수정합니다.", None),                        # 부분 문자열(10 ⊂ 100) 금지
            ('Integer.parseInt("20")으로 바꿉니다.', None),
            ('Integer.valueOf("10")을 씁니다.', None),                     # 모범답안에 없는 수정법
            ('int x = "10" 그대로 두면 됩니다.', None),                    # 문제의 오류 코드 자체
            ("int x = 10;으로 바꾸면 안 됩니다.", None),                   # 부정
            ("int x = 10;은 사용하지 마세요.", None),
            ("문자열을 int에 넣을 수 없습니다.", None),
        ],
    )
    def test_extraction(self, user_answer: str, expected: str | None) -> None:
        assert _cef_fix_code_evidence(ANSWER, user_answer, QUESTION) == expected

    @pytest.mark.parametrize(
        ("question", "answer", "user_answer", "expected"),
        [
            (
                "int[] nums = {1, 2, 3};\nSystem.out.println(nums[3]);",
                "인덱스는 0부터 시작하므로 nums[3]은 범위를 벗어난다. nums[2]로 수정해야 한다.",
                "nums[3]은 없는 위치라서 nums[2]로 고치면 됩니다.",
                "nums[2]",
            ),
            (
                "int[] nums = {1, 2, 3};\nSystem.out.println(nums[3]);",
                "인덱스는 0부터 시작하므로 nums[3]은 범위를 벗어난다. nums[2]로 수정해야 한다.",
                "nums[4]로 고치면 됩니다.",
                None,
            ),
            (
                "if (score > 60) System.out.println(\"합격\");",
                "60점도 합격이어야 하므로 score >= 60으로 수정해야 한다.",
                "score < 60으로 바꿔야 합니다.",
                None,
            ),
        ],
    )
    def test_generic_problems(self, question: str, answer: str, user_answer: str, expected: str | None) -> None:
        assert _cef_fix_code_evidence(answer, user_answer, question) == expected

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ('int x = "10" 그대로 두면 됩니다.', True),
            ("원래 코드도 문제없습니다.", True),
            ("수정할 필요가 없습니다.", True),
            ("정상적으로 동작합니다.", True),
            (CASE_B[0], False),
            (CASE_C[0], False),
        ],
    )
    def test_contradiction_cue(self, text: str, expected: bool) -> None:
        assert _cef_contradiction_cue(text) is expected
