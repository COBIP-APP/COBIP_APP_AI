# COBIP 실무 AI 퀴즈 apiSpec 명세

`docs/rag/feature-app/app-practical-quiz-template.md`의 엔드포인트를 `cobip-api-spec-rules.md`의 `apiSpec[]` 확장 필드 규칙으로 작성한 명세다. 퀴즈 데이터 구조와 JSON Schema는 템플릿 문서를 기준으로 한다.

## 확장 필드 적용

| 필드 | 적용 기준 |
| --- | --- |
| authenticationRequired | App → Spring Boot API는 true (JWT). Spring Boot → FastAPI 내부 API는 false (내부망 전용, App 직접 호출 금지) |
| requestHeaders | Content-Type: application/json, Spring Boot API는 Authorization 추가 |
| requestFields | snake_case 요청 필드 설명 배열 |
| responseFields | snake_case 응답 필드 설명 배열 |
| statusCodes | 성공 + 입력 검증 실패 + 인증/권한 + AI 연동 실패 코드 |
| errorResponses | Spring Boot는 `{ "message", "fieldErrors" }`, FastAPI는 기본 `{ "detail" }` 형식 |
| frontendNotes | App 연동 시 주의사항 (FastAPI 내부 API는 호출 주체 명시) |

## 1. 실무 퀴즈 생성 (Spring Boot)

```json
{
  "apiName": "실무 퀴즈 생성",
  "method": "POST",
  "endpoint": "/api/practical-quizzes/generate",
  "description": "분야·유형·난이도를 받아 AI 서버로 실무 퀴즈 세트를 생성하고 저장한 뒤, 정답을 제외한 문제 목록을 반환한다.",
  "authenticationRequired": true,
  "requestHeaders": {
    "Content-Type": "application/json",
    "Authorization": "Bearer {accessToken}"
  },
  "requestBody": {
    "category": "BACKEND",
    "domain": "SPRING",
    "quiz_type": "MULTIPLE_CHOICE",
    "difficulty": "intermediate",
    "count": 3,
    "keywords": ["트랜잭션 전파", "AOP 프록시"]
  },
  "requestFields": [
    {"name": "category", "type": "string", "required": true, "description": "BACKEND / DATABASE / DEVOPS / AI_RAG"},
    {"name": "domain", "type": "string", "required": true, "description": "category에 허용된 domain (예: SPRING, SQL, KUBERNETES, RAG)"},
    {"name": "quiz_type", "type": "string", "required": true, "description": "MULTIPLE_CHOICE / BLANK / DESCRIPTIVE"},
    {"name": "difficulty", "type": "string", "required": true, "description": "beginner / intermediate / advanced"},
    {"name": "count", "type": "integer", "required": true, "description": "생성 문항 수 (1~10)"},
    {"name": "keywords", "type": "string[]", "required": false, "description": "출제 키워드 (최대 5개)"}
  ],
  "responseBody": {
    "quiz_set_id": "3f1c9a2e-7b4d-4e0a-9c1f-2a8b6d5e4f30",
    "total_count": 1,
    "quizzes": [
      {
        "quiz_id": "3f1c9a2e-7b4d-4e0a-9c1f-2a8b6d5e4f30-01",
        "quiz_type": "MULTIPLE_CHOICE",
        "category": "BACKEND",
        "domain": "SPRING",
        "difficulty": "intermediate",
        "title": "@Transactional self-invocation 미적용",
        "scenario": "주문 서비스에서 placeOrder()가 같은 클래스의 saveOrderHistory()를 호출한다 ...",
        "question": "saveOrderHistory()에 REQUIRES_NEW가 적용되지 않는 가장 직접적인 원인은?",
        "code_snippet": {"language": "java", "file_name": "OrderService.java", "content": "@Service\npublic class OrderService { ... }"},
        "multiple_choice": {
          "options": [
            {"option_id": "A", "text": "같은 인스턴스 내부 호출은 Spring AOP 프록시를 거치지 않는다"},
            {"option_id": "B", "text": "REQUIRES_NEW는 public 메서드에 선언할 수 없다"},
            {"option_id": "C", "text": "JpaRepository.save()는 트랜잭션 전파를 무시한다"},
            {"option_id": "D", "text": "하나의 클래스에는 @Transactional을 한 번만 선언할 수 있다"}
          ]
        },
        "blank": null,
        "descriptive": null,
        "tags": ["spring", "transaction", "aop"]
      }
    ]
  },
  "responseFields": [
    {"name": "quiz_set_id", "type": "string", "description": "퀴즈 세트 ID (UUID)"},
    {"name": "total_count", "type": "integer", "description": "생성된 문항 수"},
    {"name": "quizzes", "type": "object[]", "description": "정답 은닉 뷰 목록"},
    {"name": "quizzes[].multiple_choice", "type": "object|null", "description": "options만 포함 (answer_option_id, option_explanations 제외)"},
    {"name": "quizzes[].blank", "type": "object|null", "description": "blank_count, hint만 포함 (answers 제외)"},
    {"name": "quizzes[].descriptive", "type": "object|null", "description": "max_score, pass_score, answer_guide만 포함 (rubric, core_keywords, model_answer 제외)"}
  ],
  "status": 201,
  "statusCodes": [
    {"code": 201, "description": "퀴즈 생성 및 저장 성공"},
    {"code": 400, "description": "입력 검증 실패 또는 category-domain 조합 오류"},
    {"code": 401, "description": "accessToken 누락/만료"},
    {"code": 502, "description": "AI 서버 오류 응답"},
    {"code": 504, "description": "AI 서버 응답 시간 초과 (60초)"}
  ],
  "errorResponses": [
    {"status": 400, "body": {"message": "Validation failed", "fieldErrors": [{"field": "domain", "message": "category BACKEND에 허용되지 않은 domain입니다."}]}},
    {"status": 401, "body": {"message": "Unauthorized"}},
    {"status": 502, "body": {"message": "AI quiz generation failed"}},
    {"status": 504, "body": {"message": "AI quiz generation timeout"}}
  ],
  "frontendNotes": "LLM 생성으로 수 초~수십 초가 걸리므로 로딩 상태와 중복 요청 방지를 처리한다. quiz_type별로 non-null인 객체(multiple_choice/blank/descriptive)만 렌더링하고, code_snippet.language로 코드 하이라이팅한다. BLANK는 code_snippet.content의 ___ 위치에 입력 필드를 순서대로 배치한다."
}
```

