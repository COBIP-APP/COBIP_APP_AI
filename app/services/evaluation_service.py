"""기능템플릿 기본 문제 채점 + 코드 조각 분석 service."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, TypeVar

from app.core.config import settings
from app.models.enums import QuestionType
from app.prompts.quiz_grade_prompts import (
    QUIZ_ANSWER_GRADING_SYSTEM_PROMPT,
    QUIZ_CRITERIA_SYSTEM_PROMPT,
    QUIZ_SEMANTIC_EQUIVALENCE_SYSTEM_PROMPT,
    build_quiz_answer_grading_prompt,
    build_quiz_criteria_prompt,
    build_quiz_semantic_equivalence_prompt,
)
from app.schemas.evaluation import (
    CodeAnalyzeRequest,
    CodeAnalyzeResponse,
    JavaCodeGradingRequest,
    JavaCodeGradingResponse,
    QuizGradeCriterion,
    QuizGradeCriterionResult,
    QuizGradeRequest,
    QuizGradeResponse,
)
from app.services.code_analyze_service import CodeAnalyzeService
from app.services.embedding_service import EmbeddingService
from app.services.evaluation_payload_normalizer import (
    extract_answer_keywords,
    normalize_answer_text,
)
from app.services.llm_service import LLMService

__all__ = [
    "EvaluationService",
    "CodeAnalyzeRequest",
    "CodeAnalyzeResponse",
    "JavaCodeGradingRequest",
    "JavaCodeGradingResponse",
]

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

GradingMethod = Literal["rule", "ai", "rule_fallback"]
GradeResult = Literal["CORRECT", "PARTIAL", "INCORRECT"]

# 정규화 완전 일치가 아니면 AI 채점을 시도하는 유형.
# MULTIPLE_CHOICE / OX 는 항상 rule 채점.
_AI_GRADED_TYPES: frozenset[QuestionType] = frozenset(
    {
        QuestionType.SHORT_ANSWER,
        QuestionType.FILL_BLANK,
        QuestionType.DESCRIPTIVE,
        QuestionType.OUTPUT_PREDICTION,
        QuestionType.CODE_ERROR_FIND,
        QuestionType.CODE_FILL,
    }
)

# 위 유형 중 criteria 채점 대신 의미 동등성 판정(1회 호출, 100/0점)을 쓰는 유형.
_SEMANTIC_GRADED_TYPES: frozenset[QuestionType] = frozenset(
    {
        QuestionType.SHORT_ANSWER,
        QuestionType.FILL_BLANK,
    }
)

_MAX_CRITERIA = 5
_CORRECT_MIN_SCORE = 80
_PARTIAL_MIN_SCORE = 40
_LLM_JSON_MAX_ATTEMPTS = 2
_CRITERIA_MAX_TOKENS = 512
_GRADING_MAX_TOKENS = 1024
_SEMANTIC_MAX_TOKENS = 64
_SEMANTIC_CORRECT_FEEDBACK = "정답입니다. 모범 답안과 의미상 동일한 표현입니다."
_SEMANTIC_WRONG_FEEDBACK = "오답입니다. 모범 답안은 '{correct_answer}'입니다."
_LOG_ERROR_MAX_CHARS = 300
_CRITERION_FEEDBACK_MAX_CHARS = 500
_MIN_CRITERION_CHARS = 6
_LLM_JSON_RETRY_SUFFIX = (
    "\n\n[주의: 이전 응답이 규약에 맞지 않았습니다({error}). "
    "설명 없이 규약의 JSON 객체 하나만 출력하라.]"
)
_VAGUE_CRITERION_RE = re.compile(r"잘\s*(이해|작성|설명|풀|답|했|하였)|적절(히|하게)\s*(작성|답|설명)했")



@dataclass
class _QuizGradeOutcome:
    """grade_quiz 내부 결과. 외부 응답은 response(기존 6개 필드)만 반환한다."""

    response: QuizGradeResponse
    grading_method: GradingMethod
    result: GradeResult | None = None
    criteria_results: list[QuizGradeCriterionResult] = field(default_factory=list)


_OX_TRUE = frozenset({"o", "○", "true", "t", "참", "맞다", "맞음", "예", "yes", "y"})
_OX_FALSE = frozenset({"x", "×", "false", "f", "거짓", "틀리다", "틀림", "아니오", "no", "n"})


# 개념 키워드별 한 문장 코칭 설명. normalize_answer_text 가 한글 동의어를
# 영문 키워드로 매핑하므로(해시→hash, 암호화→encrypt 등) 키는 영문 기준이다.
_KEYWORD_COACHING: dict[str, str] = {
    "bcrypt": "BCrypt는 단방향 해시 함수로, 비밀번호를 복호화 불가능한 형태로 저장할 때 사용합니다.",
    "hash": "해시는 비밀번호를 원문으로 되돌릴 수 없게 변환해 저장하는 방식입니다.",
    "encrypt": "암호화는 민감한 값이 그대로 노출되지 않도록 변환해 다루는 처리입니다.",
    "salt": "솔트는 같은 비밀번호라도 해시 값이 달라지게 해 레인보우 테이블 공격을 막습니다.",
    "validation": "이메일 형식·비밀번호 길이처럼 요청 값 자체의 검증은 DTO에서 처리해야 합니다.",
    "service": "이메일 중복 확인처럼 DB 조회가 필요한 비즈니스 검증은 Service 계층에서 처리해야 합니다.",
    "controller": "Controller는 HTTP 요청을 받아 DTO로 변환하고 응답을 반환하는 계층입니다.",
    "repository": "Repository는 DB 조회·저장 같은 영속성 처리를 담당하는 계층입니다.",
    "dto": "DTO는 요청/응답 데이터를 담아 계층 간에 안전하게 전달하는 객체입니다.",
    "token": "토큰은 인증된 사용자를 식별하기 위해 서버가 발급하는 인증 수단입니다.",
    "jwt": "JWT는 서버가 상태를 저장하지 않고도 사용자를 인증할 수 있는 토큰 형식입니다.",
    "bearer": "Bearer는 Authorization 헤더에 토큰을 담아 전달하는 인증 방식입니다.",
    "login": "로그인은 자격 증명을 검증한 뒤 인증 토큰(또는 세션)을 발급하는 흐름입니다.",
    "signup": "회원가입은 입력값을 검증하고 사용자 정보를 저장하는 흐름입니다.",
    "auth": "인증은 요청한 사용자가 본인이 맞는지 확인하는 과정입니다.",
    "session": "세션은 서버가 로그인 상태를 저장해 사용자를 식별하는 방식입니다.",
    "duplicate": "중복 검사는 이미 가입된 값인지 DB에서 확인해 중복 등록을 막는 처리입니다.",
}


class EvaluationService:
    """기능템플릿 기본 문제 채점 + 코드 분석 service."""

    def __init__(self, llm_service: LLMService | None = None) -> None:
        self._llm = llm_service or LLMService()

    def grade_quiz(self, request: QuizGradeRequest) -> QuizGradeResponse:
        """유형별 rule / AI criteria 혼합 채점. 응답은 기존 6개 필드만 반환한다.

        - MULTIPLE_CHOICE / OX, 빈 답안, 정규화 완전 일치: rule
        - SHORT_ANSWER / FILL_BLANK: AI 의미 동등성 판정 1회 (100 / 0점)
        - 그 외 _AI_GRADED_TYPES: AI 2단계(criteria 생성 → 기준별 평가)
        - AI 호출·파싱·검증 실패: 기존 _grade_answer 로 rule_fallback
        """
        return self._grade_quiz_outcome(request).response

    def _grade_quiz_outcome(self, request: QuizGradeRequest) -> _QuizGradeOutcome:
        correct_answer = (request.question.answer or "").strip()
        user_answer = (request.userAnswer or "").strip()
        q_type = request.question.type

        use_ai = (
            bool(correct_answer)
            and bool(user_answer)
            and q_type in _AI_GRADED_TYPES
            and not self._is_exact_match(correct_answer, user_answer, q_type)
            and self._ai_grading_enabled()
        )
        if not use_ai:
            return self._rule_quiz_response(
                request,
                correct_answer=correct_answer,
                user_answer=user_answer,
                grading_method="rule",
            )

        if q_type == QuestionType.CODE_ERROR_FIND:
            grade_with_ai = self._grade_code_error_find
        elif q_type == QuestionType.CODE_FILL:
            grade_with_ai = self._grade_code_fill
        elif q_type == QuestionType.OUTPUT_PREDICTION:
            grade_with_ai = self._grade_output_prediction
        elif q_type == QuestionType.DESCRIPTIVE:
            grade_with_ai = self._grade_descriptive_semantic
        elif q_type in _SEMANTIC_GRADED_TYPES:
            grade_with_ai = self._grade_quiz_with_semantic
        else:
            grade_with_ai = self._grade_quiz_with_ai
        try:
            ai_outcome = grade_with_ai(
                request,
                correct_answer=correct_answer,
                user_answer=user_answer,
            )
        except Exception as exc:  # noqa: BLE001 - 어떤 실패든 rule 채점으로 응답한다.
            self._log_ai_failure(stage="unexpected", q_type=q_type, exc=exc)
            ai_outcome = None

        if ai_outcome is not None:
            return ai_outcome
        return self._rule_quiz_response(
            request,
            correct_answer=correct_answer,
            user_answer=user_answer,
            grading_method="rule_fallback",
        )

    def _rule_quiz_response(
        self,
        request: QuizGradeRequest,
        *,
        correct_answer: str,
        user_answer: str,
        grading_method: GradingMethod,
    ) -> _QuizGradeOutcome:
        is_correct, score = self._grade_answer(
            correct_answer=correct_answer,
            user_answer=user_answer,
            question_type=request.question.type,
            choices=request.question.choices,
        )
        feedback = self._build_quiz_feedback(
            is_correct=is_correct,
            score=score,
            correct_answer=correct_answer,
            user_answer=user_answer,
            related_section=request.question.relatedSection,
            question_text=(request.question.question or "").strip(),
        )
        logger.info(
            "quiz_grade type=%s method=%s score=%d",
            request.question.type.value,
            grading_method,
            score,
        )
        response = QuizGradeResponse(
            isCorrect=is_correct,
            score=score,
            feedback=feedback,
            correctAnswer=correct_answer,
            explanation=self._quiz_explanation(request, correct_answer),
            relatedSection=request.question.relatedSection,
        )
        return _QuizGradeOutcome(response=response, grading_method=grading_method)

    def _quiz_explanation(self, request: QuizGradeRequest, correct_answer: str) -> str:
        return request.question.explanation or self._build_explanation_fallback(
            correct_answer=correct_answer,
            related_section=request.question.relatedSection,
            question_text=(request.question.question or "").strip(),
        )

    @staticmethod
    def _ai_grading_enabled() -> bool:
        return bool(settings.QUIZ_GRADE_AI_ENABLED and settings.OLLAMA_BASE_URL)

    @staticmethod
    def _compact_answer(text: str) -> str:
        return re.sub(r"\s+", "", normalize_answer_text(text))

    @classmethod
    def _is_exact_match(
        cls,
        correct_answer: str,
        user_answer: str,
        question_type: QuestionType,
    ) -> bool:
        """정규화(대소문자·공백·문장부호·동의어) 후 완전 일치 여부. 빈칸은 칸별 비교."""
        if question_type == QuestionType.FILL_BLANK:
            correct_parts = cls._split_blank_answers(correct_answer)
            if len(correct_parts) > 1:
                user_parts = cls._split_blank_answers(user_answer)
                return len(user_parts) == len(correct_parts) and all(
                    cls._compact_answer(c) == cls._compact_answer(u)
                    for c, u in zip(correct_parts, user_parts, strict=True)
                )
        compact_correct = cls._compact_answer(correct_answer)
        return bool(compact_correct) and compact_correct == cls._compact_answer(user_answer)

    @staticmethod
    def _split_blank_answers(text: str) -> list[str]:
        return [part for part in re.split(r"\s*[,，]\s*", text.strip()) if part]

    # ------------------------------------------------------------------
    # AI 의미 동등성 판정 (SHORT_ANSWER / FILL_BLANK)
    # ------------------------------------------------------------------
    def _grade_quiz_with_semantic(
        self,
        request: QuizGradeRequest,
        *,
        correct_answer: str,
        user_answer: str,
    ) -> _QuizGradeOutcome | None:
        """AI 는 isEquivalent 만 판정한다. 점수·feedback 은 서버가 고정 규칙으로 만든다."""
        question = request.question
        prompt = build_quiz_semantic_equivalence_prompt(
            question=(question.question or "").strip(),
            correct_answer=correct_answer,
            user_answer=user_answer,
        )
        try:
            is_equivalent = self._call_llm_json(
                prompt=prompt,
                system_prompt=QUIZ_SEMANTIC_EQUIVALENCE_SYSTEM_PROMPT,
                validator=self._validate_equivalence,
                max_tokens=_SEMANTIC_MAX_TOKENS,
                temperature=0.0,
            )
        except (RuntimeError, ValueError) as exc:
            self._log_ai_failure(stage="semantic", q_type=question.type, exc=exc)
            return None

        score = 100 if is_equivalent else 0
        result: GradeResult = "CORRECT" if is_equivalent else "INCORRECT"
        feedback = (
            _SEMANTIC_CORRECT_FEEDBACK
            if is_equivalent
            else _SEMANTIC_WRONG_FEEDBACK.format(correct_answer=correct_answer)
        )
        logger.info(
            "quiz_grade type=%s method=ai mode=semantic score=%d result=%s",
            question.type.value,
            score,
            result,
        )
        response = QuizGradeResponse(
            isCorrect=is_equivalent,
            score=score,
            feedback=feedback,
            correctAnswer=correct_answer,
            explanation=self._quiz_explanation(request, correct_answer),
            relatedSection=question.relatedSection,
        )
        return _QuizGradeOutcome(response=response, grading_method="ai", result=result)

    def _grade_descriptive_semantic(
        self,
        request: QuizGradeRequest,
        *,
        correct_answer: str,
        user_answer: str,
    ) -> _QuizGradeOutcome | None:
        """DESCRIPTIVE: BGE-M3 유사도로 점수를 정하고 LLM은 feedback만 생성한다."""
        question = request.question
        question_text = (question.question or "").strip()

        # 1. BGE-M3 의미 유사도 계산
        try:
            embedding_service = EmbeddingService()
            vectors = embedding_service.embed_texts(
                [correct_answer, user_answer]
            )

            correct_vec = vectors[0]
            user_vec = vectors[1]

            dot = sum(a * b for a, b in zip(correct_vec, user_vec))
            correct_norm = sum(v * v for v in correct_vec) ** 0.5
            user_norm = sum(v * v for v in user_vec) ** 0.5

            similarity = (
                dot / (correct_norm * user_norm)
                if correct_norm and user_norm
                else 0.0
            )

        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "descriptive embedding scoring failed errorType=%s error=%s",
                type(exc).__name__,
                str(exc)[:100],
            )
            return None

        # 2. similarity -> 0~100 점수 변환
        #
        # 실측값 기준:
        #   정답형     0.8854
        #   부분정답   0.8113
        #   완전오답   0.5738
        #
        # 경계:
        #   >= 0.85       CORRECT (85~100)
        #   0.70~0.8499   PARTIAL (40~79)
        #   < 0.70        INCORRECT (0~39)
        if similarity >= 0.85:
            score = round(
                85 + min(1.0, (similarity - 0.85) / 0.15) * 15
            )
        elif similarity >= 0.70:
            score = round(
                40 + ((similarity - 0.70) / 0.15) * 39
            )
        else:
            score = round(
                max(0.0, similarity) / 0.70 * 39
            )

        score = max(0, min(100, score))
        result = self._result_from_score(score)
        is_correct = result == "CORRECT"

        logger.info(
            "quiz_grade type=%s method=hybrid mode=descriptive_bge similarity=%.4f score=%d result=%s",
            question.type.value,
            similarity,
            score,
            result,
        )

        # 3. 오답은 LLM 환각을 막기 위해 서버에서 안전한 feedback을 만든다.
        if result == "INCORRECT":
            feedback = (
                "제출한 답안은 문제에서 요구한 Docker의 핵심 역할과 의미가 다릅니다. "
                "Docker가 애플리케이션과 의존성을 함께 패키징해 환경 차이를 줄이고, "
                "동일한 이미지를 재사용해 일관된 배포를 가능하게 한다는 점을 다시 확인해 보세요."
            )

            response = QuizGradeResponse(
                isCorrect=is_correct,
                score=score,
                feedback=feedback,
                correctAnswer=correct_answer,
                explanation=self._quiz_explanation(request, correct_answer),
                relatedSection=question.relatedSection,
            )

            return _QuizGradeOutcome(
                response=response,
                grading_method="ai",
                result=result,
            )

        # 4. PARTIAL / CORRECT는 LLM이 feedback만 생성
        prompt = f"""[문제]
{question_text}

