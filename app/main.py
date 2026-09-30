import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import chat, evaluation, health, quiz, rag
from app.services.embedding_warmup_state import run_startup_embedding_warmup


@asynccontextmanager
async def lifespan(_: FastAPI):
    """`app.*` 로거의 INFO 가 컨테이너 stdout 에 남도록 한다 (docker logs 확인용)."""
    app_logger = logging.getLogger("app")
    if not app_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(levelname)s %(name)s %(message)s"),
        )
        app_logger.addHandler(handler)
        app_logger.setLevel(logging.INFO)

    run_startup_embedding_warmup(app_logger)
    yield


app = FastAPI(
    title="COBIP CS Quiz & RAG AI Server",
    version="1.0.0",
    description="Qdrant 기반 실무 CS 퀴즈 자동 생성 및 심층 오답 해설 AI 백엔드 API",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# [핵심 라우터 등록]
app.include_router(health.router)
app.include_router(quiz.router)  # 신규 퀴즈 생성 및 해설 도메인
app.include_router(rag.router)   # Qdrant 문서 인덱싱 및 단독 검색
app.include_router(chat.router)  # 심층 멘토링 질의응답 챗봇
app.include_router(evaluation.router)  # 자바 코딩테스트 채점 및 코드 분석

# [레거시 라우터 격리 보관]
# 모바일 환경 개편에 따라 feature_template / code 채점 mock 라우터는 비활성화/격리 처리됨.
# from app.api.routes import agentic, feature_template, grammar
# app.include_router(feature_template.router)
# app.include_router(grammar.router)
# app.include_router(agentic.router)