## 2. 실무 퀴즈 단건 조회 (Spring Boot)

```json
{
  "apiName": "실무 퀴즈 단건 조회",
  "method": "GET",
  "endpoint": "/api/practical-quizzes/{quiz_id}",
  "description": "저장된 실무 퀴즈 1건을 정답 은닉 뷰로 조회한다.",
  "authenticationRequired": true,
  "requestHeaders": {
    "Authorization": "Bearer {accessToken}"
  },
  "requestBody": null,
  "requestFields": [
    {"name": "quiz_id", "type": "string", "required": true, "description": "path variable, 퀴즈 ID"}
  ],
  "responseBody": {
    "quiz_id": "3f1c9a2e-7b4d-4e0a-9c1f-2a8b6d5e4f30-02",
    "quiz_type": "BLANK",
    "category": "DEVOPS",
    "domain": "KUBERNETES",
    "difficulty": "intermediate",
    "title": "Spring Boot Pod 헬스체크 설정",
    "scenario": "Spring Boot 애플리케이션이 기동에 40초가 걸리는데 ...",
    "question": "빈칸에 들어갈 probe 키 이름을 순서대로 채우시오.",
    "code_snippet": {"language": "yaml", "file_name": "deployment.yaml", "content": "containers:\n  - name: order-api\n    ___:\n ..."},
    "multiple_choice": null,
    "blank": {"blank_count": 2, "hint": "재시작 판단용 probe와 트래픽 유입 판단용 probe"},
    "descriptive": null,
    "tags": ["kubernetes", "probe"]
  },
  "responseFields": [
    {"name": "quiz_id", "type": "string", "description": "퀴즈 ID"},
    {"name": "quiz_type", "type": "string", "description": "MULTIPLE_CHOICE / BLANK / DESCRIPTIVE"},
    {"name": "scenario", "type": "string", "description": "실무 상황/배경"},
    {"name": "code_snippet", "type": "object|null", "description": "문제용 코드/설정 블록"},
    {"name": "blank", "type": "object|null", "description": "정답 제외 빈칸 정보"}
  ],
  "status": 200,
  "statusCodes": [
    {"code": 200, "description": "조회 성공"},
    {"code": 401, "description": "accessToken 누락/만료"},
    {"code": 403, "description": "다른 사용자의 퀴즈 세트 접근"},
    {"code": 404, "description": "quiz_id 없음"}
  ],
  "errorResponses": [
    {"status": 401, "body": {"message": "Unauthorized"}},
    {"status": 403, "body": {"message": "Forbidden"}},
    {"status": 404, "body": {"message": "Quiz not found", "quiz_id": "3f1c9a2e-7b4d-4e0a-9c1f-2a8b6d5e4f30-99"}}
  ],
  "frontendNotes": "문제 재진입·새로고침 시 사용한다. 이미 제출한 문항은 제출 응답을 캐시해 해설 화면을 복원한다."
}
```

