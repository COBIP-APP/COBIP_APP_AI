"""실무 CS 및 기술면접 마크다운/텍스트 대량 Qdrant 인덱싱 파이프라인.

로컬 문서(마크다운, 텍스트)를 재귀적으로 읽어 의미 단위(500~800자)로 청킹한 뒤,
BGE-M3 임베딩 모델을 통해 벡터화하여 Qdrant 컬렉션에 배치 단위로 대량 업로드합니다.

사용법:
    # 1. 청킹 및 파싱 결과 사전 점검 (외부 서비스 호출 없음)
    python scripts/bulk_indexer.py --data-dir data/raw --dry-run

    # 2. 실제 Qdrant 인덱싱 수행 (배치 사이즈 32)
    python scripts/bulk_indexer.py --data-dir data/raw --apply --batch-size 32

    # 3. 특정 컬렉션 지정 및 청크 크기 조절
    python scripts/bulk_indexer.py --data-dir data/raw --apply --collection cobip_knowledge --chunk-size 650 --chunk-overlap 100
"""

from __future__ import annotations

import argparse
import glob
import logging
import os
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 프로젝트 루트를 PYTHONPATH에 등록
CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from app.core.config import settings
    DEFAULT_COLLECTION = settings.QDRANT_COLLECTION
except Exception:
    DEFAULT_COLLECTION = os.getenv("QDRANT_COLLECTION", "cobip_knowledge")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("bulk_indexer")

NAMESPACE_INDEXER = uuid.UUID("a3b89012-4c5d-4e6f-8a1b-9c0d1e2f3a4b")


@dataclass
class DocumentChunk:
    chunk_id: str
    point_id: str
    title: str
    content: str
    category: str
    topic: str
    source_file: str
    chunk_index: int


class MarkdownChunker:
    """마크다운 헤더(#, ##, ###)와 문단을 고려하여 의미 단위로 청킹하는 클래스."""

    def __init__(self, chunk_size: int = 650, chunk_overlap: int = 100) -> None:
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def chunk_file(
        self,
        file_path: Path,
        base_dir: Path,
    ) -> list[DocumentChunk]:
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                raw_text = f.read()
        except Exception as exc:
            logger.warning("파일 읽기 실패: %s (%s)", file_path, exc)
            return []

        rel_path = file_path.relative_to(base_dir).as_posix()
        parts = rel_path.split("/")

        # 디렉터리 구조 기반 category/topic 추출
        if len(parts) > 1:
            category = parts[0]
            topic = file_path.stem
        else:
            category = "CS"
            topic = file_path.stem

        # 문서 제목 추출 (H1 헤더 우선)
        h1_match = re.search(r"^#\s+(.+)$", raw_text, re.MULTILINE)
        doc_title = h1_match.group(1).strip() if h1_match else file_path.stem

        # 헤더/단락 단위 분할
        sections = self._split_into_sections(raw_text)
        chunks: list[DocumentChunk] = []
        chunk_idx = 0

        for sec_header, sec_text in sections:
            header_prefix = f"[{category} > {doc_title}"
            if sec_header and sec_header != doc_title:
                header_prefix += f" > {sec_header}"
            header_prefix += "]\n"

            sub_chunks = self._sliding_window_chunks(sec_text, header_prefix)
            for c_text in sub_chunks:
                cid = f"{rel_path}#chunk_{chunk_idx}"
                pid = str(uuid.uuid5(NAMESPACE_INDEXER, cid))
                chunks.append(
                    DocumentChunk(
                        chunk_id=cid,
                        point_id=pid,
                        title=f"{doc_title} - {sec_header}" if sec_header else doc_title,
                        content=c_text,
                        category=category,
                        topic=topic,
                        source_file=rel_path,
                        chunk_index=chunk_idx,
                    )
                )
                chunk_idx += 1

        return chunks

    def _split_into_sections(self, text: str) -> list[tuple[str, str]]:
        """마크다운 헤더 기준으로 섹션을 분리."""
        lines = text.split("\n")
        sections: list[tuple[str, str]] = []
        current_header = ""
        current_lines: list[str] = []

        for line in lines:
            header_match = re.match(r"^(#{1,3})\s+(.+)$", line)
            if header_match:
                if current_lines:
                    sec_body = "\n".join(current_lines).strip()
                    if sec_body:
                        sections.append((current_header, sec_body))
                current_header = header_match.group(2).strip()
                current_lines = []
            else:
                current_lines.append(line)

        if current_lines:
            sec_body = "\n".join(current_lines).strip()
            if sec_body:
                sections.append((current_header, sec_body))

        if not sections and text.strip():
            sections.append(("", text.strip()))

        return sections

    def _sliding_window_chunks(self, text: str, prefix: str) -> list[str]:
        """지정된 크기와 오버랩으로 텍스트를 청킹."""
        effective_chunk_size = max(200, self.chunk_size - len(prefix))
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

        chunks: list[str] = []
        current_accum: list[str] = []
        current_len = 0

        for p in paragraphs:
            # 단일 문단이 청크 크기보다 긴 경우 문자 단위 분할
            if len(p) > effective_chunk_size:
                if current_accum:
                    chunks.append(prefix + "\n\n".join(current_accum))
                    current_accum = []
                    current_len = 0
                step = effective_chunk_size - self.chunk_overlap
                for start in range(0, len(p), step):
                    sub = p[start : start + effective_chunk_size]
                    chunks.append(prefix + sub)
                continue

            if current_len + len(p) > effective_chunk_size:
                if current_accum:
                    chunks.append(prefix + "\n\n".join(current_accum))
                    # 오버랩 보존
                    overlap_item = current_accum[-1] if len(current_accum[-1]) < self.chunk_overlap else ""
                    current_accum = [overlap_item] if overlap_item else []
                    current_len = len(overlap_item)

            current_accum.append(p)
            current_len += len(p) + 2

        if current_accum:
            chunks.append(prefix + "\n\n".join(current_accum))

        return [c.strip() for c in chunks if c.strip()]


