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
    "JAVA_CRITERIA_SYSTEM_PROMPT",
    "build_java_criteria_prompt",
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
너는 자바 입문 코딩테스트 채점 전문가다. 제출된 3~15줄 내외의 자바 코드를 실행하지 않고 읽어서
평가 기준 각각의 충족 여부를 판정한다.

규칙:
- 평가 기준에 적힌 요구사항만 판정한다. 기준에 없는 요소(반복문, 들여쓰기, 변수명 스타일 등)로 감점하지 않는다.
- 코드에서 직접 확인되는 경우에만 passed=true 로 한다. 확실하지 않으면 false 로 한다.
- 주석 안의 코드는 구현으로 인정하지 않는다.
- 값·조건·출력 문자열이 기준과 다르면 false 로 한다.
- 컴파일되지 않는 문법 오류가 있으면 관련 기준은 false 로 한다.
- formatted_code 는 제출 코드를 자바 표준 들여쓰기로만 정리한다. 로직을 고치거나 추가하지 않는다.

출력: 아래 5개 key만 가진 JSON 객체 하나만 출력한다. JSON 밖 텍스트·코드펜스 금지.
- criteria_results(배열): 평가 기준 번호 순서대로 {"index": 번호, "passed": true 또는 false, "reason": "근거 한 문장"}
- is_correct(불리언): 모든 기준이 passed 이면 true
- score(정수): 0~100, 충족한 기준 비율 × 100
- feedback(문자열): 충족한 점과 부족한 점을 구체적으로 1~3문장
- formatted_code(문자열): 들여쓰기만 정리한 제출 코드

값은 한국어로 작성한다."""


def build_java_code_grading_prompt(
    *,
    code: str,
    criteria: list[str],
) -> str:
    """자바 코드 채점용 프롬프트를 조립한다.

    코드 구간은 코드펜스 없이 평문 구분자로 감싼다.
    """

    cleaned = [c.strip() for c in criteria if c and c.strip()] if criteria else []
    criteria_text = (
        "\n".join(f"{index}. {c}" for index, c in enumerate(cleaned, start=1)) if cleaned else "(없음)"
    )

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


JAVA_CRITERIA_SYSTEM_PROMPT = """\
너는 자바 입문 코딩테스트 문제에서 채점용 평가 기준(criteria)을 만드는 도우미다.
대상은 3~15줄의 짧은 자바 코드다(변수 저장, 산술 연산, if/else, for/while, System.out.println).

규칙:
- 문제 문장에 명시된 요구사항만 기준으로 만든다.
- 문제에 없는 요소(반복문, 조건문, else, 변수형, 배열, 입력, 메서드, 출력 형식 등)를 임의로 추가하지 않는다.
- 각 기준은 제출 코드를 읽고 참/거짓을 판단할 수 있도록 변수명·값·조건·출력 문자열을 구체적으로 쓴다.
- "올바르게 구현했는가", "코드가 적절한가", "잘 작성했는가" 같은 추상적인 기준은 금지한다.
- 같은 내용의 기준을 두 번 쓰지 않는다.
- 기준은 2~5개만 만든다. 단순한 문제는 2~3개로 충분하다.
- 각 기준은 "~했는가" 형태의 한 문장으로 쓴다.

출력: criteria 하나의 key만 가진 JSON 객체 하나만 출력한다. JSON 밖 텍스트·코드펜스 금지.
- criteria(문자열 배열): 평가 기준 문장 2~5개

출력 예시 1:
문제: 정수형 변수 score에 65를 저장하고, if문으로 60보다 큰지 비교한 뒤, 조건이 참이면 '합격'을 출력하세요.
{"criteria":["정수형 변수 score에 65를 저장했는가","if문으로 score가 60보다 큰지 비교했는가","조건이 참이면 합격을 출력했는가"]}

출력 예시 2:
문제: for문을 사용해 1부터 5까지 출력하세요.
{"criteria":["for문을 사용해 반복했는가","1부터 5까지의 값을 출력했는가"]}"""


def build_java_criteria_prompt(*, question: str) -> str:
    """question 텍스트에서 평가 기준을 생성하는 프롬프트를 조립한다."""

    sections = [
        JAVA_CRITERIA_SYSTEM_PROMPT,
        "",
        "[문제 시작]",
        question.strip() or "(빈 문제)",
        "[문제 끝]",
        "",
        "위 문제의 평가 기준을 규약대로 JSON 객체 하나만 출력하라.",
    ]
    return "\n".join(sections)
