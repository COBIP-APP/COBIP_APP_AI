"""Qdrant 지식 기반 퀴즈 자동 생성 및 심층 오답 해설 Service."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.schemas.quiz import (
    QuizExplainRequest,
    QuizExplainResponseData,
    QuizGenerateRequest,
    QuizGenerateResponseData,
    QuizQuestionItem,
)
from app.schemas.rag import RetrievedReference
from app.services.llm_service import LLMService
from app.services.retriever_service import RetrieverService

__all__ = ["QuizService"]

logger = logging.getLogger(__name__)


class QuizService:
    """Qdrant 벡터 검색과 Ollama LLM을 결합한 퀴즈 생성 & 해설 서비스."""

    def __init__(self) -> None:
        self.retriever = RetrieverService()
        self.llm = LLMService()

    # =========================================================================
    # 1. 퀴즈 자동 생성 (Quiz Generation)
    # =========================================================================
    def generate_quizzes(self, request: QuizGenerateRequest) -> QuizGenerateResponseData:
        """Qdrant에서 실무 지식을 검색하여 Context에 주입 후 퀴즈를 생성한다."""
        keyword_str = " ".join(request.keywords or [])
        query = f"{request.category} {keyword_str} 실무 핵심 CS 이론 개념 면접".strip()

        # 1) Qdrant 지식 청크 검색 (Top 3~5)
        references: list[RetrievedReference] = []
        try:
            references = self.retriever.retrieve(query, top_k=5)
            logger.info(
                "Quiz retrieval success: category=%s, hits=%d",
                request.category,
                len(references),
            )
        except Exception as exc:
            logger.warning("Quiz retrieval error (fallback to general context): %s", exc)

        context_text = self._build_context_text(references)

        # 2) 프롬프트 구성
        system_prompt = self._build_quiz_generate_system_prompt(request)
        user_prompt = (
            f"[요청 사양]\n"
            f"- 카테고리: {request.category}\n"
            f"- 난이도: {request.difficulty}\n"
            f"- 문제 유형: {request.type}\n"
            f"- 생성할 문제 수: {request.count}개\n"
            f"- 키워드: {keyword_str if keyword_str else '전반적 핵심 개념'}\n\n"
            f"[참고 지식 (Context)]\n"
            f"{context_text}\n\n"
            f"위 [참고 지식]의 기술적 내용과 실무 트러블슈팅 포인트를 반영하여 {request.count}개의 실무형 퀴즈를 순수 JSON 포맷으로 생성하라."
        )

        # 3) LLM 호출 및 재시도 방어 로직 (최대 2회 재시도)
        quizzes = self._call_llm_with_retry_for_quizzes(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            request=request,
            references=references,
            max_retries=2,
        )

        return QuizGenerateResponseData(
            category=request.category,
            difficulty=request.difficulty,
            type=request.type,
            totalCount=len(quizzes),
            quizzes=quizzes,
            retrievedKnowledgeCount=len(references),
        )

    # =========================================================================
    # 2. 오답 분석 및 심층 해설 (Quiz Explanation)
    # =========================================================================
    def explain_quiz(self, request: QuizExplainRequest) -> QuizExplainResponseData:
        """Qdrant에서 관련 지식을 검색하여 3줄 요약 + 상세 해설 + 실무 트러블슈팅 팁을 제공한다."""
        user_ans_clean = str(request.user_answer).strip().lower()
        correct_ans_clean = str(request.correct_answer).strip().lower()
        is_correct = user_ans_clean == correct_ans_clean

        # 1) Qdrant 지식 청크 검색 (Top 4) — 사용자 추가 질문도 검색 쿼리에 포함
        search_query = " ".join(filter(None, [
            request.category or "",
            request.question,
            request.user_question or "",
            request.correct_answer,
        ])).strip()

        references: list[RetrievedReference] = []
        try:
            references = self.retriever.retrieve(search_query, top_k=4)
        except Exception as exc:
            logger.warning("Quiz explanation retrieval error: %s", exc)

        context_text = self._build_context_text(references)

        # 2) 강화된 시스템 프롬프트: 반드시 지식 청크를 직접 인용 강제
        system_prompt = (
            "너는 10년 이상 경력의 시니어 소프트웨어 엔지니어 기술 멘토다.\n"
            "아래의 엄격한 규칙을 반드시 따라야 한다:\n"
            "1. [참고 지식(Context)] 블록에 실린 구체적인 내용(키워드, 원칙, 규칙)을 직접 인용하여 답하라.\n"
            "2. 추상적이거나 모니터링 도구(Prometheus, Grafana 등) 언급 같은 일반론으로 얼버무리지 마라.\n"
            "3. [사용자 추가 질문]에 명시적으로 답하라 — 그 답도 [참고 지식(Context)]에서 근거를 끌어와야 한다.\n"
            "4. 마크다운 코드블록(```json) 또는 부가 인사말 없이 순수 JSON 객체 하나만 출력하라.\n\n"
            "[출력 JSON 스키마]\n"
            "{\n"
            '  "summary": [\n'
            '    "1. 핵심 개념: [참고 지식]에서 발췌한 핵심 원칙 1문장",\n'
            '    "2. 오답 원인: 제출 답안이 왜 틀렸는지 [참고 지식] 근거로 설명",\n'
            '    "3. 정답 원칙: [사용자 추가 질문]에 [참고 지식] 기반으로 직접 답변"\n'
            "  ],\n"
            '  "detailedExplanation": "[참고 지식]의 구체적 내용(예: 인덱스 컬럼 순서 원칙, 조건 타입별 배치 규칙 등)을 인용하며 내부 동작 원리와 정오답 이유를 상세히 설명",\n'
            '  "troubleshootingTip": "[참고 지식]에서 파악한 실무 적용 원칙(예: 동등 조건 선두 배치, 범위 조건 후두 배치)을 실제 DB 쿼리 튜닝 사례와 함께 제시"\n'
            "}"
        )

        user_prompt = (
            f"[문제 내용]: {request.question}\n"
            f"[사용자 제출 답안]: {request.user_answer}\n"
            f"[실제 정답]: {request.correct_answer}\n"
            f"[사용자 추가 질문 — 반드시 구체적으로 답할 것]: {request.user_question or '없음'}\n"
            f"[정답 일치 여부]: {'정답' if is_correct else '오답'}\n\n"
            f"[참고 지식 (Context) — 아래 내용을 직접 인용하여 해설을 작성하라]\n"
            f"{context_text}\n\n"
            f"위 [참고 지식]의 구체적인 내용(키워드, 원칙, 예시)을 직접 인용하며 "
            f"summary(정확히 3개 문자열 리스트), detailedExplanation, troubleshootingTip을 포함한 순수 JSON을 출력하라."
        )

        # 3) LLM 호출 및 재시도, LLM 실패 시 RAG 지식 합성 Fallback
        explanation_data = self._call_llm_with_retry_for_explanation(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            request=request,
            is_correct=is_correct,
            references=references,
            max_retries=2,
        )

        return QuizExplainResponseData(
            isCorrect=is_correct,
            summary=explanation_data.get("summary", []),
            detailedExplanation=explanation_data.get("detailedExplanation", ""),
            troubleshootingTip=explanation_data.get("troubleshootingTip", ""),
            references=references,
        )

    # =========================================================================
    # 내부 헬퍼 메서드
    # =========================================================================
    def _build_context_text(self, references: list[RetrievedReference]) -> str:
        if not references:
            return "(Qdrant에 사전 인덱싱된 관련 지식이 없습니다. 최신 실무 표준 CS 지식을 바탕으로 생성하십시오.)"

        chunks = []
        for i, ref in enumerate(references, 1):
            title = ref.title or f"참고문헌 {i}"
            content = ref.content.strip()
            chunks.append(f"[{i}] {title}\n{content}")
        return "\n\n".join(chunks)

    def _build_quiz_generate_system_prompt(self, request: QuizGenerateRequest) -> str:
        prompt = (
            "너는 대한민국 최고 수준의 소프트웨어 엔지니어링 / CS 기술 면접관이다.\n"
            "주어진 [참고 지식(Context)]을 바탕으로 실제 실무 면접과 코딩 테스트 필기시험에 출제될 법한 고품질 퀴즈를 생성하라.\n"
            "규칙:\n"
            "1. 단순 단어 암기가 아닌, 실무 트러블슈팅, 아키텍처 원리, 장애 방지 관점의 문제를 출제하라.\n"
            "2. 절대로 사족 텍스트나 마크다운 코드블록(```json 등)을 출력하지 마라.\n"
            "3. 오직 아래 JSON 구조 하나만 반환하라.\n\n"
            "[응답 JSON 포맷 예시]\n"
            "{\n"
            '  "quizzes": [\n'
            "    {\n"
            '      "id": 1,\n'
            '      "question": "Spring Boot 환경에서 @Transactional이 선언된 메서드가 내부(this) 메서드 호출 시 트랜잭션이 적용되지 않는 원인으로 가장 적절한 것은?",\n'
            '      "type": "multiple_choice",\n'
            '      "options": [\n'
            '        "1. Spring AOP는 프록시 기반으로 동작하므로 self-invocation 시 프록시 객체를 거치지 않기 때문",\n'
            '        "2. private 메서드가 아니더라도 리플렉션 호출이 차단되기 때문",\n'
            '        "3. 트랜잭션 매니저가 동일 스레드의 중복 호출을 거부하기 때문",\n'
            '        "4. CGLIB 프록시는 인터페이스 기반 프록시보다 우선순위가 낮기 때문"\n'
            "      ],\n"
            '      "answer": "1",\n'
            '      "explanation": "Spring의 선언적 트랜잭션은 AOP 프록시 객체를 통해 타깃 메서드를 감싸 실행합니다. 동일 인스턴스 내부에서 호출(self-invocation)할 경우 프록시를 통하지 않고 실제 타깃 인스턴스의 메서드가 직접 호출되므로 트랜잭션 어드바이스가 적용되지 않습니다.",\n'
            '      "references": ["Spring AOP 프록시 메커니즘", "@Transactional 주의점"]\n'
            "    }\n"
            "  ]\n"
            "}\n"
        )
        if request.type == "ox":
            prompt += (
                "\n[주의: OX 퀴즈인 경우]\n"
                "- options 필드는 반드시 ['1. O', '2. X'] 형식이어야 하며, answer는 '1' 또는 '2' (또는 'O', 'X')로 지정하라.\n"
            )
        elif request.type == "short_answer":
            prompt += (
                "\n[주의: 단답형 퀴즈인 경우]\n"
                "- options 필드는 null 또는 빈 리스트([])로 지정하고, answer는 실무 핵심 기술 키워드(예: 'MVCC', '인덱스')를 지정하라.\n"
            )
        return prompt

    def _call_llm_with_retry_for_quizzes(
        self,
        system_prompt: str,
        user_prompt: str,
        request: QuizGenerateRequest,
        references: list[RetrievedReference],
        max_retries: int = 2,
    ) -> list[QuizQuestionItem]:
        last_error = None
        current_user_prompt = user_prompt

        ref_titles = [ref.title for ref in references if ref.title]

        for attempt in range(max_retries + 1):
            try:
                raw_text = self.llm.generate_text(
                    prompt=current_user_prompt,
                    system_prompt=system_prompt,
                    timeout_seconds=45,
                )
                parsed_json = self._extract_json(raw_text)

                # 'quizzes' 키 또는 루트 리스트 처리
                items_data = parsed_json.get("quizzes", parsed_json) if isinstance(parsed_json, dict) else parsed_json
                if not isinstance(items_data, list):
                    raise ValueError(f"JSON 결과에 퀴즈 리스트가 없습니다: {parsed_json.keys() if isinstance(parsed_json, dict) else type(parsed_json)}")

                quizzes: list[QuizQuestionItem] = []
                for idx, item in enumerate(items_data, 1):
                    q_id = int(item.get("id", idx))
                    q_text = str(item.get("question", "")).strip()
                    if not q_text:
                        continue
                    q_type = str(item.get("type", request.type)).strip()
                    options = item.get("options")
                    if isinstance(options, list):
                        options = [str(opt) for opt in options]
                    elif q_type == "ox":
                        options = ["1. O", "2. X"]
                    else:
                        options = None

                    answer = str(item.get("answer", "1")).strip()
                    explanation = str(item.get("explanation", "실무 관점 핵심 해설입니다.")).strip()
                    q_refs = item.get("references")
                    if not isinstance(q_refs, list) or not q_refs:
                        q_refs = ref_titles[:2] if ref_titles else [request.category]

                    quizzes.append(
                        QuizQuestionItem(
                            id=q_id,
                            question=q_text,
                            type=q_type,
                            options=options,
                            answer=answer,
                            explanation=explanation,
                            references=q_refs,
                        )
                    )

                if quizzes:
                    logger.info("Successfully generated %d quizzes (attempt %d)", len(quizzes), attempt + 1)
                    return quizzes[: request.count]

                raise ValueError("파싱된 퀴즈 항목이 비어 있습니다.")

            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Quiz generation attempt %d failed: %s. Retrying...",
                    attempt + 1,
                    exc,
                )
                current_user_prompt = (
                    f"{user_prompt}\n\n"
                    f"[주의: 이전 응답이 JSON 파싱 또는 스키마 검증 오류({exc})를 발생시켰습니다. "
                    f"반드시 마크다운 없이 순수 JSON 포맷 {{'quizzes': [...]}} 으로만 엄격히 다시 출력하세요.]"
                )

        logger.error("All %d attempts failed. Falling back to structured backup quizzes: %s", max_retries + 1, last_error)
        return self._generate_fallback_quizzes(request, ref_titles)

    def _call_llm_with_retry_for_explanation(
        self,
        system_prompt: str,
        user_prompt: str,
        request: QuizExplainRequest,
        is_correct: bool,
        references: list[RetrievedReference] | None = None,
        max_retries: int = 2,
    ) -> dict[str, Any]:
        last_error = None
        current_user_prompt = user_prompt

        for attempt in range(max_retries + 1):
            try:
                raw_text = self.llm.generate_text(
                    prompt=current_user_prompt,
                    system_prompt=system_prompt,
                    timeout_seconds=40,
                )
                parsed = self._extract_json(raw_text)
                if not isinstance(parsed, dict):
                    raise ValueError(f"해설 결과가 dict 타입이 아닙니다: {type(parsed)}")

                summary = parsed.get("summary")
                if not isinstance(summary, list) or len(summary) < 2:
                    summary = [
                        f"1. 제출 답안: {request.user_answer} ({'정답입니다.' if is_correct else '오답입니다.'})",
                        f"2. 정답 기준: {request.correct_answer}이 기술적으로 타당합니다.",
                        "3. 실무 관점: 핵심 동작 메커니즘을 정확히 파악하는 것이 중요합니다.",
                    ]

                detailed = str(parsed.get("detailedExplanation", "")).strip()
                if not detailed:
                    detailed = f"{request.question}에 대한 정답은 '{request.correct_answer}'입니다."

                trouble = str(parsed.get("troubleshootingTip", "")).strip()
                if not trouble:
                    trouble = "실무 로그 분석 시 관련 예외 스택 트레이스를 확인하고 표준 매뉴얼을 준수하세요."

                return {
                    "summary": summary[:3],
                    "detailedExplanation": detailed,
                    "troubleshootingTip": trouble,
                }
            except Exception as exc:
                last_error = exc
                logger.warning("Quiz explanation attempt %d failed: %s", attempt + 1, exc)
                current_user_prompt = (
                    f"{user_prompt}\n\n"
                    f"[주의: 이전 응답이 JSON 파싱 오류({exc})를 일으켰습니다. "
                    f"반드시 마크다운 없이 순수 JSON {{'summary': [...], 'detailedExplanation': '...', 'troubleshootingTip': '...'}} 만 반환하세요.]"
                )

        # =====================================================================
        # LLM 완전 실패 시: Qdrant 검색 지식 청크를 직접 합성하는 RAG-Synthesis Fallback
        # 추상적인 기본 템플릿 대신 실제 검색된 지식 내용을 구조화하여 응답한다.
        # =====================================================================
        logger.error(
            "Explanation LLM failed after %d retries (%s). Activating RAG-synthesis fallback.",
            max_retries + 1,
            last_error,
        )
        return self._rag_synthesis_fallback(
            request=request,
            is_correct=is_correct,
            references=references or [],
        )

    def _rag_synthesis_fallback(
        self,
        request: QuizExplainRequest,
        is_correct: bool,
        references: list[RetrievedReference],
    ) -> dict[str, Any]:
        """Ollama 없이 Qdrant 검색 지식 청크를 직접 구조화하여 해설을 합성한다.

        추상적인 기본 템플릿 대신 references[0]의 실제 내용을 파싱하여
        구체적인 3줄 요약, 상세 해설, 실무 팁을 만든다.
        """
        result_label = "정답" if is_correct else "오답"
        user_q = request.user_question or ""

        # references에서 가장 관련성 높은 청크(score 기준 상위)의 핵심 내용 추출
        top_content = ""
        top_title = ""
        extra_contents: list[str] = []

        if references:
            # score 기준 정렬
            sorted_refs = sorted(
                references, key=lambda r: r.score or 0.0, reverse=True
            )
            top_ref = sorted_refs[0]
            top_content = (top_ref.content or "").strip()
            top_title = (top_ref.title or "").strip()
            for ref in sorted_refs[1:]:
                c = (ref.content or "").strip()
                if c:
                    extra_contents.append(c[:200])

        # 가장 유용한 문장들을 추출 (온점 또는 개행 단위)
        sentences = [s.strip() for s in re.split(r'(?<=[.。!?\n])', top_content) if len(s.strip()) > 20]
        key_sentences = sentences[:4] if sentences else [top_content[:300]] if top_content else []

        # 사용자 추가 질문에 대한 근거 문장 탐색
        user_q_answer = ""
        if user_q and top_content:
            # 추가 질문 키워드를 포함한 문장 우선 탐색
            user_q_keywords = [w for w in user_q.split() if len(w) >= 2]
            for sent in sentences:
                if any(kw in sent for kw in user_q_keywords):
                    user_q_answer = sent
                    break
        if not user_q_answer and key_sentences:
            user_q_answer = key_sentences[-1] if len(key_sentences) >= 2 else key_sentences[0]

        # 3줄 요약 합성
        summary_1 = (
            f"1. 핵심 개념 [{top_title}]: "
            + (key_sentences[0] if key_sentences else f"정답은 '{request.correct_answer}'입니다.")
        )
        summary_2 = (
            f"2. {result_label} 판별: 제출 답안 '{request.user_answer}'은 "
            + ("정답과 일치합니다." if is_correct else f"'{request.correct_answer}'이 정답입니다. "
               + (key_sentences[1] if len(key_sentences) > 1 else ""))
        )
        summary_3 = (
            f"3. 실무 원칙 [{user_q or '핵심 정리'}]: {user_q_answer}"
            if user_q_answer
            else f"3. 실무 원칙: {key_sentences[-1] if key_sentences else '해당 개념을 정확히 이해하고 적용하는 것이 중요합니다.'}"
        )

        # 상세 해설 합성
        full_context = "\n".join(key_sentences[:4]) if key_sentences else top_content[:500]
        detailed = (
            f"[검색된 실무 지식 기반 해설]\n\n"
            f"문제: {request.question}\n"
            f"정답: {request.correct_answer}\n\n"
            f"{full_context}"
        )
        if extra_contents:
            detailed += "\n\n추가 참고 내용:\n" + extra_contents[0]

        # 트러블슈팅 팁 합성 (사용자 추가 질문 기반)
        if user_q and user_q_answer:
            tip = (
                f"[추가 질문: '{user_q}'] → {user_q_answer}\n\n"
                + (key_sentences[2] if len(key_sentences) > 2 else "")
            )
        elif key_sentences:
            tip_sentences = key_sentences[2:4] or key_sentences[-1:]
            tip = "실무 적용 원칙:\n" + "\n".join(tip_sentences)
        else:
            tip = f"'{request.correct_answer}' 개념을 정확히 이해하고 실무 코드에 올바르게 적용하세요."

        return {
            "summary": [summary_1, summary_2, summary_3],
            "detailedExplanation": detailed.strip(),
            "troubleshootingTip": tip.strip(),
        }

    def _extract_json(self, text: str) -> Any:
        """마크다운 코드블록 제거 및 JSON 추출."""
        cleaned = re.sub(r"```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        cleaned = cleaned.replace("```", "").strip()

        # 1) 전체 json.loads 시도
        try:
            return json.loads(cleaned)
        except Exception:
            pass

        # 2) { ... } 객체 슬라이스 시도
        start_obj = cleaned.find("{")
        end_obj = cleaned.rfind("}")
        if start_obj != -1 and end_obj > start_obj:
            candidate = cleaned[start_obj : end_obj + 1]
            try:
                return json.loads(candidate)
            except Exception:
                pass

        # 3) [ ... ] 배열 슬라이스 시도
        start_arr = cleaned.find("[")
        end_arr = cleaned.rfind("]")
        if start_arr != -1 and end_arr > start_arr:
            candidate = cleaned[start_arr : end_arr + 1]
            try:
                return json.loads(candidate)
            except Exception:
                pass

        raise ValueError(f"응답에서 유효한 JSON을 추출할 수 없습니다. 텍스트 앞부분: {cleaned[:150]}")

    def _generate_fallback_quizzes(
        self, request: QuizGenerateRequest, ref_titles: list[str]
    ) -> list[QuizQuestionItem]:
        """LLM 호출 불가/실패 시 서비스 지속성을 보장하는 고품질 백업 퀴즈."""
        category_lower = request.category.lower()

        # Database 카테고리 템플릿
        if "db" in category_lower or "data" in category_lower:
            return [
                QuizQuestionItem(
                    id=1,
                    question="RDBMS에서 인덱스(B-Tree Index) 설계 시 카디널리티(Cardinality)와 선택도(Selectivity)에 대한 설명으로 가장 올바른 것은?",
                    type="multiple_choice",
                    options=[
                        "1. 카디널리티가 높고 선택도가 낮을수록(1에 가까울수록) 인덱스 효율이 우수하다.",
                        "2. 카디널리티가 낮을수록(예: 성별 필드) 인덱스 스캔 효율이 가장 극대화된다.",
                        "3. 인덱스는 무조건 많은 컬럼을 복합 인덱스로 묶는 것이 성능상 항상 유리하다.",
                        "4. 인덱스 리프 노드는 실제 테이블의 모든 컬럼 데이터를 항상 복사하여 저장한다.",
                    ],
                    answer="1",
                    explanation="카디널리티가 높다는 것은 중복된 값이 적고 유니크한 값이 많음을 의미합니다. 선택도가 우수할수록 B-Tree 탐색 후 테이블 랜덤 I/O를 최소화할 수 있습니다.",
                    references=ref_titles or ["Database B-Tree 인덱스 최적화"],
                ),
                QuizQuestionItem(
                    id=2,
                    question="트랜잭션 격리 수준(Isolation Level) 중 'Non-Repeatable Read'는 방지하지만 'Phantom Read'가 발생할 수 있는 수준은?",
                    type="multiple_choice",
                    options=[
                        "1. Read Uncommitted",
                        "2. Read Committed",
                        "3. Repeatable Read",
                        "4. Serializable",
                    ],
                    answer="3",
                    explanation="Repeatable Read는 하나의 트랜잭션 내에서 동일한 행을 여러 번 조회할 때 동일한 데이터를 보장하지만, 다른 트랜잭션이 새로운 행을 INSERT할 때 범위 조회에서 새로운 행이 나타나는 Phantom Read가 발생할 수 있습니다 (InnoDB는 MVCC/Next-Key Lock으로 완화).",
                    references=ref_titles or ["트랜잭션 격리 수준과 MVCC"],
                ),
                QuizQuestionItem(
                    id=3,
                    question="MySQL InnoDB 스토리지 엔진의 클러스터링 인덱스(Clustered Index)는 프라이머리 키(PK) 순서로 데이터 레코드가 물리적으로 정렬되어 저장된다.",
                    type="ox",
                    options=["1. O", "2. X"],
                    answer="1",
                    explanation="InnoDB에서 클러스터링 인덱스는 PK 값을 키로 가지며 리프 노드에 실제 데이터 행 전체를 저장하므로, PK 순서대로 물리적 정렬이 이루어집니다.",
                    references=ref_titles or ["InnoDB 클러스터링 인덱스 구조"],
                ),
            ][: request.count]

        # 기본 CS/Spring 공통 템플릿
        return [
            QuizQuestionItem(
                id=1,
                question=f"[{request.category}] 대규모 트래픽 처리 환경에서 동시성 제어(Concurrency Control)를 위한 낙관적 락(Optimistic Lock)과 비관적 락(Pessimistic Lock)의 선택 기준으로 가장 적절한 것은?",
                type="multiple_choice",
                options=[
                    "1. 충돌이 빈번하게 발생하는 환경에서는 롤백 비용이 큰 낙관적 락보다 비관적 락이 성능상 유리하다.",
                    "2. 낙관적 락은 데이터베이스 레벨에서 SELECT FOR UPDATE 쿼리를 사용하여 강제로 락을 획득한다.",
                    "3. 비관적 락은 애플리케이션 레벨의 버전(Version) 컬럼을 통해 정합성을 검증한다.",
                    "4. 두 방식 모두 트래픽과 데이터 충돌 빈도에 무관하게 항상 동일한 처리량을 보인다.",
                ],
                answer="1",
                explanation="낙관적 락은 충돌이 적을 것으로 가정하여 커밋 시점에 버전 체크를 수행합니다. 충돌이 빈번할 경우 재시도(Retry) 및 롤백 오버헤드가 급증하므로, 충돌이 잦은 환경에서는 데이터베이스 락을 직접 잡는 비관적 락이 더 유리할 수 있습니다.",
                references=ref_titles or [f"{request.category} 동시성 제어"],
            ),
            QuizQuestionItem(
                id=2,
                question="TCP 3-Way Handshake 과정에서 클라이언트와 서버 간의 연결을 맺기 위해 주고받는 플래그의 올바른 순서는?",
                type="multiple_choice",
                options=[
                    "1. SYN -> SYN-ACK -> ACK",
                    "2. ACK -> SYN -> ACK-SYN",
                    "3. FIN -> ACK -> FIN-ACK",
                    "4. SYN -> ACK -> FIN",
                ],
                answer="1",
                explanation="TCP 통신을 시작하기 위해 클라이언트가 SYN 패킷을 전송하고, 서버가 SYN-ACK로 응답하며, 클라이언트가 최종 ACK를 전송함으로써 신뢰성 있는 세션이 수립됩니다.",
                references=ref_titles or ["TCP 프로토콜 연결 수립"],
            ),
        ][: request.count]