[모범답안]
{correct_answer}

[학생답안]
{user_answer}

[서버 채점 결과]
점수: {score}
판정: {result}

위 점수와 판정은 서버가 이미 결정했으며 변경하면 안 된다.

학생 답안에 대해 피드백만 작성하라.

규칙:
- 학생이 잘 설명한 핵심 내용을 먼저 말한다.
- 부족한 핵심 내용이 있으면 모범답안에 근거해서만 설명한다.
- 학생 답안이 사실과 다르면 그 잘못된 내용을 사실처럼 다시 서술하지 않는다.
- 틀린 내용은 "해당 설명은 Docker의 실제 역할과 다릅니다"처럼 명확히 지적한다.
- 부분정답에서는 "모범답안이 부족하다"는 식으로 말하지 말고, 학생 답안에서 빠진 핵심 내용을 직접 설명한다.
- 문제나 모범답안에 없는 새로운 기술, 절차, 용어를 요구하지 않는다.
- 특정 단어가 없다는 이유만으로 부족하다고 하지 않는다.
- 표현이 달라도 의미가 같으면 인정한다.
- 한국어 1~2문장으로 자연스럽게 작성한다.

JSON 객체 하나만 출력:
{{"feedback":"..."}}
"""

        def validate_feedback(data: dict) -> str:
            feedback = _coerce_text(data.get("feedback")).strip()
            if not feedback:
                raise ValueError("feedback이 없습니다")
            return feedback

        try:
            feedback = self._call_llm_json(
                prompt=prompt,
                system_prompt=(
                    "너는 프로그래밍·CS 서술형 답안 피드백 작성자다. "
                    "서버가 결정한 점수와 판정을 바꾸지 말고 피드백만 작성한다."
                ),
                validator=validate_feedback,
                max_tokens=220,
                temperature=0.0,
            )
        except (RuntimeError, ValueError) as exc:
            self._log_ai_failure(
                stage="descriptive_feedback",
                q_type=question.type,
                exc=exc,
            )
            feedback = (
                "핵심 개념의 충족 정도를 기준으로 채점했습니다. "
                "모범답안과 비교해 부족한 핵심 내용을 보완해 보세요."
            )

        response = QuizGradeResponse(
            isCorrect=is_correct,
            score=score,
            feedback=feedback,
            correctAnswer=correct_answer,
            explanation=self._quiz_explanation(request, correct_answer),
            relatedSection=question.relatedSection,
        )

        return _QuizGradeOutcome(
            response=response,
            grading_method="ai",
            result=result,
        )

    def _grade_output_prediction(
        self,
        request: QuizGradeRequest,
        *,
        correct_answer: str,
        user_answer: str,
    ) -> _QuizGradeOutcome | None:
        """OUTPUT_PREDICTION: 실행 결과를 서버에서 직접 비교한다."""

        question = request.question

        def normalize_output(value: str) -> str:
            value = (value or "").strip()

            if value.startswith("```") and value.endswith("```"):
                lines = value.splitlines()

                if lines:
                    lines = lines[1:]

                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]

                value = "\n".join(lines).strip()

            return "\n".join(
                line.strip()
                for line in value.splitlines()
            ).strip()

        correct_normalized = normalize_output(correct_answer)
        user_normalized = normalize_output(user_answer)

        if user_normalized == correct_normalized:
            score = 100
            result: GradeResult = "CORRECT"
            feedback = "정답입니다. 코드의 실행 결과를 정확히 작성했습니다."
        else:
            score = 0
            result = "INCORRECT"
            feedback = (
                "오답입니다. 코드의 실행 결과가 모범 답안과 다릅니다. "
                f"정답은 '{correct_answer}'입니다."
            )

        logger.info(
            "quiz_grade type=%s method=rule mode=output_prediction "
            "score=%d result=%s",
            question.type.value,
            score,
            result,
        )

        response = QuizGradeResponse(
            isCorrect=result == "CORRECT",
            score=score,
            feedback=feedback,
            correctAnswer=correct_answer,
            explanation=self._quiz_explanation(request, correct_answer),
            relatedSection=question.relatedSection,
        )

        return _QuizGradeOutcome(
            response=response,
            grading_method="rule",
            result=result,
        )

    def _grade_code_fill(
        self,
        request: QuizGradeRequest,
        *,
        correct_answer: str,
        user_answer: str,
    ) -> _QuizGradeOutcome | None:
        """CODE_FILL: 코드 자체를 서버에서 엄격하게 비교한다."""

        question = request.question

        def normalize_code(value: str) -> str:
            value = (value or "").strip()

            # markdown code fence 제거
            if value.startswith("```") and value.endswith("```"):
                lines = value.splitlines()

                if lines:
                    lines = lines[1:]

                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]

                value = "\n".join(lines).strip()

            # 공백/개행 차이는 무시
            normalized = "".join(value.split())

            # Python에서 의미 없는 끝 세미콜론 정도는 허용
            normalized = normalized.rstrip(";")

            return normalized

        correct_normalized = normalize_code(correct_answer)
        user_normalized = normalize_code(user_answer)

        if user_normalized == correct_normalized:
            score = 100
            result: GradeResult = "CORRECT"
            feedback = "정답입니다. 빈칸에 들어갈 코드를 정확히 작성했습니다."
        else:
            score = 0
            result = "INCORRECT"
            feedback = (
                "오답입니다. 빈칸에 들어갈 코드가 모범 답안과 다릅니다. "
                f"정답은 '{correct_answer}'입니다."
            )

        logger.info(
            "quiz_grade type=%s method=rule mode=code_fill "
            "score=%d result=%s",
            question.type.value,
            score,
            result,
        )

        response = QuizGradeResponse(
            isCorrect=result == "CORRECT",
            score=score,
            feedback=feedback,
            correctAnswer=correct_answer,
            explanation=self._quiz_explanation(request, correct_answer),
            relatedSection=question.relatedSection,
        )

        return _QuizGradeOutcome(
            response=response,
            grading_method="rule",
            result=result,
        )

    def _grade_code_error_find(
        self,
        request: QuizGradeRequest,
        *,
        correct_answer: str,
        user_answer: str,
    ) -> _QuizGradeOutcome | None:
        """
        CODE_ERROR_FIND 하이브리드 채점.

        1. BGE-M3로 모범답안과 학생답안의 전체 관련성을 확인한다.
        2. 관련성이 충분하면 Ollama를 1회 호출해
           오류 원인 / 수정 방법 / 직접 모순 여부만 판정한다.
        3. 점수와 feedback은 서버가 결정한다.
        """
        question = request.question
        question_text = (question.question or "").strip()

        # ------------------------------
        # 1차: BGE-M3 관련성 필터
        # ------------------------------
        try:
            embedding_service = EmbeddingService()
            vectors = embedding_service.embed_texts(
                [correct_answer, user_answer]
            )

            correct_vec = vectors[0]
            user_vec = vectors[1]

            dot = sum(a * b for a, b in zip(correct_vec, user_vec))
            correct_norm = sum(v * v for v in correct_vec) ** 0.5
            user_norm = sum(v * v for v in user_vec) ** 0.5

            similarity = (
                dot / (correct_norm * user_norm)
                if correct_norm and user_norm
                else 0.0
            )

            logger.info(
                "quiz_grade type=%s mode=code_hybrid embedding_similarity=%.4f",
                question.type.value,
                similarity,
            )

        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "code_error_find embedding filter failed errorType=%s",
                type(exc).__name__,
            )
            similarity = 1.0

        # 관련성이 너무 낮으면 LLM까지 보내지 않는다.
        if similarity < 0.68:
            score = 0
            result: GradeResult = "INCORRECT"

            response = QuizGradeResponse(
                isCorrect=False,
                score=score,
                feedback=(
                    "오답입니다. 제출한 답변이 문제의 핵심 오류 원인과 "
                    "수정 방법에서 모범 답안과 충분히 연결되지 않습니다."
                ),
                correctAnswer=correct_answer,
                explanation=self._quiz_explanation(request, correct_answer),
                relatedSection=question.relatedSection,
            )

            logger.info(
                "quiz_grade type=%s method=ai mode=code_hybrid "
                "similarity=%.4f score=0 result=INCORRECT",
                question.type.value,
                similarity,
            )

            return _QuizGradeOutcome(
                response=response,
                grading_method="ai",
                result=result,
            )

        # ------------------------------
        # 2차: Ollama 1회 정밀 판정
        # ------------------------------
        prompt = f"""
