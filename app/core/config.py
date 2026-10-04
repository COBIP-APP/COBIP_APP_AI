"""환경변수 기반 설정."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    APP_ENV: str = "local"
    API_PREFIX: str = "/ai"

    REDIS_URL: str | None = None

    # Qdrant (Compose 서비스명 기본; 로컬 단독은 host.docker.internal 등으로 덮어쓰기)
    QDRANT_URL: str = "http://qdrant:6333"
    QDRANT_COLLECTION: str = "cobip_knowledge"

    # Embedding (Retriever 연동 전 단계)
    EMBEDDING_MODEL: str = "BAAI/bge-m3"

    LLM_PROVIDER: str = "ollama"
    OLLAMA_BASE_URL: str = "http://host.docker.internal:11434/v1"
    OLLAMA_MODEL: str = "qwen2.5-coder:1.5b"
    LLM_TEMPERATURE: float = 0.3
    LLM_MAX_TOKENS: int = 4096
    LLM_TIMEOUT_SECONDS: int = 60
    FEATURE_TEMPLATE_LLM_TIMEOUT_SECONDS: int = 120

    # /ai/code/analyze 안정성: 작은 토큰 상한 + 입력 코드 길이 제한으로
    # 터널 경유 장시간 생성·연결 끊김(Server disconnected)을 방지한다.
    CODE_ANALYZE_MAX_TOKENS: int = 800
    CODE_ANALYZE_MAX_CODE_CHARS: int = 4000

    CACHE_TTL_SECONDS: int = 3600
    RATE_LIMIT_PER_MINUTE: int = 30

    # RAG: Retriever 연동 전 토글·파라미터
    RAG_ENABLED: bool = False
    RAG_TOP_K: int = 3
    # /ai/chat 전용: 이 score 미만 검색 결과는 prompt/references 에서 제외
    CHAT_RAG_MIN_SCORE: float = 0.55

    # Agentic RAG 15차: 기능템플릿 전용 RAG 검색·프롬프트 방어 파라미터
    FEATURE_TEMPLATE_RAG_TOP_K: int = 3
    FEATURE_TEMPLATE_RAG_CONTENT_MAX_CHARS: int = 1000

    # Agentic RAG 16차: warm-up 및 Redis 캐시 제어
    EMBEDDING_WARMUP_ENABLED: bool = False
    EMBEDDING_WARMUP_TEXT: str = "Spring Boot 로그인 기능템플릿 RAG warm up"
    RAG_RETRIEVAL_CACHE_ENABLED: bool = False
    RAG_RETRIEVAL_CACHE_TTL_SECONDS: int = 3600
    FEATURE_TEMPLATE_CACHE_ENABLED: bool = False
    FEATURE_TEMPLATE_CACHE_TTL_SECONDS: int = 3600

    # Agentic RAG 24차: LLM full-first (GPU Ollama + RAG 전체 생성, instant는 fallback 전용)
    FEATURE_TEMPLATE_LLM_FULL_FIRST_ENABLED: bool = True

    # Agentic RAG 17차: 최초 generate fast skeleton (짧은 초안, uncached 속도 개선)
    FEATURE_TEMPLATE_FAST_SKELETON_ENABLED: bool = False

    # Agentic RAG 18차: ultra-fast skeleton (LLM 생성 범위 최소화, normalizer 보정)
    FEATURE_TEMPLATE_ULTRA_FAST_SKELETON_ENABLED: bool = False
    FEATURE_TEMPLATE_SKELETON_RAG_TOP_K: int = 2
    FEATURE_TEMPLATE_SKELETON_RAG_CONTENT_MAX_CHARS: int = 300
    FEATURE_TEMPLATE_SKELETON_MAX_TOKENS: int = 800

    # Agentic RAG 22차: quality instant skeleton (LLM 실패 시 fallback 전용)
    FEATURE_TEMPLATE_INSTANT_SKELETON_ENABLED: bool = True
    FEATURE_TEMPLATE_INITIAL_LLM_ENHANCEMENT_ENABLED: bool = False
    FEATURE_TEMPLATE_INITIAL_LLM_TIMEOUT_SECONDS: int = 8
    FEATURE_TEMPLATE_CACHE_INSTANT_SKELETON_ENABLED: bool = True

    # Agent /ai/chat: 룰 기본, 선택적 LLM intent 보조
    AGENT_LLM_INTENT_ENABLED: bool = False
    AGENT_LLM_INTENT_REFINE_GENERAL: bool = False


settings = Settings()
