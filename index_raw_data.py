"""data/raw/ 폴더의 마크다운 파일들을 Qdrant에 자동 인덱싱하는 스크립트."""

import hashlib
import logging
import os
import sys
from pathlib import Path
from typing import Any

# 프로젝트 루트를 PYTHONPATH에 추가
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from app.services.embedding_service import EmbeddingService
from app.services.qdrant_service import QdrantService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def get_file_metadata(file_path: Path) -> dict[str, Any]:
    """파일 경로에서 메타데이터를 추출한다."""
    relative_path = file_path.relative_to(project_root / "data" / "raw")
    parts = relative_path.parts
    
    metadata = {
        "source": "local_markdown",
        "path": str(relative_path),
        "fileName": file_path.name,
    }
    
    # 카테고리 추출 (상위 폴더)
    if len(parts) >= 2:
        metadata["category"] = parts[0]
        metadata["topic"] = parts[0]
    
    # 세부 토픽 추출 (하위 폴더)
    if len(parts) >= 3:
        metadata["subtopic"] = parts[1]
    
    return metadata


def read_markdown_file(file_path: Path) -> str:
    """마크다운 파일 내용을 읽는다."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        logger.error(f"파일 읽기 실패: {file_path} - {e}")
        return ""


def generate_document_id(file_path: Path, content: str, chunk_idx: int = 0) -> int:
    """파일 경로와 내용 기반으로 고유 ID를 생성한다."""
    # 파일 경로와 내용을 조합하여 해시 생성
    path_str = str(file_path.relative_to(project_root))
    combined = f"{path_str}:{len(content)}:{chunk_idx}"
    hash_value = hashlib.md5(combined.encode("utf-8")).hexdigest()
    # 해시의 앞 16자를 정수로 변환 (Qdrant PointStruct ID 호환)
    return int(hash_value[:16], 16)


def chunk_content(content: str, max_chars: int = 1000) -> list[str]:
    """내용을 적절한 크기로 청킹한다."""
    if len(content) <= max_chars:
        return [content]
    
    chunks = []
    current_chunk = ""
    
    # 문장 단위로 분할 (마침표, 줄바꿈 기준)
    sentences = []
    for line in content.split("\n"):
        if line.strip():
            sentences.append(line)
        else:
            sentences.append("")  # 빈 줄 유지
    
    for sentence in sentences:
        if len(current_chunk) + len(sentence) <= max_chars:
            current_chunk += sentence + "\n"
        else:
            if current_chunk.strip():
                chunks.append(current_chunk.strip())
            current_chunk = sentence + "\n"
    
    if current_chunk.strip():
        chunks.append(current_chunk.strip())
    
    return chunks


def index_markdown_files():
    """data/raw/ 폴더의 모든 마크다운 파일을 인덱싱한다."""
    raw_data_path = project_root / "data" / "raw"
    
    if not raw_data_path.exists():
        logger.error(f"data/raw/ 폴더를 찾을 수 없습니다: {raw_data_path}")
        return
    
    # 서비스 초기화
    qdrant_service = QdrantService()
    embedding_service = EmbeddingService()
    
    # 컬렉션 확인 및 생성
    collection_name = "cobip_knowledge"
    if not qdrant_service.collection_exists(collection_name):
        logger.info(f"컬렉션 생성 중: {collection_name}")
        # 임베딩 모델의 벡터 크기 확인
        try:
            test_embedding = embedding_service.embed_text("test")
            vector_size = len(test_embedding)
            qdrant_service.ensure_collection(vector_size, collection_name)
            logger.info(f"컬렉션 생성 완료 (vector_size={vector_size})")
        except Exception as e:
            logger.error(f"컬렉션 생성 실패: {e}")
            return
    else:
        logger.info(f"컬렉션 이미 존재함: {collection_name}")
    
    # 모든 마크다운 파일 찾기
    md_files = list(raw_data_path.rglob("*.md"))
    logger.info(f"찾은 마크다운 파일 수: {len(md_files)}")
    
    if not md_files:
        logger.warning("인덱싱할 마크다운 파일이 없습니다.")
        return
    
    # 인덱싱 진행
    indexed_count = 0
    failed_count = 0
    
    for md_file in md_files:
        try:
            logger.info(f"파일 처리 중: {md_file.relative_to(project_root)}")
            
            # 파일 내용 읽기
            content = read_markdown_file(md_file)
            if not content:
                logger.warning(f"파일 내용이 비어있음: {md_file}")
                failed_count += 1
                continue
            
            # 메타데이터 추출
            metadata = get_file_metadata(md_file)
            metadata["title"] = md_file.stem  # 파일 확장자 제거한 이름
            
            # 내용 청킹
            chunks = chunk_content(content, max_chars=1000)
            logger.info(f"  - 청크 수: {len(chunks)}")
            
            # 각 청크를 별도 포인트로 인덱싱
            points = []
            for chunk_idx, chunk in enumerate(chunks):
                # 고유 ID 생성 (정수형)
                point_id = generate_document_id(md_file, chunk, chunk_idx)
                
                # 임베딩 생성
                try:
                    embedding = embedding_service.embed_text(chunk)
                except Exception as e:
                    logger.error(f"임베딩 생성 실패: {md_file} chunk {chunk_idx} - {e}")
                    continue
                
                # 포인트 생성
                point_metadata = metadata.copy()
                point_metadata["chunkIndex"] = chunk_idx
                point_metadata["totalChunks"] = len(chunks)
                point_metadata["contentPreview"] = chunk[:200] + "..." if len(chunk) > 200 else chunk
                
                points.append({
                    "id": point_id,
                    "vector": embedding,
                    "payload": point_metadata
                })
            
            # Qdrant에 upsert
            if points:
                success = qdrant_service.upsert(points, collection_name)
                if success:
                    indexed_count += 1
                    logger.info(f"  - 인덱싱 성공: {len(points)} 포인트")
                else:
                    failed_count += 1
                    logger.error(f"  - 인덱싱 실패: {md_file}")
            else:
                failed_count += 1
                logger.warning(f"  - 인덱싱할 포인트 없음: {md_file}")
                
        except Exception as e:
            logger.error(f"파일 처리 중 오류: {md_file} - {e}")
            failed_count += 1
    
    # 결과 요약
    logger.info("=" * 50)
    logger.info("인덱싱 완료")
    logger.info(f"성공: {indexed_count} 파일")
    logger.info(f"실패: {failed_count} 파일")
    logger.info(f"총 파일: {len(md_files)}")
    logger.info("=" * 50)


if __name__ == "__main__":
    try:
        index_markdown_files()
    except KeyboardInterrupt:
        logger.info("사용자에 의해 중단됨")
    except Exception as e:
        logger.error(f"치명적 오류: {e}", exc_info=True)
        sys.exit(1)