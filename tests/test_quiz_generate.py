"""POST /ai/quiz/generate: 다중 유형(quiz_types) 생성 + domain 초점 검색 테스트."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.routes import quiz as quiz_route
from app.main import app
from app.schemas.quiz import QuizGenerateRequest
from app.schemas.rag import RetrievedReference
from app.services.focused_retriever import FocusedRetriever, extract_focus_terms
from app.services.quiz_service import QuizService

_USER_PAYLOAD = {
    "user_id": "usr_test02",
    "category": "Infrastructure",
    "domain": "AWS EC2 Docker ECR GitHub Actions CI/CD",
    "quiz_types": ["MULTIPLE_CHOICE", "BLANK", "DESCRIPTIVE"],
    "count_per_type": 1,
}
_RELEVANT_CATEGORIES = {"aws", "docker", "cicd"}


def _ref(ref_id: str, category: str, topic: str, title: str, body: str, score: float = 0.0) -> RetrievedReference:
    return RetrievedReference(
        id=ref_id,
        title=title,
        content=f"[{category} > {title}]\n{body}",
        score=score,
        sourceType="bulk_markdown",
        metadata={"category": category, "topic": topic, "source_file": f"{category}/{topic}.md"},
    )


_CORPUS: dict[str, tuple[RetrievedReference, tuple[str, ...]]] = {
    "net1": (_ref("net1", "network", "tcp", "TCP 3-Way Handshake - 개념", "TCP는 연결 지향 프로토콜이며 `SYN` 패킷으로 연결을 시작한다. 서버는 SYN-ACK로 응답한다."), ()),
    "net2": (_ref("net2", "network", "http", "HTTP - 상태 코드", "HTTP 상태 코드는 요청 처리 결과를 나타낸다. 5xx는 서버 측 오류를 의미한다."), ()),
    "spring1": (_ref("spring1", "spring", "transaction", "Spring 트랜잭션 - 동시성", "낙관적 락은 `@Version` 컬럼으로 충돌을 감지한다. 비관적 락은 DB 락을 사용한다."), ("spring", "jpa", "transaction")),
    "spring2": (_ref("spring2", "spring", "jpa", "Spring JPA - 영속성 컨텍스트", "영속성 컨텍스트는 엔티티를 1차 캐시에 보관한다. 변경 감지로 `flush` 시점에 UPDATE가 실행된다."), ("spring", "jpa")),
    "aws_ec2": (_ref("aws_ec2", "aws", "aws-ec2", "AWS EC2 - 확인 방법", "EC2에 SSH로 접속할 때는 `22`번 포트를 Security Group 인바운드에서 허용해야 한다. 외부 접속이 안 되면 Security Group과 퍼블릭 IP를 먼저 확인한다."), ("aws", "ec2")),
    "aws_ecr": (_ref("aws_ecr", "aws", "aws-container-deployment", "AWS 컨테이너 배포 - ECR", "ECR은 AWS의 프라이빗 컨테이너 레지스트리이다. 이미지를 가져오려면 `aws ecr get-login-password`로 인증해야 한다."), ("aws", "ecr", "docker")),
    "docker1": (_ref("docker1", "docker", "docker-troubleshooting", "Docker 장애 대응 - Exited", "컨테이너가 Exited 상태이면 `docker logs`로 종료 원인을 확인한다. 내부 확인은 docker exec로 수행한다."), ("docker",)),
    "docker2": (_ref("docker2", "docker", "docker-networking", "Docker 네트워크 - localhost", "컨테이너 안의 localhost는 컨테이너 자기 자신을 의미한다. 다른 컨테이너는 `서비스 이름`으로 접근해야 한다."), ("docker",)),
    "cicd1": (_ref("cicd1", "cicd", "cicd-github-actions", "GitHub Actions - workflow", "GitHub Actions의 자동화 단위는 `workflow`이며 YAML 파일로 정의한다. job은 runner에서 실행되는 step의 묶음이다."), ("github", "actions", "ci/cd")),
    "cicd2": (_ref("cicd2", "cicd", "cicd-docker-ecr", "CI/CD - ECR 푸시", "CI 파이프라인은 테스트 후 이미지를 빌드해 `docker push`로 ECR에 저장한다. 배포 단계는 저장된 이미지 태그를 사용한다."), ("ecr", "ci/cd", "docker")),
}

# 넓은 카테고리 질의(예: "Infrastructure ...")에서 Network/Spring 청크가 상위에 오는 실제 증상을 재현한다.
_BROAD_QUERY_RANKING = [("net1", 0.71), ("spring1", 0.70), ("net2", 0.69), ("aws_ec2", 0.68), ("spring2", 0.67), ("docker1", 0.60)]


class FakeRetriever:
    def __init__(self) -> None:
        self.queries: list[tuple[str, int | None]] = []

    def retrieve(self, query: str, top_k: int | None = None) -> list[RetrievedReference]:
        self.queries.append((query, top_k))
        k = top_k or 3
        q = query.lower()
        term_hits = [
            (ref_id, 0.66)
            for ref_id, (_, terms) in _CORPUS.items()
            if any(t in q.split() or (t in q and "/" in t) for t in terms)
        ]
        if len(q.split()) > 3 or not term_hits:
            ranking = _BROAD_QUERY_RANKING
        else:
            ranking = term_hits
        return [_CORPUS[ref_id][0].model_copy(update={"score": score}) for ref_id, score in ranking[:k]]


class FakeLLM:
    def __init__(self, responses: list[object]) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []

    def generate_text(self, prompt: str, system_prompt: str | None = None, timeout_seconds: int = 45) -> str:
        self.prompts.append(prompt)
        resp = self._responses.pop(0) if self._responses else RuntimeError("LLM unavailable")
        if isinstance(resp, Exception):
            raise resp
        return resp if isinstance(resp, str) else json.dumps(resp, ensure_ascii=False)


def _service(llm_responses: list[object]) -> tuple[QuizService, FakeRetriever, FakeLLM]:
    svc = QuizService.__new__(QuizService)
    retriever, llm = FakeRetriever(), FakeLLM(llm_responses)
    svc.retriever = retriever
    svc.llm = llm
    return svc, retriever, llm


_MC = {
    "type": "MULTIPLE_CHOICE",
    "question": "EC2에 SSH로 접속할 때 Security Group 인바운드에서 허용해야 하는 포트는?",
    "options": ["1. 22", "2. 80", "3. 443", "4. 8080"],
    "answer": "1",
    "explanation": "SSH는 22번 포트를 사용한다.",
}
_BLANK = {
    "type": "BLANK",
    "question": "종료된 컨테이너의 원인을 확인할 때 사용하는 명령어는 ___ 이다.",
    "options": None,
    "answer": "docker logs",
    "explanation": "docker logs는 컨테이너 표준 출력 로그를 보여준다.",
}
_DESC = {
    "type": "DESCRIPTIVE",
    "question": "GitHub Actions로 Docker 이미지를 ECR에 푸시하고 EC2에 배포하는 과정을 설명하시오.",
    "answer": "테스트 후 이미지를 빌드하고 ECR에 푸시한 뒤 EC2에서 pull 후 컨테이너를 교체하고 헬스 체크로 검증한다.",
    "explanation": "CI와 CD 단계를 구분해 설명해야 한다.",
    "gradingKeywords": ["docker build", "ECR push", "health check"],
}


def _types(quizzes: list) -> list[str]:
    return [q.type for q in quizzes]


# --- request schema -----------------------------------------------------------


def test_request_accepts_quiz_types_and_builds_plan() -> None:
    req = QuizGenerateRequest(**_USER_PAYLOAD)
    assert req.user_id == "usr_test02"
    assert req.quiz_types == ["multiple_choice", "blank", "descriptive"]
    assert req.type_plan() == [("multiple_choice", 1), ("blank", 1), ("descriptive", 1)]
    assert req.total_count == 3


def test_request_accepts_camel_case_and_aliases() -> None:
    req = QuizGenerateRequest(category="Backend", quizTypes=["객관식", "fill-in-the-blank", "서술형"], countPerType=2)
    assert req.type_plan() == [("multiple_choice", 2), ("blank", 2), ("descriptive", 2)]


def test_request_rejects_unknown_quiz_type() -> None:
    with pytest.raises(ValidationError):
        QuizGenerateRequest(category="Backend", quiz_types=["MULTIPLE_CHOICE", "ESSAY_LONG"])


def test_legacy_request_plan_unchanged() -> None:
    req = QuizGenerateRequest(category="Database", count=3, type="객관식")
    assert req.quiz_types is None
    assert req.type_plan() == [("multiple_choice", 3)]


# --- generation: type fidelity & per-type counts --------------------------------


def test_three_types_generate_one_each() -> None:
    svc, _, _ = _service([{"quizzes": [_MC, _BLANK, _DESC]}])
    result = svc.generate_quizzes(QuizGenerateRequest(**_USER_PAYLOAD))

    assert result.totalCount == 3
    assert _types(result.quizzes) == ["multiple_choice", "blank", "descriptive"]
    assert [q.id for q in result.quizzes] == [1, 2, 3]
    assert result.type == "mixed"
    assert result.quizTypes == ["multiple_choice", "blank", "descriptive"]
    blank, desc = result.quizzes[1], result.quizzes[2]
    assert blank.options is None and blank.answer == "docker logs"
    assert desc.options is None and desc.gradingKeywords == ["docker build", "ECR push", "health check"]


def test_extra_items_are_capped_per_type() -> None:
    mc2 = {**_MC, "question": "HTTPS 기본 포트는?"}
    mc3 = {**_MC, "question": "HTTP 기본 포트는?"}
    svc, _, _ = _service([{"quizzes": [_MC, mc2, mc3, _BLANK, _DESC]}])
    result = svc.generate_quizzes(QuizGenerateRequest(**_USER_PAYLOAD))
    assert _types(result.quizzes) == ["multiple_choice", "blank", "descriptive"]


def test_missing_types_are_requested_again() -> None:
    mc2 = {**_MC, "question": "HTTPS 기본 포트는?"}
    svc, _, llm = _service([{"quizzes": [_MC, mc2]}, {"quizzes": [_BLANK, _DESC]}])
    result = svc.generate_quizzes(QuizGenerateRequest(**_USER_PAYLOAD))

    assert _types(result.quizzes) == ["multiple_choice", "blank", "descriptive"]
    assert len(llm.prompts) == 2
    assert "blank: 1개" in llm.prompts[1] and "descriptive: 1개" in llm.prompts[1]


def test_type_inferred_when_llm_omits_type() -> None:
    items = [{k: v for k, v in item.items() if k != "type"} for item in (_MC, _BLANK, _DESC)]
    svc, _, _ = _service([{"quizzes": items}])
    result = svc.generate_quizzes(QuizGenerateRequest(**_USER_PAYLOAD))
    assert _types(result.quizzes) == ["multiple_choice", "blank", "descriptive"]


def test_llm_failure_uses_type_aware_domain_backups() -> None:
    svc, _, _ = _service([RuntimeError("down")] * 3)
    result = svc.generate_quizzes(QuizGenerateRequest(**_USER_PAYLOAD))

    assert result.totalCount == 3
    assert _types(result.quizzes) == ["multiple_choice", "blank", "descriptive"]
    for q in result.quizzes:
        assert "TCP" not in q.question and "동시성" not in q.question
    mc, blank, desc = result.quizzes
    assert mc.options is not None and len(mc.options) == 4 and mc.answer in {"1", "2", "3", "4"}
    assert "___" in blank.question and blank.answer
    assert desc.answer and desc.gradingKeywords


def test_invalid_items_are_replaced_not_retyped() -> None:
    bad_blank = {**_BLANK, "question": "빈칸 표시가 없는 문장"}
    bad_mc = {**_MC, "options": None}
    svc, _, _ = _service([{"quizzes": [bad_mc, bad_blank, _DESC]}, RuntimeError("down")])
    result = svc.generate_quizzes(QuizGenerateRequest(**_USER_PAYLOAD))
    assert _types(result.quizzes) == ["multiple_choice", "blank", "descriptive"]
    assert result.quizzes[0].options is not None
    assert "___" in result.quizzes[1].question


# --- retrieval ----------------------------------------------------------------


def test_extract_focus_terms() -> None:
    assert extract_focus_terms("AWS EC2 Docker ECR GitHub Actions CI/CD") == [
        "AWS", "EC2", "Docker", "ECR", "GitHub", "Actions", "CI/CD",
    ]
    assert extract_focus_terms("Spring Data JPA, Transaction", ["jpa", "N+1"]) == [
        "Spring Data JPA", "Transaction", "jpa", "N+1",
    ]
    assert extract_focus_terms(None, None) == []


def test_infrastructure_domain_prioritizes_aws_docker_cicd_context() -> None:
    svc, retriever, _ = _service([{"quizzes": [_MC, _BLANK, _DESC]}])
    result = svc.generate_quizzes(QuizGenerateRequest(**_USER_PAYLOAD))

    categories = [src.split("/")[0] for src in result.retrievedSources]
    assert result.retrievedKnowledgeCount == len(result.retrievedSources) > 0
    assert set(categories[:3]) <= _RELEVANT_CATEGORIES
    assert sum(c in _RELEVANT_CATEGORIES for c in categories) > len(categories) / 2
    assert _RELEVANT_CATEGORIES <= set(categories)
    assert "network" not in categories[:4] and "spring" not in categories[:4]
    assert any("Docker" in q for q, _ in retriever.queries)


def test_focused_retrieval_generalizes_to_other_domains() -> None:
    ranked = FocusedRetriever(FakeRetriever()).retrieve(
        "Backend Spring JPA Transaction 영속성", ["Spring", "JPA"], top_k=3, context_hint="Backend"
    )
    assert [r.metadata["category"] for r in ranked[:2]] == ["spring", "spring"]


def test_focused_retrieval_limits_chunks_per_source() -> None:
    dup = [_CORPUS["docker1"][0].model_copy(update={"id": f"d{i}", "score": 0.9 - i * 0.01}) for i in range(4)]

    class OneSource:
        def retrieve(self, query: str, top_k: int | None = None) -> list[RetrievedReference]:
            return dup + [_CORPUS["aws_ec2"][0].model_copy(update={"score": 0.5})]

    ranked = FocusedRetriever(OneSource(), max_per_source=2).retrieve("q", ["Docker"], top_k=3)
    assert [r.id for r in ranked] == ["d0", "d1", "aws_ec2"]


def test_legacy_request_keeps_single_query_and_category_templates() -> None:
    svc, retriever, llm = _service([RuntimeError("down")] * 3)
    result = svc.generate_quizzes(QuizGenerateRequest(category="Database", count=3, type="multiple_choice"))

    assert retriever.queries == [("Database  실무 핵심 CS 이론 개념 면접", 5)]
    assert "- 문제 유형: multiple_choice" in llm.prompts[0]
    assert result.type == "multiple_choice"
    assert result.totalCount == 3
    assert set(_types(result.quizzes)) == {"multiple_choice"}
    assert "카디널리티" in result.quizzes[0].question


@pytest.mark.parametrize("category", ["Spring", "Network", "OS"])
def test_legacy_categories_still_generate_requested_count(category: str) -> None:
    item = {**_MC, "type": "multiple_choice"}
    svc, _, _ = _service([{"quizzes": [item]}, RuntimeError("down")])
    result = svc.generate_quizzes(QuizGenerateRequest(category=category, count=2))
    assert result.totalCount == 2
    assert set(_types(result.quizzes)) == {"multiple_choice"}
    assert result.quizzes[0].question == _MC["question"]


# --- API contract -------------------------------------------------------------


def test_generate_endpoint_returns_one_quiz_per_requested_type(monkeypatch: pytest.MonkeyPatch) -> None:
    svc, _, _ = _service([{"quizzes": [_MC, _BLANK, _DESC]}])
    monkeypatch.setattr(quiz_route, "QuizService", lambda: svc)

    resp = TestClient(app).post("/ai/quiz/generate", json=_USER_PAYLOAD)

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["totalCount"] == 3
    assert [q["type"] for q in data["quizzes"]] == ["multiple_choice", "blank", "descriptive"]
    assert data["quizTypes"] == ["multiple_choice", "blank", "descriptive"]
    assert data["domain"] == _USER_PAYLOAD["domain"]


def test_generate_endpoint_rejects_unknown_quiz_type() -> None:
    resp = TestClient(app).post("/ai/quiz/generate", json={**_USER_PAYLOAD, "quiz_types": ["ESSAY_LONG"]})
    assert resp.status_code == 422