## 3. 실무 퀴즈 답안 제출 (Spring Boot)

```json
{
  "apiName": "실무 퀴즈 답안 제출",
  "method": "POST",
  "endpoint": "/api/practical-quizzes/{quiz_id}/submissions",
  "description": "답안을 제출하면 MULTIPLE_CHOICE/BLANK는 Spring Boot에서, DESCRIPTIVE는 AI 서버에서 채점하고 채점 결과와 상세 해설을 반환한다.",
  "authenticationRequired": true,
  "requestHeaders": {
    "Content-Type": "application/json",
    "Authorization": "Bearer {accessToken}"
  },
  "requestBody": {
    "quiz_type": "BLANK",
    "selected_option_id": null,
    "blank_answers": ["livenessProbe", "startupProbe"],
    "descriptive_answer": null
  },
  "requestFields": [
    {"name": "quiz_type", "type": "string", "required": true, "description": "저장된 퀴즈의 quiz_type과 일치해야 함"},
    {"name": "selected_option_id", "type": "string", "required": false, "description": "MULTIPLE_CHOICE 필수, A~D"},
    {"name": "blank_answers", "type": "string[]", "required": false, "description": "BLANK 필수, blank_index 순서, 길이 = blank_count"},
    {"name": "descriptive_answer", "type": "string", "required": false, "description": "DESCRIPTIVE 필수, 20~3000자"}
  ],
  "responseBody": {
    "submission_id": 1024,
    "quiz_id": "3f1c9a2e-7b4d-4e0a-9c1f-2a8b6d5e4f30-02",
    "quiz_type": "BLANK",
    "is_correct": false,
    "score": 50,
    "max_score": 100,
    "grading_detail": {
      "blank_results": [
        {"blank_index": 1, "user_answer": "livenessProbe", "is_correct": true, "accepted_answers": ["livenessProbe"]},
        {"blank_index": 2, "user_answer": "startupProbe", "is_correct": false, "accepted_answers": ["readinessProbe"]}
      ]
    },
    "detailed_explanation": {
      "core_concept": "livenessProbe는 재시작 여부, readinessProbe는 Service 엔드포인트 등록 여부를 결정한다.",
      "explanation": "기동이 느린 앱에 짧은 liveness 지연을 주면 초기화 중 재시작 루프가 발생한다 ...",
      "practical_tips": ["기동이 매우 느리면 startupProbe를 추가해 liveness 검사를 지연시킨다."],
      "common_mistakes": ["liveness와 readiness에 동일 경로를 사용한다."]
    },
    "submitted_at": "2026-09-30T12:00:00Z"
  },
  "responseFields": [
    {"name": "submission_id", "type": "integer", "description": "제출 ID"},
    {"name": "is_correct", "type": "boolean", "description": "정답 여부 (DESCRIPTIVE는 score >= pass_score)"},
    {"name": "score", "type": "integer", "description": "0~100 점수"},
    {"name": "max_score", "type": "integer", "description": "100 고정"},
    {"name": "grading_detail", "type": "object", "description": "quiz_type별 채점 상세 (템플릿 문서 표 참고)"},
    {"name": "detailed_explanation", "type": "object", "description": "AI 상세 해설 및 실무 팁"},
    {"name": "submitted_at", "type": "string", "description": "ISO-8601 UTC 제출 시각"}
  ],
  "status": 200,
  "statusCodes": [
    {"code": 200, "description": "채점 성공"},
    {"code": 400, "description": "quiz_type 불일치, 유형별 필수 답안 누락, blank_answers 길이 불일치"},
    {"code": 401, "description": "accessToken 누락/만료"},
    {"code": 404, "description": "quiz_id 없음"},
    {"code": 502, "description": "DESCRIPTIVE AI 채점 실패"},
    {"code": 504, "description": "DESCRIPTIVE AI 채점 시간 초과 (30초)"}
  ],
  "errorResponses": [
    {"status": 400, "body": {"message": "Validation failed", "fieldErrors": [{"field": "blank_answers", "message": "빈칸 2개에 대한 답안이 필요합니다."}]}},
    {"status": 401, "body": {"message": "Unauthorized"}},
    {"status": 404, "body": {"message": "Quiz not found", "quiz_id": "3f1c9a2e-7b4d-4e0a-9c1f-2a8b6d5e4f30-99"}},
    {"status": 502, "body": {"message": "AI grading failed"}}
  ],
  "frontendNotes": "quiz_type에 해당하는 답안 필드 하나만 채워 전송한다. 제출 후에만 정답과 detailed_explanation을 표시한다. DESCRIPTIVE는 grading_detail.rubric_results로 항목별 점수·누락 키워드를, improved_answer로 보강 답안을 보여준다."
}
```

