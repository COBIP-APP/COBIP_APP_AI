"""Mission feedback / quiz grade FE payload → server schema 정규화."""

from __future__ import annotations

import re
from typing import Any

from app.models.enums import QuestionType
from app.schemas.quiz import normalize_quiz_type

__all__ = [
    "normalize_mission_feedback_payload",
    "normalize_quiz_grade_payload",
    "normalize_api_spec_list",
    "normalize_submitted_code_list",
]


def _coerce_str(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_api_spec_field(item: dict[str, Any]) -> dict[str, Any]:
    out = dict(item)
    name = _coerce_str(item.get("name") or item.get("fieldName") or item.get("header"))
    if name:
        out["name"] = name
    for alias in ("fieldName", "header"):
        out.pop(alias, None)
    if "required" not in out:
        out["required"] = True
    if "type" not in out or not _coerce_str(out.get("type")):
        out["type"] = "string"
    if "description" not in out:
        out["description"] = ""
    return out


def _normalize_api_spec_header(item: dict[str, Any]) -> dict[str, Any]:
    out = dict(item)
    name = _coerce_str(item.get("name") or item.get("header"))
    if name:
        out["name"] = name
    out.pop("header", None)
    if "required" not in out:
        out["required"] = True
    if "description" not in out:
        out["description"] = ""
    return out


def _normalize_api_spec_error(item: dict[str, Any]) -> dict[str, Any]:
    out = dict(item)
    status = item.get("status")
    if status is None:
        status = item.get("statusCode")
    if status is not None:
        try:
            out["status"] = int(status)
        except (TypeError, ValueError):
            out["status"] = 400
    code = item.get("code")
    if code is None:
        code = item.get("errorCode")
    if code is not None:
        out["code"] = _coerce_str(code)
    if "message" not in out or not _coerce_str(out.get("message")):
        out["message"] = _coerce_str(out.get("message") or "error")
    out.pop("statusCode", None)
    out.pop("errorCode", None)
    return out


def _normalize_api_spec_item(item: dict[str, Any]) -> dict[str, Any]:
    out = dict(item)
    if not _coerce_str(out.get("method")):
        out["method"] = "GET"
    if not _coerce_str(out.get("endpoint")):
        out["endpoint"] = "/"
    if not _coerce_str(out.get("apiName")):
        out["apiName"] = out["endpoint"]
    if not _coerce_str(out.get("description")):
        out["description"] = out["apiName"]
    if out.get("requestBody") is None:
        out["requestBody"] = {}
    if out.get("responseBody") is None:
        out["responseBody"] = {}
    if out.get("status") is None:
        out["status"] = 200

    headers_raw = out.get("requestHeaders")
    if isinstance(headers_raw, dict):
        out["requestHeaders"] = [
            {
                "name": _coerce_str(k),
                "value": _coerce_str(v) if v is not None else None,
                "required": True,
                "description": "",
            }
            for k, v in headers_raw.items()
            if _coerce_str(k)
        ]
    elif isinstance(headers_raw, list):
        out["requestHeaders"] = [
            _normalize_api_spec_header(x) for x in headers_raw if isinstance(x, dict)
        ]
    else:
        out["requestHeaders"] = []

    for field_key in ("requestFields", "responseFields"):
        raw_fields = out.get(field_key)
        if isinstance(raw_fields, list):
            out[field_key] = [
                _normalize_api_spec_field(x) for x in raw_fields if isinstance(x, dict)
            ]
        else:
            out[field_key] = []

    errors_raw = out.get("errorResponses")
    if isinstance(errors_raw, list):
        out["errorResponses"] = [
            _normalize_api_spec_error(x) for x in errors_raw if isinstance(x, dict)
        ]
    else:
        out["errorResponses"] = []

    return out


def normalize_api_spec_list(specs: object) -> list[dict[str, Any]]:
    if not isinstance(specs, list):
        return []
    return [_normalize_api_spec_item(x) for x in specs if isinstance(x, dict)]


def normalize_submitted_code_list(files: object) -> list[dict[str, Any]]:
    if not isinstance(files, list):
        return []
    out: list[dict[str, Any]] = []
    for raw in files:
        if not isinstance(raw, dict):
            continue
        file_name = _coerce_str(raw.get("fileName") or raw.get("filePath") or "unknown.java")
        file_path = raw.get("filePath")
        if file_path is not None:
            file_path = _coerce_str(file_path) or None
        content = raw.get("content", "")
        if content is None:
            content = ""
        if not isinstance(content, str):
            content = str(content)
        language = _coerce_str(raw.get("language") or "java") or "java"
        if content.strip():
            out.append(
                {
                    "fileName": file_name,
                    "filePath": file_path,
                    "language": language,
                    "content": content,
                }
            )
    return out


def normalize_mission_feedback_payload(data: object) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    out = dict(data)
    api_specs = out.pop("apiSpec", None)
    if api_specs is None:
        api_specs = out.get("apiSpecs")
    out["apiSpecs"] = normalize_api_spec_list(api_specs)
    out["submittedCode"] = normalize_submitted_code_list(out.get("submittedCode"))
    return out


_QUESTION_TYPE_VALUES = frozenset(t.value for t in QuestionType)
_GENERATED_TYPE_TO_GRADE_TYPE = {"blank": QuestionType.FILL_BLANK.value}
_DIFFICULTY_ALIASES: dict[str, str] = {
    "초급": "beginner",
    "beginner": "beginner",
    "easy": "beginner",
    "중급": "intermediate",
    "intermediate": "intermediate",
    "medium": "intermediate",
    "고급": "advanced",
    "advanced": "advanced",
    "hard": "advanced",
}


def _normalize_grade_question_type(value: object) -> object:
    """생성 응답 유형(blank, 서술형, MULTIPLE_CHOICE 등)을 QuestionType 값으로 맞춘다."""
    if not isinstance(value, str):
        return value
    key = value.strip().lower().replace("-", "_").replace(" ", "_")
    if key in _QUESTION_TYPE_VALUES:
        return key
    generated = normalize_quiz_type(value)
    if generated is None:
        return value
    return _GENERATED_TYPE_TO_GRADE_TYPE.get(generated, generated)


def _normalize_difficulty(value: object) -> str | None:
    key = _coerce_str(value).lower()
    return _DIFFICULTY_ALIASES.get(key)


def _normalize_grading_keywords(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None
    keywords = [_coerce_str(v) for v in value if _coerce_str(v)]
    return keywords or None


def _normalize_generated_question_fields(q: dict[str, Any], out: dict[str, Any]) -> None:
    """/ai/quiz/generate 의 QuizQuestionItem 형식을 QuestionSchema 형식으로 보정한다."""
    if _coerce_str(q.get("questionId")):
        q["questionId"] = _coerce_str(q.get("questionId"))
    elif _coerce_str(q.get("id")):
        q["questionId"] = _coerce_str(q.get("id"))
    else:
        q["questionId"] = "Q-1"
    q.pop("id", None)

    if "type" in q:
        q["type"] = _normalize_grade_question_type(q.get("type"))

    options = q.pop("options", None)
    if q.get("choices") is None and isinstance(options, list):
        q["choices"] = options

    if q.get("explanation") is None:
        q["explanation"] = ""

    difficulty = _normalize_difficulty(q.get("difficulty"))
    if difficulty is None:
        difficulty = _normalize_difficulty(out.get("difficulty")) or "intermediate"
    q["difficulty"] = difficulty

    question_keywords = q.pop("gradingKeywords", None)
    keywords = _normalize_grading_keywords(out.get("gradingKeywords"))
    if keywords is None:
        keywords = _normalize_grading_keywords(question_keywords)
    out["gradingKeywords"] = keywords


def normalize_quiz_grade_payload(data: object) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    out = dict(data)
    question = out.get("question")
    if isinstance(question, dict):
        q = dict(question)
        answer = q.get("answer")
        if answer is None:
            answer = q.get("correctAnswer")
        if answer is not None:
            q["answer"] = _coerce_str(answer)
        q.pop("correctAnswer", None)
        user = out.get("userAnswer")
        if user is None:
            user = out.get("answer")
        if user is not None:
            out["userAnswer"] = _coerce_str(user)
        out.pop("answer", None)
        _normalize_generated_question_fields(q, out)
        out["question"] = q

    if not _coerce_str(out.get("featureName")):
        out["featureName"] = _coerce_str(out.get("category"))

    related = out.get("relatedApiSpecs")
    if related is not None:
        out["relatedApiSpecs"] = normalize_api_spec_list(related)
    return out


_ANSWER_SYNONYMS: dict[str, str] = {
    "해시": "hash",
    "해싱": "hash",
    "암호화": "encrypt",
    "토큰": "token",
    "인증": "auth",
    "로그인": "login",
    "회원가입": "signup",
    "가입": "signup",
    "생성": "create",
    "조회": "read",
    "수정": "update",
    "삭제": "delete",
}


def normalize_answer_text(text: str) -> str:
    """채점용 답안 정규화 — 대소문자·공백·문장부호·한영 동의어 차이 완화."""

    s = (text or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^\w\s가-힣]", "", s)
    for src, dst in _ANSWER_SYNONYMS.items():
        s = s.replace(src, dst)
    return s.strip()


def extract_answer_keywords(text: str) -> set[str]:
    normalized = normalize_answer_text(text)
    if not normalized:
        return set()
    tokens = re.findall(r"[a-z0-9가-힣]{2,}", normalized)
    stop = frozenset({"the", "and", "for", "with", "입니다", "한다", "하는", "있는"})
    return {t for t in tokens if t not in stop and len(t) >= 2}