당신은 프로그래밍 코드 오류 찾기 문제를 채점합니다.

[문제]
{question_text}

[모범 답안]
{correct_answer}

[학생 답안]
{user_answer}

학생 답안을 아래 세 항목으로만 판정하세요.

1. causeCorrect
학생이 왜 오류가 발생하는지 원인을 의미상 올바르게 설명했으면 true입니다.

표현이나 문장이 모범 답안과 달라도 같은 원인이면 true입니다.

예:
모범: "nums[3]은 리스트 범위를 벗어난다."
학생: "리스트는 0부터 시작하므로 nums[3] 위치는 존재하지 않는다."
→ causeCorrect=true

2. fixCorrect
학생이 실제로 올바른 수정 방법을 제시했으면 true입니다.

중요:
코드의 숫자, 인덱스, 변수명, 연산자 등 구체적인 수정 내용이
모범 답안과 다르면 의미가 비슷해 보여도 false입니다.

예:
모범 수정: nums[2]
학생 수정: nums[2]
→ fixCorrect=true

모범 수정: nums[2]
학생 수정: nums[4]
→ fixCorrect=false

3. hasContradiction
학생 답안에 모범 답안과 직접 충돌하는 코드, 값, 원인 또는 수정 방법이
포함되어 있으면 true입니다.

단순히 설명이 짧거나 일부 내용이 빠진 것은 contradiction이 아닙니다.

반드시 아래 JSON 형식만 반환하세요.

