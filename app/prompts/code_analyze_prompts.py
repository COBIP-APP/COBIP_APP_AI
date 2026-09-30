"""POST /ai/code/analyze 용 프롬프트 모음.

실제 LLM 호출은 code_analyze_service 에서 수행한다.
이 파일은 프롬프트 문자열·조립 헬퍼만 보관한다.

작은 모델(qwen2.5-coder:1.5b)이 터널 경유에서도 안정적으로 처리하도록
프롬프트를 짧게 유지하고, 코드 구간은 마크다운 코드펜스(```) 대신
평문 구분자([현재 코드 시작]/[현재 코드 끝])로 감싼다.
LLMService.generate_json 은 단일 prompt 만 받으므로 출력 규약도 prompt 안에 싣는다.
"""

from __future__ import annotations

__all__ = [
    "CODE_ANALYZE_SYSTEM_PROMPT",
    "build_code_analyze_prompt",
    "JAVA_CODE_GRADING_SYSTEM_PROMPT",
    "build_java_code_grading_prompt",
]


CODE_ANALYZE_SYSTEM_PROMPT = """\
너는 학습용 코드 분석 도우미다. 제출 전 코드를 보고 코드리뷰와 오답피드백을 제공한다.
채점기가 아니므로 통과/실패·점수는 매기지 않는다.

규칙:
- 코드리뷰: 구조·흐름·책임 분리·가독성·예외 처리·입력 검증 관점으로 본다.
- 오답피드백: requirements/context/successCriteria 기준으로 충족·누락·오구현을 구분한다.
- 제출 코드에 없는 내용은 추측하지 않는다.
- 정답/완성 코드를 출력하지 않는다. improvementSuggestions 는 짧은 힌트만 쓴다.

출력: 아래 8개 key 만 가진 JSON 객체 하나만 출력한다. JSON 밖 텍스트·코드펜스 금지.
- summary(문자열): 코드 요약
- explanation(문자열): 코드리뷰 설명
- potentialIssues(문자열 배열): 코드리뷰 관점 이슈
- improvementSuggestions(문자열 배열): 짧은 수정 힌트
- satisfiedRequirements(문자열 배열): 충족한 요구사항
- missingRequirements(문자열 배열): 누락된 요구사항
- incorrectParts(문자열 배열): 잘못 구현된 부분
- wrongAnswerFeedback(문자열 배열): 요구사항과 안 맞는 이유
값은 한국어, 배열 항목은 1~2문장. requirements/successCriteria 가 없으면 해당 배열은 []."""


def _format_list_inline(title: str, items: list[str]) -> str:
    cleaned = [item.strip() for item in items if item and item.strip()]
    if not cleaned:
        return f"{title}: (없음)"
    return f"{title}: " + " / ".join(cleaned)


def build_code_analyze_prompt(
    *,
    code: str,
    language: str,
    context: str | None,
    mission_title: str | None,
    mission_description: str | None,
    requirements: list[str],
    success_criteria: list[str],
) -> str:
    """system 규약 + 분석 대상 컨텍스트를 단일 prompt 문자열로 조립한다.

    코드 구간은 코드펜스 없이 평문 구분자로 감싼다.
    """

    context_text = (context or "").strip() or "(없음)"
    mission_title_text = (mission_title or "").strip() or "(없음)"
    mission_desc_text = (mission_description or "").strip() or "(없음)"

    sections = [
        CODE_ANALYZE_SYSTEM_PROMPT,
        "",
        f"언어: {language}",
        f"문맥: {context_text}",
        f"미션 제목: {mission_title_text}",
        f"미션 설명: {mission_desc_text}",
        _format_list_inline("요구사항", requirements),
        _format_list_inline("완료 기준", success_criteria),
        "[현재 코드 시작]",
        code if code.strip() else "(빈 코드)",
        "[현재 코드 끝]",
        "위 코드를 분석해 규약대로 JSON 객체 하나만 출력하라.",
    ]
    return "\n".join(sections)


JAVA_CODE_GRADING_SYSTEM_PROMPT = """\
너는 자바 코딩테스트 채점 전문가다. 제출된 자바 코드를 정적 분석하여 평가 기준에 따라 채점한다.

분석 항목:
- 정수형 변수 선언 (int, long 등)
- 조건문 사용 (if, else if, else)
- 반복문 사용 (for, while)
- 들여쓰기 및 코드 컨벤션 준수
- 문법적 정확성

규칙:
- 3~15줄 내외의 짧은 자바 코드를 분석한다.
- 평가 기준(criteria)을 모두 충족하면 만점, 부분 충족 시 비례 점수, 미충족 시 0점 부여한다.
- 들여쓰기가 잘못되거나 컨벤션을 위반하면 감점한다.
- 문법 오류가 있으면 0점 처리한다.
- 코드를 자바 표준 컨벤션에 맞게 포맷팅하여 제공한다.

출력: 아래 4개 key만 가진 JSON 객체 하나만 출력한다. JSON 밖 텍스트·코드펜스 금지.
- is_correct(불리언): 모든 평가 기준을 충족하는지 여부
- score(정수): 0~100 사이의 점수
- feedback(문자열): 채점 결과 피드백 및 개선 제안
- formatted_code(문자열): 자바 표준 컨벤션에 맞게 포맷팅된 코드

값은 한국어로 작성한다."""


def build_java_code_grading_prompt(
    *,
    code: str,
    criteria: list[str],
) -> str:
    """자바 코드 채점용 프롬프트를 조립한다.
    
    코드 구간은 코드펜스 없이 평문 구분자로 감싼다.
    """
    
    criteria_text = "\n".join(f"- {c}" for c in criteria if c and c.strip()) if criteria else "(없음)"
    
    sections = [
        JAVA_CODE_GRADING_SYSTEM_PROMPT,
        "",
        "평가 기준:",
        criteria_text,
        "",
        "[제출 코드 시작]",
        code if code.strip() else "(빈 코드)",
        "[제출 코드 끝]",
        "",
        "위 코드를 분석해 규약대로 JSON 객체 하나만 출력하라.",
    ]
    return "\n".join(sections)