## 4. AI 실무 퀴즈 생성 (FastAPI 내부)

```json
{
  "apiName": "AI 실무 퀴즈 생성",
  "method": "POST",
  "endpoint": "/ai/practical-quiz/generate",
  "description": "Qdrant 실무 지식을 검색해 프롬프트에 주입하고 Ollama로 정답·해설·루브릭을 포함한 실무 퀴즈 원본을 생성한다.",
  "authenticationRequired": false,
  "requestHeaders": {
    "Content-Type": "application/json"
  },
  "requestBody": {
    "quiz_set_id": "3f1c9a2e-7b4d-4e0a-9c1f-2a8b6d5e4f30",
    "category": "DATABASE",
    "domain": "SQL",
    "quiz_type": "DESCRIPTIVE",
    "difficulty": "advanced",
    "count": 1,
    "keywords": ["N+1", "인덱스"],
    "use_rag": true
  },
  "requestFields": [
    {"name": "quiz_set_id", "type": "string", "required": true, "description": "Spring Boot 발급 UUID, quiz_id prefix로 사용"},
    {"name": "category", "type": "string", "required": true, "description": "BACKEND / DATABASE / DEVOPS / AI_RAG"},
    {"name": "domain", "type": "string", "required": true, "description": "category에 허용된 domain"},
    {"name": "quiz_type", "type": "string", "required": true, "description": "MULTIPLE_CHOICE / BLANK / DESCRIPTIVE"},
    {"name": "difficulty", "type": "string", "required": true, "description": "beginner / intermediate / advanced"},
    {"name": "count", "type": "integer", "required": true, "description": "1~10"},
    {"name": "keywords", "type": "string[]", "required": false, "description": "RAG query 및 출제 키워드"},
    {"name": "use_rag", "type": "boolean", "required": false, "description": "기본 true, RAG_ENABLED=false면 무시"}
  ],
  "responseBody": {
    "success": true,
    "message": "실무 퀴즈 생성이 완료되었습니다.",
    "data": {
      "quiz_set_id": "3f1c9a2e-7b4d-4e0a-9c1f-2a8b6d5e4f30",
      "category": "DATABASE",
      "domain": "SQL",
      "quiz_type": "DESCRIPTIVE",
      "difficulty": "advanced",
      "total_count": 1,
      "quizzes": [
        {
          "quiz_id": "3f1c9a2e-7b4d-4e0a-9c1f-2a8b6d5e4f30-01",
          "quiz_type": "DESCRIPTIVE",
          "title": "주문 목록 API 응답 지연 트러블슈팅",
          "scenario": "...",
          "question": "원인을 분석하고 개선 방안과 검증 방법을 서술하시오.",
          "code_snippet": {"language": "java", "file_name": "OrderQueryService.java", "content": "..."},
          "multiple_choice": null,
          "blank": null,
          "descriptive": {"max_score": 100, "pass_score": 60, "rubric": [], "core_keywords": ["N+1", "fetch join", "인덱스", "EXPLAIN"], "model_answer": "...", "answer_guide": "원인 → 개선 → 검증 순서로 5~10문장 작성"},
          "detailed_explanation": {"core_concept": "...", "explanation": "...", "practical_tips": ["..."], "common_mistakes": ["..."]},
          "tags": ["jpa", "n+1", "index"],
          "references": [{"ref_id": 1, "title": "JPA N+1 문제와 해결", "path": "data/raw/practical/database/sql/jpa-n-plus-1.md", "score": 0.79}]
        }
      ],
      "generation_source": "ollama",
      "rag_trace": {"retrieval_status": "success", "retrieved_count": 5, "injected_count": 3, "query": "SQL DATABASE N+1 인덱스 서술형 실무 트러블슈팅 설정 예시", "failure_reason": null}
    }
  },
  "responseFields": [
    {"name": "data.quizzes", "type": "object[]", "description": "PracticalQuizItem 원본 (정답·루브릭 포함) — Spring Boot 저장용"},
    {"name": "data.generation_source", "type": "string", "description": "ollama / fallback"},
    {"name": "data.rag_trace", "type": "object", "description": "retrieval_status(success/empty/failed/skipped), retrieved_count, injected_count, query, failure_reason"}
  ],
  "status": 200,
  "statusCodes": [
    {"code": 200, "description": "생성 성공 (LLM 실패 시 fallback 문항 포함)"},
    {"code": 400, "description": "category-domain 조합 오류"},
    {"code": 422, "description": "Pydantic 요청 스키마 검증 실패"},
    {"code": 500, "description": "fallback까지 실패한 내부 오류"}
  ],
  "errorResponses": [
    {"status": 400, "body": {"detail": "category와 domain 조합이 올바르지 않습니다."}},
    {"status": 422, "body": {"detail": [{"loc": ["body", "count"], "msg": "Input should be less than or equal to 10", "type": "less_than_equal"}]}},
    {"status": 500, "body": {"detail": "실무 퀴즈 생성 중 오류가 발생했습니다."}}
  ],
  "frontendNotes": "App에서 직접 호출하지 않는다. Spring Boot가 내부망으로 호출하며 read timeout은 60초 이상으로 설정한다. generation_source=fallback이면 Spring Boot는 저장은 하되 운영 모니터링 로그를 남긴다."
}
```