def collect_documents(data_dir: Path) -> list[Path]:
    """재귀적으로 .md 및 .txt 파일 검색."""
    if not data_dir.exists():
        logger.warning("지정된 데이터 디렉터리가 존재하지 않습니다: %s", data_dir)
        return []

    files: list[Path] = []
    for ext in ("*.md", "*.txt"):
        files.extend(data_dir.rglob(ext))
    return sorted(files)


def run_bulk_indexing(
    data_dir: Path,
    collection_name: str,
    chunk_size: int,
    chunk_overlap: int,
    batch_size: int,
    apply: bool,
) -> None:
    files = collect_documents(data_dir)
    logger.info("발견된 대상 파일 수: %d건 (경로: %s)", len(files), data_dir)

    if not files:
        logger.info(
            "인덱싱할 파일이 없습니다. 가이드에 따라 data/ 디렉터리에 CS 지식 저장소를 복사하세요."
        )
        return

    chunker = MarkdownChunker(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    all_chunks: list[DocumentChunk] = []

    for f in files:
        chunks = chunker.chunk_file(f, base_dir=data_dir)
        all_chunks.extend(chunks)

    logger.info("총 생성된 청크 수: %d개", len(all_chunks))
    if not all_chunks:
        return

    # Dry-Run 모드
    if not apply:
        logger.info("[DRY-RUN 모드] 외부 벡터 DB 전송을 수행하지 않습니다. 미리보기:")
        for sample in all_chunks[:3]:
            print("=" * 60)
            print(f"Point ID: {sample.point_id}")
            print(f"Title   : {sample.title}")
            print(f"Category: {sample.category} | Topic: {sample.topic}")
            print(f"File    : {sample.source_file}")
            print("-" * 60)
            print(sample.content[:200] + ("..." if len(sample.content) > 200 else ""))
        print("=" * 60)
        logger.info("실제 Qdrant에 인덱싱하려면 '--apply' 플래그를 붙여 실행하세요.")
        return

    # 실제 임베딩 및 Qdrant 업로드
    from app.services.embedding_service import EmbeddingService
    from app.services.qdrant_service import QdrantService

    embed_service = EmbeddingService()
    qdrant_service = QdrantService()

    # Qdrant 연결 헬스 체크
    health = qdrant_service.health_check()
    if not health.get("reachable"):
        logger.error("Qdrant 서버에 연결할 수 없습니다 (%s). Qdrant URL: %s", health, settings.QDRANT_URL)
        return

    logger.info("Qdrant 연결 확인 완료. BGE-M3 임베딩 및 업로드를 시작합니다 (배치 크기: %d)...", batch_size)

    total_uploaded = 0
    total_chunks = len(all_chunks)

    for i in range(0, total_chunks, batch_size):
        batch = all_chunks[i : i + batch_size]
        texts = [b.content for b in batch]

        try:
            vectors = embed_service.embed_texts(texts)
        except Exception as exc:
            logger.error("임베딩 생성 오류 (배치 %d~%d): %s", i, i + len(batch), exc)
            continue

        if i == 0:
            dim = len(vectors[0])
            qdrant_service.ensure_collection(vector_size=dim, collection_name=collection_name)

        points = []
        for c, vec in zip(batch, vectors):
            points.append(
                {
                    "id": c.point_id,
                    "vector": vec,
                    "payload": {
                        "chunk_id": c.chunk_id,
                        "title": c.title,
                        "content": c.content,
                        "category": c.category,
                        "topic": c.topic,
                        "source_file": c.source_file,
                        "chunk_index": c.chunk_index,
                        "sourceType": "bulk_markdown",
                    },
                }
            )

        success = qdrant_service.upsert(points, collection_name=collection_name)
        if success:
            total_uploaded += len(points)
            logger.info("인덱싱 진행률: [%d / %d] (%.1f%%)", total_uploaded, total_chunks, (total_uploaded / total_chunks) * 100)
        else:
            logger.warning("배치 업로드 실패 (범위 %d~%d)", i, i + len(batch))

    logger.info("대량 인덱싱 완료! 총 %d개 청크가 컬렉션 '%s'에 성공적으로 반영되었습니다.", total_uploaded, collection_name)


def main() -> None:
    parser = argparse.ArgumentParser(description="실무 CS 지식 문서 대량 Qdrant 인덱서")
    parser.add_argument(
        "--data-dir",
        type=str,
        default="data/raw",
        help="인덱싱 대상 마크다운/텍스트 폴더 경로 (기본값: data/raw)",
    )
    parser.add_argument(
        "--collection",
        type=str,
        default=DEFAULT_COLLECTION,
        help=f"Qdrant 컬렉션 이름 (기본값: {DEFAULT_COLLECTION})",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=650,
        help="청크 문자 크기 (기본값: 650자)",
    )
    parser.add_argument(
        "--chunk-overlap",
        type=int,
        default=100,
        help="청크 간 오버랩 문자 수 (기본값: 100자)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=32,
        help="임베딩 및 업로드 배치 크기 (기본값: 32)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="실제 임베딩 및 업로드 없이 청킹 통계 및 샘플만 확인",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="실제 Qdrant 컬렉션에 임베딩 및 upsert 수행",
    )

    args = parser.parse_args()
    data_path = Path(args.data_dir)

    run_bulk_indexing(
        data_dir=data_path,
        collection_name=args.collection,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
        batch_size=args.batch_size,
        apply=args.apply and not args.dry_run,
    )


if __name__ == "__main__":
    main()