{{
  "causeCorrect": true,
  "fixCorrect": true,
  "hasContradiction": false
}}
""".strip()

        system_prompt = (
            "당신은 프로그래밍 코드 오류 문제의 엄격한 의미 기반 채점기입니다. "
            "문자열 일치는 요구하지 않지만, 숫자·인덱스·변수명·연산자 등 "
            "구체적인 코드 수정이 틀리면 정답으로 인정하지 않습니다. "
            "점수나 자유 형식 피드백은 생성하지 말고 세 boolean만 반환하세요."
        )

        try:
            cause_correct, fix_correct, has_contradiction = self._call_llm_json(
                prompt=prompt,
                system_prompt=system_prompt,
                validator=self._validate_code_error_find_result,
                max_tokens=_SEMANTIC_MAX_TOKENS,
                temperature=0.0,
            )
        except (RuntimeError, ValueError) as exc:
            self._log_ai_failure(
                stage="code_error_find_hybrid",
                q_type=question.type,
                exc=exc,
            )
            return None

        # LLM이 수정 방법이 없는 답안을 fixCorrect=true로 과대평가하는 경우 방어
        fix_evidence_terms = (
            "바꾸", "수정", "변경", "대신", "사용해야", "사용하면",
            "확인해야", "확인하면", "검사해야", "체크해야",
            "처리해야", "고쳐", "교체", "범위를 확인",
        )

        has_fix_evidence = any(
            term in user_answer
            for term in fix_evidence_terms
        )

        if fix_correct and not has_fix_evidence:
            logger.info(
                "code_error_find fix corrected by server "
                "reason=no_fix_evidence"
            )
            fix_correct = False

        # 모순된 답인데 LLM이 둘 다 true를 반환하는 비정상 조합 방어
        if has_contradiction and cause_correct and fix_correct:
            fix_correct = False

        # BGE-M3가 모범답안과 매우 높은 의미 유사도를 보이고,
        # 수정 방법도 맞으며 모순이 없으면 LLM의 과도한 cause=false를 보정한다.
        if (
            not cause_correct
            and fix_correct
            and not has_contradiction
            and similarity >= 0.90
        ):
            logger.info(
                "code_error_find cause corrected by embedding "
                "similarity=%.4f",
                similarity,
            )
            cause_correct = True

        if cause_correct and fix_correct and not has_contradiction:
            score = 100
            result = "CORRECT"
            feedback = (
                "정답입니다. 코드 오류의 원인과 수정 방법을 모두 정확히 설명했습니다."
            )
        elif cause_correct and not fix_correct and not has_contradiction:
            score = 60
            result = "PARTIAL"
            feedback = (
                "오류 원인은 올바르게 설명했습니다. "
                "다만 실제로 어떻게 수정해야 하는지에 대한 설명이 빠졌거나 부족합니다."
            )
        elif not cause_correct and fix_correct:
            score = 0
            result = "INCORRECT"
            feedback = (
                "수정 방법과 관련된 내용은 포함되어 있지만, "
                "왜 오류가 발생하는지에 대한 원인이 정확하지 않아 오답으로 판정했습니다."
            )
        else:
            score = 0
            result = "INCORRECT"

            if similarity >= 0.80:
                feedback = (
                    "문제의 핵심과 관련된 내용은 일부 포함되어 있지만, "
                    "오류 원인 또는 수정 방법 중 하나 이상이 정확하지 않아 오답으로 판정되었습니다. "
                    f"모범 답안을 참고하면 '{correct_answer}'입니다."
                )
            else:
                feedback = (
                    "답변이 문제의 핵심 오류 원인과 수정 방법을 충분히 설명하지 못했습니다. "
                    f"모범 답안을 참고하면 '{correct_answer}'입니다."
                )

        logger.info(
            "quiz_grade type=%s method=ai mode=code_hybrid "
            "similarity=%.4f cause=%s fix=%s contradiction=%s "
            "score=%d result=%s",
            question.type.value,
            similarity,
            cause_correct,
            fix_correct,
            has_contradiction,
            score,
            result,
        )

        response = QuizGradeResponse(
            isCorrect=result == "CORRECT",
            score=score,
            feedback=feedback,
            correctAnswer=correct_answer,
            explanation=self._quiz_explanation(request, correct_answer),
            relatedSection=question.relatedSection,
        )

        return _QuizGradeOutcome(
            response=response,
            grading_method="ai",
            result=result,
        )

    @staticmethod
    def _validate_code_error_find_result(
        data: dict,
    ) -> tuple[bool, bool, bool]:
        def parse_bool(name: str) -> bool:
            value = data.get(name)

            if isinstance(value, bool):
                return value

            if isinstance(value, str):
                normalized = value.strip().lower()
                if normalized in {"true", "false"}:
                    return normalized == "true"

            raise ValueError(f"{name} 는 true/false 여야 합니다")

        return (
            parse_bool("causeCorrect"),
            parse_bool("fixCorrect"),
            parse_bool("hasContradiction"),
        )

    @staticmethod
    def _validate_equivalence(data: dict) -> bool:
        value = data.get("isEquivalent")
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
            return value.strip().lower() == "true"
        raise ValueError("isEquivalent 는 true/false 여야 합니다")

    # ------------------------------------------------------------------
    # AI criteria 채점
    # ------------------------------------------------------------------
    def _grade_quiz_with_ai(
        self,
        request: QuizGradeRequest,
        *,
        correct_answer: str,
        user_answer: str,
    ) -> _QuizGradeOutcome | None:
        question = request.question
        q_type = question.type
        question_text = (question.question or "").strip()

        try:
            criteria = self._generate_criteria(
                question_type=q_type,
                question_text=question_text,
                correct_answer=correct_answer,
                explanation=question.explanation,
                choices=question.choices,
                grading_keywords=request.gradingKeywords,
            )
        except (RuntimeError, ValueError) as exc:
            self._log_ai_failure(stage="criteria", q_type=q_type, exc=exc)
            return None

        logger.info(
            "quiz_grade generated_criteria type=%s criteria=%s",
            q_type.value,
            [
                {
                    "id": c.id,
                    "description": c.description,
                    "weight": c.weight,
                }
                for c in criteria
            ],
        )

        try:
            criteria_results, total_score, ai_feedback = self._grade_with_criteria(
                question_type=q_type,
                question_text=question_text,
                correct_answer=correct_answer,
                criteria=criteria,
                user_answer=user_answer,
            )
        except (RuntimeError, ValueError) as exc:
            self._log_ai_failure(stage="grade", q_type=q_type, exc=exc)
            return None

        logger.info(
            "quiz_grade criteria_results type=%s results=%s",
            q_type.value,
            [
                {
                    "id": r.id,
                    "description": r.description,
                    "weight": r.weight,
                    "score": r.score,
                    "passed": r.passed,
                    "feedback": r.feedback,
                }
                for r in criteria_results
            ],
        )

        result = self._result_from_score(total_score)
        is_correct = result == "CORRECT"
        feedback = ai_feedback.strip()

        if not feedback:
            feedback = self._build_quiz_feedback(
                is_correct=is_correct,
                score=total_score,
                correct_answer=correct_answer,
                user_answer=user_answer,
                related_section=question.relatedSection,
                question_text=question_text,
            )
        logger.info(
            "quiz_grade type=%s method=ai criteria=%d score=%d result=%s",
            q_type.value,
            len(criteria),
            total_score,
            result,
        )
        response = QuizGradeResponse(
            isCorrect=is_correct,
            score=total_score,
            feedback=feedback,
            correctAnswer=correct_answer,
            explanation=self._quiz_explanation(request, correct_answer),
            relatedSection=question.relatedSection,
        )
        return _QuizGradeOutcome(
            response=response,
            grading_method="ai",
            result=result,
            criteria_results=criteria_results,
        )

    def _generate_criteria(
        self,
        *,
        question_type: QuestionType,
        question_text: str,
        correct_answer: str,
        explanation: str | None,
        choices: list[str] | None,
        grading_keywords: list[str] | None,
    ) -> list[QuizGradeCriterion]:
        """1단계: 문제·모범답안으로 criteria 를 생성한다(학생 답안 미포함)."""
        prompt = build_quiz_criteria_prompt(
            question_type=question_type.value,
            question=question_text,
            correct_answer=correct_answer,
            explanation=explanation,
            choices=choices,
            grading_keywords=grading_keywords,
        )
        return self._call_llm_json(
            prompt=prompt,
            system_prompt=QUIZ_CRITERIA_SYSTEM_PROMPT,
            validator=lambda data: self._validate_criteria(
                data,
                question_type=question_type,
            ),
            max_tokens=_CRITERIA_MAX_TOKENS,
            temperature=0.0,
        )

    def _grade_with_criteria(
        self,
        *,
        question_type: QuestionType,
        question_text: str,
        correct_answer: str,
        criteria: list[QuizGradeCriterion],
        user_answer: str,
    ) -> tuple[list[QuizGradeCriterionResult], int, str]:
        """2단계: criteria 별 충족 여부를 평가한다. totalScore 는 서버가 합산한다."""
        prompt = build_quiz_answer_grading_prompt(
            question_type=question_type.value,
            question=question_text,
            correct_answer=correct_answer,
            criteria=[(c.id, c.description, c.weight) for c in criteria],
            user_answer=user_answer,
        )
        return self._call_llm_json(
            prompt=prompt,
            system_prompt=QUIZ_ANSWER_GRADING_SYSTEM_PROMPT,
            validator=lambda data: self._validate_grade_result(data, criteria),
            max_tokens=_GRADING_MAX_TOKENS,
            temperature=0.0,
        )

    def _call_llm_json(
        self,
        *,
        prompt: str,
        system_prompt: str,
        validator: Callable[[dict], _T],
        max_tokens: int,
        temperature: float | None = None,
    ) -> _T:
        """LLM 호출 → JSON 추출 → 검증.

        RuntimeError(timeout·network·HTTP 등)는 재시도 없이 그대로 올린다.
        JSON 파싱·스키마 오류(ValueError)만 최대 1회 재시도한다.
        """
        current_prompt = prompt
        last_error: ValueError | None = None
        for attempt in range(1, _LLM_JSON_MAX_ATTEMPTS + 1):
            raw = self._llm.generate_text(
                prompt=current_prompt,
                system_prompt=system_prompt,
                timeout_seconds=settings.LLM_TIMEOUT_SECONDS,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            try:
                data = self._extract_json_from_response(raw or "")
                if not isinstance(data, dict) or not data:
                    raise ValueError("LLM 응답에서 JSON 객체를 찾지 못했습니다")
                return validator(data)
            except ValueError as exc:
                last_error = exc
                logger.info(
                    'quiz grade LLM JSON invalid attempt=%d error="%s"',
                    attempt,
                    str(exc)[:_LOG_ERROR_MAX_CHARS],
                )
                current_prompt = prompt + _LLM_JSON_RETRY_SUFFIX.format(
                    error=str(exc)[:100]
                )
        assert last_error is not None
        raise last_error

    @classmethod
    def _validate_criteria(
        cls,
        data: dict,
        *,
        question_type: QuestionType,
    ) -> list[QuizGradeCriterion]:
        items = data.get("criteria")
        if not isinstance(items, list):
            raise ValueError("criteria 배열이 없습니다")  # noqa: TRY004 - 재시도 대상 스키마 오류

        kept: list[tuple[str, int]] = []
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            description = _coerce_text(item.get("description"))
            if not cls._is_meaningful_criterion(description):
                continue
            weight = _coerce_int(item.get("weight"))
            if weight is None or weight <= 0:
                continue
            key = cls._compact_answer(description)
            if key in seen:
                continue
            seen.add(key)
            kept.append((description, weight))
            if len(kept) == _MAX_CRITERIA:
                break

        if not kept:
            raise ValueError("유효한 criteria가 없습니다")

        weights = cls._normalize_weights([weight for _, weight in kept])
        return [
            QuizGradeCriterion(id=index, description=description, weight=weight)
            for index, ((description, _), weight) in enumerate(
                zip(kept, weights, strict=True), start=1
            )
        ]

    @classmethod
    def _is_meaningful_criterion(cls, description: str) -> bool:
        if len(cls._compact_answer(description)) < _MIN_CRITERION_CHARS:
            return False
        return _VAGUE_CRITERION_RE.search(description) is None

    @staticmethod
    def _normalize_weights(weights: list[int]) -> list[int]:
        """weight 합계를 100으로 보정한다(최대 잔여 방식, 각 항목 최소 1)."""
        total = sum(weights)
        if total == 100:
            return list(weights)
        logger.info("quiz grade criteria weights normalized original_sum=%d", total)
        raw = [weight * 100 / total for weight in weights]
        normalized = [int(value) for value in raw]
        remainder = 100 - sum(normalized)
        order = sorted(
            range(len(raw)), key=lambda i: raw[i] - normalized[i], reverse=True
        )
        for index in order[:remainder]:
            normalized[index] += 1
        for index, weight in enumerate(normalized):
            if weight == 0:
                largest = max(range(len(normalized)), key=lambda i: normalized[i])
                normalized[largest] -= 1
                normalized[index] = 1
        return normalized

    @staticmethod
    def _validate_grade_result(
        data: dict,
        criteria: list[QuizGradeCriterion],
    ) -> tuple[list[QuizGradeCriterionResult], int, str]:
        items = data.get("criteriaResults")
        if not isinstance(items, list):
            raise ValueError("criteriaResults 배열이 없습니다")  # noqa: TRY004 - 재시도 대상 스키마 오류

        criteria_ids = {criterion.id for criterion in criteria}
        by_id: dict[int, dict] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            criterion_id = _coerce_int(item.get("id"))
            if criterion_id in criteria_ids and criterion_id not in by_id:
                by_id[criterion_id] = item
        if not by_id:
            raise ValueError("criteria와 일치하는 평가 결과가 없습니다")

        results: list[QuizGradeCriterionResult] = []
        for criterion in criteria:
            item = by_id.get(criterion.id)
            if item is None:
                results.append(
                    QuizGradeCriterionResult(
                        id=criterion.id,
                        description=criterion.description,
                        weight=criterion.weight,
                        passed=False,
                        score=0,
                        feedback="평가 결과가 누락되어 0점 처리했습니다.",
                    )
                )
                continue
            score = _coerce_int(item.get("score"))
            if score is None:
                score = criterion.weight if item.get("passed") is True else 0
            score = max(0, min(criterion.weight, score))
            results.append(
                QuizGradeCriterionResult(
                    id=criterion.id,
                    description=criterion.description,
                    weight=criterion.weight,
                    passed=score >= criterion.weight,
                    score=score,
                    feedback=_coerce_text(item.get("feedback"))[:_CRITERION_FEEDBACK_MAX_CHARS],
                )
            )

        total_score = sum(result.score for result in results)
        llm_total = _coerce_int(data.get("totalScore"))
        if llm_total is not None and llm_total != total_score:
            logger.info(
                "quiz grade LLM totalScore ignored llm=%d server=%d", llm_total, total_score
            )
        return results, total_score, _coerce_text(data.get("feedback"))

    @staticmethod
    def _result_from_score(score: int) -> GradeResult:
        if score >= _CORRECT_MIN_SCORE:
            return "CORRECT"
        if score >= _PARTIAL_MIN_SCORE:
            return "PARTIAL"
        return "INCORRECT"

    @staticmethod
    def _log_ai_failure(*, stage: str, q_type: QuestionType, exc: BaseException) -> None:
        logger.warning(
            'quiz grade AI failed, using rule fallback: stage=%s type=%s errorType=%s error="%s" timeout=%s',
            stage,
            q_type.value,
            type(exc).__name__,
            str(exc)[:_LOG_ERROR_MAX_CHARS],
            settings.LLM_TIMEOUT_SECONDS,
        )

    @staticmethod
    def _section_label(related_section: str | None) -> str:
        labels = {
            "overview": "개요(overview)",
            "requirements": "요구사항(requirements)",
            "flow": "흐름(flow)",
            "apiSpec": "API 명세(apiSpec)",
            "codeFiles": "코드(codeFiles)",
            "basicQuestions": "기본 문제(basicQuestions)",
            "missions": "미션(missions)",
            "interviewQuestions": "면접 질문(interviewQuestions)",
            "nextRecommendations": "다음 추천(nextRecommendations)",
        }
        if not related_section:
            return "기능템플릿"
        return labels.get(related_section, related_section)

    def _build_quiz_feedback(
        self,
        *,
        is_correct: bool,
        score: int,
        correct_answer: str,
        user_answer: str,
        related_section: str | None,
        question_text: str,
    ) -> str:
        """화면에 그대로 노출되는 코칭형 피드백.

        본문 3문장 + [보완할 포인트] + [개선 답안 예시] 구조로 구성한다.
        (응답 schema 변경 없이 feedback 한 필드 안에 담는다.)
        """

        section_label = self._section_label(related_section)
        missing = self._missing_answer_keywords(correct_answer, user_answer)

        if is_correct:
            body = self._correct_body(
                score=score,
                correct_answer=correct_answer,
                section_label=section_label,
            )
            extra = self._extra_learning_block(correct_answer)
            return self._join_blocks(body, extra)

        if score >= 50:
            body = self._partial_body(
                correct_answer=correct_answer,
                user_answer=user_answer,
                section_label=section_label,
                missing=missing,
            )
        else:
            body = self._wrong_body(
                correct_answer=correct_answer,
                user_answer=user_answer,
                section_label=section_label,
            )

        points = self._coaching_points_block(missing or extract_answer_keywords(correct_answer))
        sample = self._sample_answer_block(
            correct_answer=correct_answer,
            section_label=section_label,
            question_text=question_text,
        )
        return self._join_blocks(body, points, sample)

    @staticmethod
    def _join_blocks(*blocks: str) -> str:
        return "\n\n".join(block for block in blocks if block).strip()

    @staticmethod
    def _correct_body(*, score: int, correct_answer: str, section_label: str) -> str:
        if score >= 100:
            first = f"정답입니다. '{correct_answer}'의 핵심을 정확히 짚었습니다."
        else:
            first = (
                f"정답으로 인정됩니다. 핵심 방향은 맞지만 '{correct_answer}'처럼 "
                f"용어를 더 명확히 정리하면 좋습니다."
            )
        second = (
            f"이 답이 좋은 이유는 {section_label} 섹션에서 요구하는 핵심 개념을 "
            f"빠뜨리지 않았기 때문입니다."
        )
        third = "이유와 동작 흐름까지 한 문장으로 덧붙이면 더 완성도 높은 답이 됩니다."
        return f"{first} {second} {third}"

    def _partial_body(
        self,
        *,
        correct_answer: str,
        user_answer: str,
        section_label: str,
        missing: set[str],
    ) -> str:
        included = extract_answer_keywords(user_answer) & extract_answer_keywords(correct_answer)
        included_text = ", ".join(sorted(included)[:4]) if included else "일부 핵심어"
        missing_text = ", ".join(sorted(missing)[:4]) if missing else "핵심 개념"
        first = (
            f"부분 정답입니다. 답변에 포함한 '{included_text}'는 올바른 방향입니다."
        )
        second = (
            f"다만 정답 '{correct_answer}'에서 기대하는 '{missing_text}' 개념이 빠져 "
            f"설명이 충분하지 않습니다."
        )
        third = (
            f"다음 답변에서는 빠진 개념을 {section_label} 섹션 기준으로 함께 적어 보세요."
        )
        return f"{first} {second} {third}"

    @staticmethod
    def _wrong_body(*, correct_answer: str, user_answer: str, section_label: str) -> str:
        if not user_answer.strip():
            first = "오답입니다. 답변이 비어 있어 채점할 내용이 없습니다."
        else:
            first = (
                f"오답입니다. 제출한 답변 '{user_answer}'에는 이 문제가 요구하는 "
                f"핵심 개념이 빠져 있습니다."
            )
        second = (
            f"이 문제의 핵심은 '{correct_answer}'이며, {section_label} 섹션에서 다루는 "
            f"개념과 직접 연결됩니다."
        )
        third = (
            f"다음 답변에서는 '{correct_answer}'의 의미와 그것이 필요한 이유를 "
            f"함께 설명해 보세요."
        )
        return f"{first} {second} {third}"

    @staticmethod
    def _coaching_points_block(keywords: set[str]) -> str:
        if not keywords:
            return ""
        lines: list[str] = []
        for kw in sorted(keywords)[:4]:
            explanation = _KEYWORD_COACHING.get(
                kw,
                f"'{kw}' 개념이 정답 설명에 포함됩니다. 어떤 역할을 하는지 한 문장으로 "
                f"설명할 수 있어야 합니다.",
            )
            lines.append(f"- {kw}: {explanation}")
        return "[보완할 포인트]\n" + "\n".join(lines)

    @staticmethod
    def _sample_answer_block(
        *,
        correct_answer: str,
        section_label: str,
        question_text: str,
    ) -> str:
        topic = question_text.rstrip("?").strip() if question_text else "이 문제"
        sample = (
            f"\"{topic}에 대해서는 '{correct_answer}'을(를) 사용합니다. "
            f"이는 {section_label} 섹션에서 요구하는 처리이기 때문입니다. "
            f"따라서 해당 개념을 적용해 안전하고 일관된 동작을 보장합니다.\""
        )
        return "[개선 답안 예시]\n" + sample

    @staticmethod
    def _missing_answer_keywords(correct_answer: str, user_answer: str) -> set[str]:
        correct_keywords = extract_answer_keywords(correct_answer)
        user_keywords = extract_answer_keywords(user_answer)
        return correct_keywords - user_keywords

    @staticmethod
    def _extra_learning_block(correct_answer: str) -> str:
        keywords = extract_answer_keywords(correct_answer)
        known = [kw for kw in sorted(keywords) if kw in _KEYWORD_COACHING][:2]
        if not known:
            return ""
        lines = [f"- {kw}: {_KEYWORD_COACHING[kw]}" for kw in known]
        return "[추가로 알면 좋은 포인트]\n" + "\n".join(lines)

    @staticmethod
    def _build_explanation_fallback(
        *,
        correct_answer: str,
        related_section: str | None,
        question_text: str,
    ) -> str:
        section_label = EvaluationService._section_label(related_section)
        question_hint = f"문제 '{question_text}'의 " if question_text else ""
        return (
            f"{question_hint}정답은 '{correct_answer}'입니다. "
            f"{section_label} 섹션에서 해당 개념이 왜 필요한지, "
            f"어떤 입력·처리·결과 흐름과 연결되는지 다시 읽어 보세요. "
            f"기능템플릿에 없는 새 개념은 추가하지 말고, 템플릿 근거로 이해를 정리하세요."
        )

    def _grade_answer(
        self,
        *,
        correct_answer: str,
        user_answer: str,
        question_type: QuestionType,
        choices: list[str] | None,
    ) -> tuple[bool, int]:
        if not correct_answer:
            return False, 0
        if not user_answer:
            return False, 0

        norm_correct = normalize_answer_text(correct_answer)
        norm_user = normalize_answer_text(user_answer)

        if norm_correct == norm_user:
            return True, 100

        if question_type == QuestionType.OX:
            return self._grade_ox(correct_answer, user_answer, choices)

        if question_type == QuestionType.MULTIPLE_CHOICE and choices:
            return self._grade_multiple_choice(
                correct_answer, user_answer, choices, norm_correct, norm_user
            )

        if question_type in (
            QuestionType.SHORT_ANSWER,
            QuestionType.FILL_BLANK,
            QuestionType.CODE_FILL,
        ):
            return self._grade_short_answer(norm_correct, norm_user, correct_answer, user_answer)

        if norm_correct in norm_user or norm_user in norm_correct:
            return True, 90

        overlap = self._keyword_overlap_ratio(correct_answer, user_answer)
        if overlap >= 0.8:
            return True, max(70, int(round(overlap * 100)))
        if overlap >= 0.5:
            return False, max(50, int(round(overlap * 100)))

        return False, 0

    @staticmethod
    def _grade_multiple_choice(
        correct_answer: str,
        user_answer: str,
        choices: list[str],
        norm_correct: str,
        norm_user: str,
    ) -> tuple[bool, int]:
        correct_idx = None
        user_idx = None
        for i, choice in enumerate(choices):
            nc = normalize_answer_text(choice)
            if nc == norm_correct or choice.strip() == correct_answer.strip():
                correct_idx = i
            if nc == norm_user or choice.strip() == user_answer.strip():
                user_idx = i

        if correct_idx is not None and user_idx is not None:
            return user_idx == correct_idx, 100 if user_idx == correct_idx else 0

        if norm_user == norm_correct:
            return True, 100
        return False, 0

    @classmethod
    def _grade_ox(
        cls,
        correct_answer: str,
        user_answer: str,
        choices: list[str] | None,
    ) -> tuple[bool, int]:
        correct = cls._ox_value(correct_answer, choices)
        user = cls._ox_value(user_answer, choices)
        if correct is None or user is None:
            return False, 0
        return (True, 100) if correct == user else (False, 0)

    @staticmethod
    def _ox_value(text: str, choices: list[str] | None) -> str | None:
        """O/X 답안을 'O' 또는 'X'로 정규화한다. 보기 번호('1', '2번')는 choices 로 해석한다."""
        value = (text or "").strip()
        number = re.fullmatch(r"(\d+)\s*(?:번|\.)?", value)
        if number and choices:
            index = int(number.group(1)) - 1
            if 0 <= index < len(choices):
                value = choices[index]
        value = re.sub(r"^\s*\d+\s*(?:[.)]|번)\s*", "", value)
        value = value.strip().rstrip(".!").strip().lower()
        if value in _OX_TRUE:
            return "O"
        if value in _OX_FALSE:
            return "X"
        return None

    @staticmethod
    def _grade_short_answer(
        norm_correct: str,
        norm_user: str,
        correct_answer: str,
        user_answer: str,
    ) -> tuple[bool, int]:
        if norm_correct == norm_user:
            return True, 100

        correct_keywords = extract_answer_keywords(correct_answer)
        user_keywords = extract_answer_keywords(user_answer)
        if correct_keywords and user_keywords:
            overlap = len(correct_keywords & user_keywords) / len(correct_keywords)
            if overlap >= 0.6:
                return True, max(80, int(round(overlap * 100)))
            if overlap >= 0.5:
                return True, max(70, int(round(overlap * 100)))

        if re.sub(r"\s+", "", norm_correct) == re.sub(r"\s+", "", norm_user):
            return True, 95

        nums_correct = re.findall(r"\d+", correct_answer)
        nums_user = re.findall(r"\d+", user_answer)
        if nums_correct and nums_correct == nums_user:
            return True, 100

        return False, 0

    @staticmethod
    def _keyword_overlap_ratio(correct_answer: str, user_answer: str) -> float:
        correct_keywords = extract_answer_keywords(correct_answer)
        if not correct_keywords:
            return 0.0
        user_keywords = extract_answer_keywords(user_answer)
        return len(correct_keywords & user_keywords) / len(correct_keywords)

    def analyze_code(self, request: CodeAnalyzeRequest) -> CodeAnalyzeResponse:
        """제출 전 코드리뷰 + 오답피드백 (LLM 우선, 실패 시 rule fallback).

        실제 분석 로직은 CodeAnalyzeService 가 담당한다. 채점(passed/score)은
        다루지 않으며, 그 책임은 /ai/mission/feedback 에 있다.
        """
        return CodeAnalyzeService().analyze(request)

    def grade_java_code(self, request: JavaCodeGradingRequest) -> JavaCodeGradingResponse:
        """LLM 기반 자바 코드 정적 분석 및 채점.

        criteria 가 요청에 없으면 question 에서 자동 생성한 뒤
        기존 criteria 채점 프롬프트로 점수를 부여한다.
        LLM 호출: criteria 요청 제공 시 1회(채점), 미제공 시 2회(생성 + 채점).
        """
        from app.prompts.code_analyze_prompts import build_java_code_grading_prompt

        criteria = self._resolve_java_grading_criteria(request)

        # 프롬프트 구성
        prompt = build_java_code_grading_prompt(
            code=request.code,
            criteria=criteria,
        )

        try:
            # LLM 호출
            raw_response = self._llm.generate_text(
                prompt=prompt,
                system_prompt=_JAVA_GRADING_LLM_SYSTEM,
                timeout_seconds=settings.LLM_TIMEOUT_SECONDS,
                max_tokens=_JAVA_GRADING_MAX_TOKENS,
                temperature=0.0,
            )
            response = self._build_java_grading_response(
                _parse_java_llm_json(raw_response or ""), criteria, request
            )
            method = "llm"
        except Exception as exc:  # noqa: BLE001 - 어떤 실패든 규칙 fallback 응답으로 돌린다.
            logger.warning(
                'java_code_grade grading failed, using rule fallback errorType=%s error="%s"',
                type(exc).__name__,
                str(exc)[:_LOG_ERROR_MAX_CHARS],
            )
            # 실패 시 fallback 응답
            response = self._generate_fallback_grading_response(request, criteria)
            method = "rule_fallback"

        logger.info(
            "java_code_grade method=%s criteria=%d score=%d is_correct=%s",
            method,
            len(criteria),
            response.score,
            response.is_correct,
        )
        return response

    def _build_java_grading_response(
        self,
        parsed: dict,
        criteria: list[str],
        request: JavaCodeGradingRequest,
    ) -> JavaCodeGradingResponse:
        """LLM 채점 결과를 서버 규칙으로 보수적으로 재계산한다.

        - criteria_results 가 있으면 기준별 passed 로 점수를 서버가 계산한다.
        - 코드에서 근거(출력 문자열·변수명·if/for 등)가 확인되지 않는 기준은 passed 여도 미충족 처리한다.
        - criteria_results 가 없으면 LLM score 를 근거 상한으로 제한한다.
        - is_correct 는 score == 100 과 항상 일치시킨다.
        """
        total = len(criteria)
        evidence = [
            _java_criterion_evidence(criterion, request.code, request.question)
            for criterion in criteria
        ]
        results = _parse_java_criteria_results(parsed.get("criteria_results"), total)

        if results is None:
            if "score" not in parsed:
                raise ValueError("채점 응답에 criteria_results / score 가 없습니다")
            evidence_cap = round(100 * sum(ev is not False for ev in evidence) / total)
            score = min(self._validate_score(parsed.get("score")), evidence_cap)
            if _coerce_java_bool(parsed.get("is_correct")) is not True:
                # LLM 이 '전부 충족'이라고 하지 않았다면 최소 1개 기준은 미충족으로 본다.
                score = min(score, round(100 * (total - 1) / total))
            met: list[str] = []
            unmet = [
                (criterion, _JAVA_NO_EVIDENCE_REASON)
                for criterion, ev in zip(criteria, evidence, strict=True)
                if ev is False
            ]
        else:
            met, unmet = [], []
            for criterion, (passed, reason), ev in zip(criteria, results, evidence, strict=True):
                if passed and ev is False:
                    unmet.append((criterion, _JAVA_NO_EVIDENCE_REASON))
                elif passed:
                    met.append(criterion)
                else:
                    unmet.append((criterion, reason))
            score = round(100 * len(met) / total)

        score = max(0, min(100, score))
        is_correct = score == 100
        if results is None:
            summary = f"평가 기준 {total}개 기준 채점 결과 {score}점입니다."
        else:
            summary = f"평가 기준 {total}개 중 {len(met)}개를 충족했습니다. ({score}점)"

        return JavaCodeGradingResponse(
            is_correct=is_correct,
            score=score,
            feedback=_build_java_feedback(
                summary=summary,
                llm_feedback=_coerce_text(parsed.get("feedback")),
                met=met,
                unmet=unmet,
            ),
            formatted_code=_coerce_text(parsed.get("formatted_code")) or request.code,
        )

    def _resolve_java_grading_criteria(self, request: JavaCodeGradingRequest) -> list[str]:
        """criteria 결정 순서: 요청값 > LLM(question) > 규칙 fallback(question)."""
        if request.criteria:
            logger.info(
                "java_code_grade criteria_source=request count=%d",
                len(request.criteria),
            )
            return request.criteria

        question = (request.question or "").strip()
        try:
            criteria = self._generate_java_criteria(question)
        except Exception as exc:  # noqa: BLE001 - timeout/파싱 포함 모든 실패는 fallback.
            logger.warning(
                'java_code_grade criteria generation failed errorType=%s error="%s"',
                type(exc).__name__,
                str(exc)[:_LOG_ERROR_MAX_CHARS],
            )
            criteria = []
        if criteria:
            logger.info(
                "java_code_grade criteria_source=llm count=%d",
                len(criteria),
            )
            return criteria

        fallback = self._fallback_java_criteria(question)
        logger.info(
            "java_code_grade criteria_source=fallback count=%d",
            len(fallback),
        )
        return fallback

    def _generate_java_criteria(self, question: str) -> list[str]:
        """question → LLM criteria 생성 (1회 호출, 재시도 없음). 실패 시 예외를 올려 호출부가 fallback 한다."""
        from app.prompts.code_analyze_prompts import build_java_criteria_prompt

        raw = self._llm.generate_text(
            prompt=build_java_criteria_prompt(question=question),
            system_prompt=_JAVA_CRITERIA_LLM_SYSTEM,
            timeout_seconds=settings.LLM_TIMEOUT_SECONDS,
            max_tokens=_JAVA_CRITERIA_MAX_TOKENS,
            temperature=0.0,
        )
        return self._validate_java_criteria(_parse_java_llm_json(raw or ""), question)

    @staticmethod
    def _validate_java_criteria(data: dict, question: str) -> list[str]:
        """LLM criteria 검증: 빈/짧은/추상/중복/문제에 없는 요구사항 제거, 최대 5개."""
        if not isinstance(data, dict) or "criteria" not in data:
            raise ValueError("criteria key가 없습니다")
        items = data["criteria"]
        if not isinstance(items, list):
            raise ValueError("criteria가 배열이 아닙니다")  # noqa: TRY004 - fallback 대상 스키마 오류

        kept: list[str] = []
        dropped: dict[str, int] = {}
        for item in items:
            text = _normalize_java_criterion(item)
            reason = _java_criterion_reject_reason(text, question, kept)
            if reason:
                dropped[reason] = dropped.get(reason, 0) + 1
                continue
            kept.append(text)
        if dropped:
            logger.info("java_code_grade criteria_dropped %s", dropped)
        if not kept:
            raise ValueError("유효한 criteria가 없습니다")
        return kept[:_JAVA_CRITERIA_MAX]

    @staticmethod
    def _fallback_java_criteria(question: str) -> list[str]:
        """LLM 실패 시 question 에 실제로 등장한 요구사항만 규칙으로 추출한다."""
        return _extract_java_fallback_criteria(question)

    def _extract_json_from_response(self, text: str) -> dict:
        """LLM 응답에서 JSON을 추출한다."""
        # 마크다운 코드블록 제거
        cleaned = re.sub(r"```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        cleaned = cleaned.replace("```", "").strip()
        
        # JSON 파싱 시도
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            # { } 객체 추출 시도
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start != -1 and end > start:
                try:
                    return json.loads(cleaned[start:end+1])
                except json.JSONDecodeError:
                    pass
        
        logger.warning("Failed to extract JSON from LLM response: %s", cleaned[:200])
        return {}

    def _validate_score(self, score: int) -> int:
        """점수가 0~100 범위 내에 있는지 확인한다."""
        try:
            score_int = int(score)
            return max(0, min(100, score_int))
        except (ValueError, TypeError):
            return 0

    def _generate_fallback_grading_response(
        self,
        request: JavaCodeGradingRequest,
        criteria: list[str],
    ) -> JavaCodeGradingResponse:
        """LLM 실패 시 결정된 criteria 기준 규칙 매칭으로 채점한다.

        int/if/for/들여쓰기를 무조건 채점하지 않고 criteria 에 있는 요구사항만 검사한다.
        코드에서 근거를 확인할 수 없는 기준은 충족으로 간주하지 않는다 (보수적 채점).
        """
        met: list[str] = []
        unmet: list[tuple[str, str]] = []
        for criterion in criteria:
            evidence = _java_criterion_evidence(criterion, request.code, request.question)
            if evidence is True:
                met.append(criterion)
            elif evidence is False:
                unmet.append((criterion, _JAVA_NO_EVIDENCE_REASON))
            else:
                unmet.append((criterion, "규칙으로 확인할 수 없어 미충족 처리했습니다."))

        total = len(criteria)
        score = round(100 * len(met) / total) if total else 0

        return JavaCodeGradingResponse(
            is_correct=total > 0 and score == 100,
            score=score,
            feedback=_build_java_feedback(
                summary=(
                    f"LLM 채점 실패로 규칙 기반 채점을 적용했습니다. "
                    f"(평가 기준 {total}개 중 {len(met)}개 충족, {score}점)"
                ),
                llm_feedback="",
                met=met,
                unmet=unmet,
            ),
            formatted_code=request.code,
        )


def _coerce_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _coerce_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return round(float(value))
    except (TypeError, ValueError, OverflowError):
        return None


# ---------------------------------------------------------------------------
# /ai/java/grade 전용 헬퍼 (quiz grading 로직과 공유하지 않는다)
# ---------------------------------------------------------------------------
_JAVA_CRITERIA_MAX = 5
_JAVA_CRITERIA_MAX_TOKENS = 256
_JAVA_GRADING_MAX_TOKENS = 1024
_JAVA_MIN_CRITERION_CHARS = 6
_JAVA_DUPLICATE_SIMILARITY = 0.8
_JAVA_FALLBACK_QUOTED_MAX = 3
# 상세 규약은 user prompt(builder)에 있으므로 system 은 짧게 유지해 토큰 중복을 피한다.
_JAVA_CRITERIA_LLM_SYSTEM = "너는 자바 코딩테스트 평가 기준 생성기다. 규약의 JSON 객체 하나만 출력한다."
_JAVA_GRADING_LLM_SYSTEM = "너는 자바 코딩테스트 채점 전문가다. 규약의 JSON 객체 하나만 출력한다."
_JAVA_NO_EVIDENCE_REASON = "코드에서 해당 요구사항의 근거를 찾지 못했습니다."

_JAVA_QUOTED_RE = re.compile(r'"([^"]+)"|\'([^\']+)\'|“([^”]+)”|‘([^’]+)’')
_JAVA_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_.])\d+(?:\.\d+)?(?![A-Za-z0-9_])")
_JAVA_IDENT_RE = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z_][A-Za-z0-9_]*(?![A-Za-z0-9_])")
_JAVA_STRING_LITERAL_RE = re.compile(r'"((?:[^"\\\n]|\\.)*)"')
_JAVA_CODE_COMMENT_RE = re.compile(r'("(?:[^"\\\n]|\\.)*")|//[^\n]*|/\*.*?\*/', re.DOTALL)
_JAVA_CODE_LIKE_RE = re.compile(r"[=<>(){};]")
# "그렇지 않으면"(else)은 부정 기준이 아니다.
_JAVA_NEGATION_RE = re.compile(r"(?<!그렇지 )않|없이|금지|말고")
_JAVA_CRITERION_PREFIX_RE = re.compile(r"^\s*(?:\d+\s*[.)]|[-*•])\s*")
_JAVA_CRITERION_ENDING_RE = re.compile(r"(?:했|하였|되었|됐|하)?(?:는가|는지|나|인가)$")
_JAVA_STOP_IDENTIFIERS = frozenset(
    {
        "if", "else", "for", "while", "do", "switch", "case", "break", "continue",
        "int", "long", "short", "byte", "double", "float", "boolean", "char", "string",
        "system", "out", "println", "print", "printf", "scanner", "main", "args",
        "public", "private", "protected", "static", "void", "class", "return", "new",
        "true", "false", "null", "java", "array", "method", "and", "or", "not",
    }
)

# (이름, 기준 문장 패턴, 문제 문장 허용 패턴, 코드 근거 패턴)
# - 기준에 개념이 있는데 문제에 없으면 '문제에 없는 요구사항'으로 제거한다.
# - 기준에 개념이 있으면 코드에서 해당 근거 패턴을 확인한다.
_JAVA_CONCEPTS: tuple[tuple[str, re.Pattern[str], re.Pattern[str], re.Pattern[str]], ...] = (
    (
        "for",
        re.compile(r"\bfor\b|for문", re.I),
        re.compile(r"\bfor\b|for문|반복|부터.{0,20}까지", re.I),
        re.compile(r"\bfor\s*\("),
    ),
    (
        "while",
        re.compile(r"\bwhile\b|while문", re.I),
        re.compile(r"\bwhile\b|while문|반복|부터.{0,20}까지", re.I),
        re.compile(r"\bwhile\s*\("),
    ),
    (
        "loop",
        re.compile(r"반복"),
        re.compile(r"\bfor\b|for문|\bwhile\b|while문|반복|부터.{0,20}까지", re.I),
        re.compile(r"\b(?:for|while)\s*\("),
    ),
    (
        "if",
        re.compile(r"\bif\b|if문|조건문", re.I),
        re.compile(r"\bif\b|if문|조건|만약|면(?:\s|,|$)|인지|여부|짝수|홀수|비교", re.I),
        re.compile(r"\bif\s*\("),
    ),
    (
        "else",
        re.compile(r"\belse\b|else문|그렇지\s*않|아니면|거짓", re.I),
        re.compile(r"\belse\b|else문|그렇지\s*않|아니면|아니라면|거짓|아닐|불합격|외에는|나머지\s*경우", re.I),
        re.compile(r"\belse\b"),
    ),
    (
        "print",
        re.compile(r"출력|print", re.I),
        re.compile(r"출력|print|표시|보여", re.I),
        re.compile(r"System\s*\.\s*out\s*\.\s*print"),
    ),
    (
        "input",
        re.compile(r"입력|scanner", re.I),
        re.compile(r"입력|scanner", re.I),
        re.compile(r"\bScanner\b"),
    ),
    (
        "array",
        re.compile(r"배열|array|\[\s*\]", re.I),
        re.compile(r"배열|array|\[\s*\]", re.I),
        re.compile(r"\[\s*\d*\s*\]"),
    ),
    (
        "method",
        re.compile(r"메서드|메소드|함수|method", re.I),
        re.compile(r"메서드|메소드|함수|method", re.I),
        re.compile(r"\b(?:static|public|private|protected)\b[^;{=]*\([^)]*\)\s*\{"),
    ),
    (
        "int",
        re.compile(r"정수형|int형|\bint\b", re.I),
        re.compile(r"정수|int형|\bint\b|\blong\b", re.I),
        re.compile(r"\b(?:int|long|short|byte)\b"),
    ),
    (
        "double",
        re.compile(r"실수|double|float", re.I),
        re.compile(r"실수|double|float", re.I),
        re.compile(r"\b(?:double|float)\b"),
    ),
    (
        "add",
        re.compile(r"덧셈|더하|더한|더해|합계|합을|\+"),
        re.compile(r"덧셈|더하|더한|더해|합(?!격)|\+|plus", re.I),
        re.compile(r"[^+]\+[^+=]|\+="),
    ),
    (
        "sub",
        re.compile(r"뺄셈|빼|뺀|차이"),
        re.compile(r"뺄셈|빼|뺀|차이|-"),
        re.compile(r"[^-]-[^-=>]|-="),
    ),
    (
        "mul",
        re.compile(r"곱"),
        re.compile(r"곱|\*"),
        re.compile(r"\*"),
    ),
    (
        "div",
        re.compile(r"나눗셈|나누|나눈|몫"),
        re.compile(r"나눗셈|나누|나눈|몫|/"),
        re.compile(r"/"),
    ),
    (
        "mod",
        re.compile(r"나머지|%|짝수|홀수|배수"),
        re.compile(r"나머지|%|짝수|홀수|배수"),
        re.compile(r"%"),
    ),
)

# fallback criteria 추출 규칙 (question 에 실제 등장한 요구사항만 사용)
_JAVA_ASSIGN_RE = re.compile(
    r"(?:(?P<type>정수형|실수형|문자열|정수|실수)\s*(?:타입의?\s*)?)?(?:변수\s*)?"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:에는|에다|에)\s*(?:값\s*)?"
    r"(?P<value>\d+(?:\.\d+)?|\"[^\"]*\"|'[^']*')\s*(?:을|를)?\s*(?:저장|대입|할당|넣|초기화|담)"
)
_JAVA_COMPARE_RE = re.compile(
    r"(?P<num>\d+(?:\.\d+)?)\s*"
    r"(?P<rel>보다\s*(?:크|큰|많|작|작은|적)|이상|이하|초과|미만|(?:과|와)\s*같)"
)
_JAVA_RANGE_RE = re.compile(r"(?P<a>\d+)\s*부터\s*(?P<b>\d+)\s*까지")
_JAVA_EXPLICIT_IF_RE = re.compile(r"\bif\b|if문|조건문|만약", re.I)
_JAVA_EXPLICIT_ELSE_RE = re.compile(r"\belse\b|else문|그렇지\s*않으면|아니면|아니라면", re.I)
_JAVA_FOR_RE = re.compile(r"\bfor\b|for문", re.I)
_JAVA_WHILE_RE = re.compile(r"\bwhile\b|while문", re.I)
_JAVA_PRINT_RE = re.compile(r"출력|print", re.I)
_JAVA_ARITHMETIC_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"더하|더한|더해|덧셈|합을|합계"), "덧셈(+)"),
    (re.compile(r"뺄셈|빼|뺀"), "뺄셈(-)"),
    (re.compile(r"곱하|곱한|곱해|곱셈"), "곱셈(*)"),
    (re.compile(r"나누|나눈|나눗셈|몫"), "나눗셈(/)"),
    (re.compile(r"나머지"), "나머지(%)"),
)
# 추출 실패 시 문제 문장을 요구사항 절로 나누기 위한 구분자.
_JAVA_QUESTION_CLAUSE_SPLIT_RE = re.compile(
    r"(?:한\s*뒤|한\s*후|후에|그런\s*다음|그리고|다음으로|다음에|마지막으로|하고|하여|[,，.。;；])\s*"
)
_JAVA_IMPERATIVE_ENDING_RE = re.compile(
    r"\s*(?:하세요|하시오|해\s*주세요|해주세요|하라|해야\s*합니다|해야\s*한다|합니다|한다)\.?\s*$"
)


def _parse_java_llm_json(text: str) -> dict:
    """코드펜스·앞뒤 설명이 섞인 LLM 응답에서 첫 JSON 객체를 추출한다. 실패 시 ValueError."""
    cleaned = re.sub(r"```(?:json)?\s*", "", text or "", flags=re.IGNORECASE)
    cleaned = cleaned.replace("```", "").strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        parsed = None
        decoder = json.JSONDecoder()
        for index, char in enumerate(cleaned):
            if char != "{":
                continue
            try:
                candidate, _ = decoder.raw_decode(cleaned[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                parsed = candidate
                break
    if not isinstance(parsed, dict):
        raise ValueError("LLM 응답에서 JSON 객체를 찾지 못했습니다")
    return parsed


def _coerce_java_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    return None


def _strip_java_quoted(text: str) -> str:
    return _JAVA_QUOTED_RE.sub(" ", text)


def _java_quoted_values(text: str) -> list[str]:
    return [
        next(group for group in match.groups() if group is not None).strip()
        for match in _JAVA_QUOTED_RE.finditer(text)
    ]


def _java_identifiers(text: str) -> list[str]:
    return [
        ident
        for ident in _JAVA_IDENT_RE.findall(text)
        if ident.lower() not in _JAVA_STOP_IDENTIFIERS
    ]


def _java_has_identifier(ident: str, text: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(ident)}(?![A-Za-z0-9_])", text) is not None


def _java_clues(criterion: str) -> tuple[list[str], list[str], list[str], list[re.Pattern[str]]]:
    """기준 문장에서 코드로 확인 가능한 단서를 뽑는다.

    반환: (출력 문자열, 숫자, 식별자, 코드 근거 패턴)
    따옴표 안 내용은 출력 문자열로만 취급하고 숫자/식별자/개념 추출에서는 제외한다.
    """
    outputs: list[str] = []
    identifiers: list[str] = []
    for value in _java_quoted_values(criterion):
        if not value or _JAVA_CODE_LIKE_RE.search(value):
            continue
        if _JAVA_IDENT_RE.fullmatch(value) and value.lower() not in _JAVA_STOP_IDENTIFIERS:
            identifiers.append(value)
        elif value.lower() not in _JAVA_STOP_IDENTIFIERS:
            outputs.append(value)
    bare = _strip_java_quoted(criterion)
    identifiers += _java_identifiers(bare)
    numbers = _JAVA_NUMBER_RE.findall(bare)
    concepts = [code_re for _, crit_re, _, code_re in _JAVA_CONCEPTS if crit_re.search(bare)]
    return outputs, numbers, list(dict.fromkeys(identifiers)), concepts


def _java_code_views(code: str) -> tuple[str, list[str]]:
    """(주석·문자열 리터럴 내용을 제거한 코드, 문자열 리터럴 목록)."""
    without_comments = _JAVA_CODE_COMMENT_RE.sub(lambda m: m.group(1) or " ", code)
    literals = [
        literal.replace('\\"', '"') for literal in _JAVA_STRING_LITERAL_RE.findall(without_comments)
    ]
    return _JAVA_STRING_LITERAL_RE.sub('""', without_comments), literals


def _java_number_in_code(number: str, code: str) -> bool:
    candidates = {number}
    if number.isdigit():
        # 'score > 60' ↔ 'score >= 61', 'i <= 5' ↔ 'i < 6' 같은 동치 표현 허용.
        value = int(number)
        candidates |= {str(value - 1), str(value + 1)}
    return any(
        re.search(rf"(?<![A-Za-z0-9_.]){re.escape(c)}(?![A-Za-z0-9_])", code) for c in candidates
    )


def _java_criterion_evidence(criterion: str, code: str, question: str | None) -> bool | None:
    """기준의 코드 근거 확인.

    True: 확인 가능한 단서가 모두 코드에 있음 / False: 하나 이상 없음 / None: 확인할 단서 없음.
    question 이 있으면 문제에 등장한 숫자·식별자·출력 문자열만 강제한다
    (계산 결과값처럼 코드에 직접 나오지 않을 수 있는 값으로 감점하지 않기 위함).
    """
    if _JAVA_NEGATION_RE.search(criterion):
        return None
    q = (question or "").strip()
    outputs, numbers, identifiers, concepts = _java_clues(criterion)
    # 문제에 따옴표로 명시된 출력 문자열이 기준 문장에 따옴표 없이 등장해도 근거로 쓴다.
    bare = _strip_java_quoted(criterion)
    for value in _java_quoted_values(q):
        if (
            value
            and value not in outputs
            and not _JAVA_CODE_LIKE_RE.search(value)
            and re.search(rf"(?<![가-힣A-Za-z0-9]){re.escape(value)}(?![A-Za-z0-9])", bare)
        ):
            outputs.append(value)
    code_clean, literals = _java_code_views(code)

    checks: list[bool] = []
    for value in outputs:
        if not q or value in q:
            # '합격' 기준이 "불합격" 리터럴로 통과하지 않도록 단어 경계를 둔다.
            pattern = re.compile(rf"(?<![가-힣A-Za-z0-9]){re.escape(value)}(?![가-힣A-Za-z0-9])")
            checks.append(any(pattern.search(literal) for literal in literals))
    if q:
        question_numbers = set(_JAVA_NUMBER_RE.findall(q))
        checks += [_java_number_in_code(n, code_clean) for n in numbers if n in question_numbers]
    for ident in identifiers:
        if not q or _java_has_identifier(ident, q):
            checks.append(_java_has_identifier(ident, code_clean))
    checks += [bool(code_re.search(code_clean)) for code_re in concepts]
    return all(checks) if checks else None


def _normalize_java_criterion(item: object) -> str:
    if isinstance(item, dict):
        item = next(
            (item[key] for key in ("description", "criterion", "text", "content") if item.get(key)),
            None,
        )
    if not isinstance(item, str):
        return ""
    text = _JAVA_CRITERION_PREFIX_RE.sub("", item)
    return re.sub(r"\s+", " ", text).strip()


def _java_criterion_key(text: str) -> str:
    compact = re.sub(r"[\s\"'“”‘’.,!?()]", "", text.lower())
    return _JAVA_CRITERION_ENDING_RE.sub("", compact)


def _java_is_duplicate(text: str, kept: list[str]) -> bool:
    """같은 단서(출력 문자열·숫자·식별자)를 가지면서 문장이 거의 같으면 중복으로 본다."""
    key = _java_criterion_key(text)
    clues = _java_clues(text)[:3]
    bigrams = {key[i : i + 2] for i in range(len(key) - 1)}
    for other in kept:
        other_key = _java_criterion_key(other)
        if key == other_key:
            return True
        if _java_clues(other)[:3] != clues:
            continue
        other_bigrams = {other_key[i : i + 2] for i in range(len(other_key) - 1)}
        union = bigrams | other_bigrams
        if union and len(bigrams & other_bigrams) / len(union) >= _JAVA_DUPLICATE_SIMILARITY:
            return True
    return False


def _java_criterion_reject_reason(text: str, question: str, kept: list[str]) -> str | None:
    if not text:
        return "empty"
    if len(re.sub(r"\s+", "", text)) < _JAVA_MIN_CRITERION_CHARS:
        return "too_short"
    outputs, numbers, identifiers, concepts = _java_clues(text)
    if not (outputs or numbers or identifiers or concepts):
        # "올바르게 구현했는가" 처럼 코드에서 확인할 단서가 없는 추상 기준
        return "vague"
    bare = _strip_java_quoted(text)
    for _, crit_re, question_re, _ in _JAVA_CONCEPTS:
        if crit_re.search(bare) and not question_re.search(question):
            return "not_in_question"
    # 1글자 식별자(i, j 등 반복 변수)는 문제에 없어도 허용한다.
    if any(len(ident) > 1 and not _java_has_identifier(ident, question) for ident in identifiers):
        return "not_in_question"
    if any(value not in question for value in outputs):
        return "not_in_question"
    if _java_is_duplicate(text, kept):
        return "duplicate"
    return None


def _parse_java_criteria_results(value: object, total: int) -> list[tuple[bool, str]] | None:
    """채점 응답의 criteria_results → 기준 순서대로 (passed, reason). 유효 항목이 없으면 None."""
    if not isinstance(value, list) or not value or total <= 0:
        return None
    results: list[tuple[bool, str] | None] = [None] * total
    for position, item in enumerate(value):
        if isinstance(item, dict):
            index = _coerce_int(item.get("index"))
            slot = index - 1 if index is not None and 1 <= index <= total else position
            passed = _coerce_java_bool(item.get("passed"))
            reason = _coerce_text(item.get("reason"))
        elif isinstance(item, bool):
            slot, passed, reason = position, item, ""
        else:
            continue
        if passed is None or not 0 <= slot < total or results[slot] is not None:
            continue
        results[slot] = (passed, reason)
    if all(result is None for result in results):
        return None
    return [result or (False, "채점 결과가 누락되어 미충족 처리했습니다.") for result in results]


def _build_java_feedback(
    *,
    summary: str,
    llm_feedback: str,
    met: list[str],
    unmet: list[tuple[str, str]],
) -> str:
    blocks = [summary]
    if llm_feedback:
        blocks.append(llm_feedback)
    if met:
        blocks.append("[충족한 요구사항]\n" + "\n".join(f"- {c}" for c in met))
    if unmet:
        blocks.append(
            "[부족한 요구사항]\n"
            + "\n".join(f"- {c}" + (f" ({reason})" if reason else "") for c, reason in unmet)
        )
    return "\n\n".join(blocks)


def _java_relation(rel: str) -> str:
    rel = re.sub(r"\s+", "", rel)
    if rel == "이상":
        return ">="
    if rel == "이하":
        return "<="
    if rel.endswith("같"):
        return "=="
    if rel == "초과" or rel[-1:] in {"크", "큰", "많"}:
        return ">"
    return "<"


def _extract_java_fallback_criteria(question: str) -> list[str]:
    """question 에서 명확히 추출 가능한 요구사항만 기준으로 만든다.

    if/반복문/출력 기준은 문제 문장에 해당 요구가 있을 때만 생성한다.
    추출이 전혀 안 되면 일반 하드코딩 기준 대신 문제 문장 절을 그대로 기준으로 쓴다.
    """
    q = (question or "").strip()
    criteria: list[str] = []

    def add(text: str) -> None:
        if not _java_is_duplicate(text, criteria):
            criteria.append(text)

    # 1. 변수 저장
    assigned: list[str] = []
    for match in _JAVA_ASSIGN_RE.finditer(q):
        type_word = {"정수": "정수형", "실수": "실수형"}.get(match["type"] or "", match["type"] or "")
        value = match["value"]
        if value[0] == "'":
            value = f'"{value[1:-1]}"'
        prefix = f"{type_word} 변수" if type_word else "변수"
        add(f"{prefix} {match['name']}에 {value}을(를) 저장했는가")
        assigned.append(match["name"])

    def subject(before: str) -> str:
        if assigned:
            return assigned[0]
        idents = _java_identifiers(before) or _java_identifiers(q)
        return idents[-1] if idents else "값"

    # 2. 조건 (if 는 문제에 명시된 경우에만 기준 문장에 넣는다)
    explicit_if = _JAVA_EXPLICIT_IF_RE.search(q) is not None
    if_prefix = "if문으로 " if explicit_if else ""
    compare = _JAVA_COMPARE_RE.search(q)
    parity = re.search(r"짝수|홀수", q)
    if compare:
        target = subject(q[: compare.start()])
        add(f"{if_prefix}{target} {_java_relation(compare['rel'])} {compare['num']} 조건을 비교했는가")
    if parity:
        target = subject(q[: parity.start()])
        add(f"{if_prefix}% 연산자로 {target}이(가) {parity.group()}인지 검사했는가")
    if explicit_if and not (compare or parity):
        add("if문으로 조건을 검사했는가")
    if _JAVA_EXPLICIT_ELSE_RE.search(q):
        add("else로 조건이 거짓인 경우를 처리했는가")

    # 3. 반복 (for/while/반복 이 문제에 있을 때만)
    loop_word = (
        "for문" if _JAVA_FOR_RE.search(q)
        else "while문" if _JAVA_WHILE_RE.search(q)
        else "반복문" if "반복" in q
        else ""
    )
    loop_range = _JAVA_RANGE_RE.search(q)
    if loop_word and loop_range:
        add(f"{loop_word}으로 {loop_range['a']}부터 {loop_range['b']}까지 반복했는가")
    elif loop_word:
        add(f"{loop_word}을 사용해 반복했는가")
    elif loop_range:
        add(f"{loop_range['a']}부터 {loop_range['b']}까지의 값을 처리했는가")

    # 4. 산술 연산
    for pattern, label in _JAVA_ARITHMETIC_RULES:
        if label == "나머지(%)" and parity:
            continue
        if pattern.search(q):
            add(f"{label} 연산으로 값을 계산했는가")

    # 5. 출력 (출력 요구가 있을 때만)
    if _JAVA_PRINT_RE.search(q):
        outputs = [
            value
            for value in _java_quoted_values(q)
            if value and not _JAVA_CODE_LIKE_RE.search(value)
            and not any(f'"{value}"' in c for c in criteria)
        ][:_JAVA_FALLBACK_QUOTED_MAX]
        for value in outputs:
            add(f'"{value}"을(를) 출력했는가')
        if not outputs:
            add("System.out.println으로 결과를 출력했는가")

    if criteria:
        return criteria[:_JAVA_CRITERIA_MAX]

    clauses = [
        _JAVA_IMPERATIVE_ENDING_RE.sub("", clause.strip()).strip()
        for clause in _JAVA_QUESTION_CLAUSE_SPLIT_RE.split(q)
    ]
    clauses = [clause for clause in clauses if clause]
    if not clauses and q:
        clauses = [q[:100]]
    return [f"문제 요구사항({clause})을 구현했는가" for clause in clauses[:_JAVA_CRITERIA_MAX]]