## 5. AI 서술형 채점 (FastAPI 내부)

```json
{
  "apiName": "AI 서술형 채점",
  "method": "POST",
  "endpoint": "/ai/practical-quiz/grade",
  "description": "DESCRIPTIVE 문항의 사용자 답안을 루브릭·핵심 키워드·모범 답안 기준으로 채점하고 항목별 피드백을 반환한다.",
  "authenticationRequired": false,
  "requestHeaders": {
    "Content-Type": "application/json"
  },
  "requestBody": {
    "quiz_id": "3f1c9a2e-7b4d-4e0a-9c1f-2a8b6d5e4f30-03",
    "quiz_type": "DESCRIPTIVE",
    "scenario": "주문 목록 API(GET /api/orders)가 ...",
    "question": "원인을 분석하고 개선 방안과 검증 방법을 서술하시오.",
    "descriptive": {"max_score": 100, "pass_score": 60, "rubric": [], "core_keywords": ["N+1", "fetch join", "인덱스", "EXPLAIN"], "model_answer": "..."},
    "user_answer": "요청마다 items를 지연 로딩해서 N+1이 발생합니다. fetch join으로 바꾸고 member_id에 인덱스를 추가합니다."
  },
  "requestFields": [
    {"name": "quiz_id", "type": "string", "required": true, "description": "채점 대상 퀴즈 ID"},
    {"name": "quiz_type", "type": "string", "required": true, "description": "DESCRIPTIVE 고정"},
    {"name": "scenario", "type": "string", "required": true, "description": "문제 실무 상황"},
    {"name": "question", "type": "string", "required": true, "description": "문제 질문"},
    {"name": "descriptive", "type": "object", "required": true, "description": "저장된 루브릭·핵심 키워드·모범 답안 원본"},
    {"name": "user_answer", "type": "string", "required": true, "description": "사용자 서술 답안 (20~3000자)"}
  ],
  "responseBody": {
    "success": true,
    "message": "서술형 채점이 완료되었습니다.",
    "data": {
      "quiz_id": "3f1c9a2e-7b4d-4e0a-9c1f-2a8b6d5e4f30-03",
      "score": 70,
      "max_score": 100,
      "is_passed": true,
      "rubric_results": [
        {"criterion_id": "R1", "earned_points": 30, "max_points": 40, "matched_keywords": ["N+1", "지연 로딩"], "missing_keywords": ["인덱스 부재|Full Table Scan"], "feedback": "Full Table Scan 원인 설명이 없습니다."}
      ],
      "overall_feedback": "원인과 해결책은 적절하나 검증 단계가 빠졌습니다.",
      "improved_answer": "...",
      "grading_source": "ollama"
    }
  },
  "responseFields": [
    {"name": "data.score", "type": "integer", "description": "rubric_results[].earned_points 합계 (0~100)"},
    {"name": "data.is_passed", "type": "boolean", "description": "score >= pass_score"},
    {"name": "data.rubric_results", "type": "object[]", "description": "항목별 점수, matched/missing 키워드, 피드백"},
    {"name": "data.improved_answer", "type": "string", "description": "사용자 답안에 누락 항목을 보강한 답안"},
    {"name": "data.grading_source", "type": "string", "description": "ollama / fallback_keyword"}
  ],
  "status": 200,
  "statusCodes": [
    {"code": 200, "description": "채점 성공 (LLM 실패 시 키워드 fallback 채점)"},
    {"code": 400, "description": "quiz_type이 DESCRIPTIVE가 아님 또는 루브릭 배점 합 != max_score"},
    {"code": 422, "description": "Pydantic 요청 스키마 검증 실패"},
    {"code": 500, "description": "내부 오류"}
  ],
  "errorResponses": [
    {"status": 400, "body": {"detail": "서술형(DESCRIPTIVE) 문항만 AI 채점을 지원합니다."}},
    {"status": 422, "body": {"detail": [{"loc": ["body", "user_answer"], "msg": "Field required", "type": "missing"}]}},
    {"status": 500, "body": {"detail": "서술형 채점 중 오류가 발생했습니다."}}
  ],
  "frontendNotes": "App에서 직접 호출하지 않는다. Spring Boot는 저장된 descriptive 원본을 그대로 전달하고, 응답을 제출 이력(grading_detail)에 저장한다. read timeout은 30초로 설정한다."
}
```

## 금지 사항

- endpoint에 `/api/feature`, `/api/example`, 기존 `/ai/quiz/*` 경로 사용 금지
- statusCodes에 성공 코드만 넣고 400/401(Spring Boot) 또는 400/422(FastAPI) 누락 금지
- App 대상 API의 authenticationRequired를 false로 설정 금지
- 채점 전 응답(생성·조회)에 정답·루브릭·모범 답안 포함 금지
