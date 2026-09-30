"""기능템플릿 기본 문제 채점 + 코드 조각 분석 service."""

from __future__ import annotations

import json
import logging
import re

from app.models.enums import QuestionType
from app.schemas.evaluation import (
    CodeAnalyzeRequest,
    CodeAnalyzeResponse,
    JavaCodeGradingRequest,
    JavaCodeGradingResponse,
    QuizGradeRequest,
    QuizGradeResponse,
)
from app.services.code_analyze_service import CodeAnalyzeService
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

    def grade_quiz(self, request: QuizGradeRequest) -> QuizGradeResponse:
        correct_answer = (request.question.answer or "").strip()
        user_answer = (request.userAnswer or "").strip()
        q_type = request.question.type

        is_correct, score = self._grade_answer(
            correct_answer=correct_answer,
            user_answer=user_answer,
            question_type=q_type,
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

        return QuizGradeResponse(
            isCorrect=is_correct,
            score=score,
            feedback=feedback,
            correctAnswer=correct_answer,
            explanation=(
                request.question.explanation
                or self._build_explanation_fallback(
                    correct_answer=correct_answer,
                    related_section=request.question.relatedSection,
                    question_text=(request.question.question or "").strip(),
                )
            ),
            relatedSection=request.question.relatedSection,
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

        평가 기준(criteria)에 따라 자바 코드를 분석하고 점수를 부여한다.
        """
        from app.prompts.code_analyze_prompts import build_java_code_grading_prompt

        llm = LLMService()
        
        # 프롬프트 구성
        prompt = build_java_code_grading_prompt(
            code=request.code,
            criteria=request.criteria,
        )
        
        try:
            # LLM 호출
            system_prompt = "너는 자바 코딩테스트 채점 전문가다."
            raw_response = llm.generate_text(
                prompt=prompt,
                system_prompt=system_prompt,
                timeout_seconds=60,
            )
            
            # JSON 파싱
            parsed = self._extract_json_from_response(raw_response)
            
            # 응답 객체 생성
            return JavaCodeGradingResponse(
                is_correct=parsed.get("is_correct", False),
                score=self._validate_score(parsed.get("score", 0)),
                feedback=parsed.get("feedback", "채점 피드백을 생성할 수 없었습니다."),
                formatted_code=parsed.get("formatted_code", request.code),
            )
            
        except Exception as exc:
            logger.error("Java code grading failed: %s", exc)
            # 실패 시 fallback 응답
            return self._generate_fallback_grading_response(request)

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

    def _generate_fallback_grading_response(self, request: JavaCodeGradingRequest) -> JavaCodeGradingResponse:
        """LLM 실패 시 fallback 채점 응답을 생성한다."""
        # 간단한 규칙 기반 채점
        code_lower = request.code.lower()
        
        # 기본 점수
        score = 0
        feedback_parts = []
        
        # 정수형 변수 선언 확인
        if any(keyword in code_lower for keyword in ["int ", "long ", "short ", "byte "]):
            score += 25
            feedback_parts.append("정수형 변수 선언이 확인되었습니다.")
        else:
            feedback_parts.append("정수형 변수 선언이 없습니다.")
        
        # 조건문 확인
        if "if " in code_lower:
            score += 25
            feedback_parts.append("조건문(if)이 사용되었습니다.")
        else:
            feedback_parts.append("조건문(if)이 없습니다.")
        
        # 반복문 확인
        if any(keyword in code_lower for keyword in ["for ", "while "]):
            score += 25
            feedback_parts.append("반복문(for/while)이 사용되었습니다.")
        else:
            feedback_parts.append("반복문(for/while)이 없습니다.")
        
        # 들여쓰기 기본 확인
        if "\t" in request.code or "    " in request.code:
            score += 25
            feedback_parts.append("기본적인 들여쓰기가 확인되었습니다.")
        else:
            feedback_parts.append("들여쓰기가 부족합니다.")
        
        feedback = " ".join(feedback_parts)
        
        return JavaCodeGradingResponse(
            is_correct=score >= 75,
            score=score,
            feedback=feedback,
            formatted_code=request.code,
        )
