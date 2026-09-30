# Docker Compose: 여러 서비스 정의, depends_on, healthcheck, 환경변수와 compose 명령어

## 개념

### Docker Compose란
Docker Compose는 여러 컨테이너(서비스)의 이미지, 포트, 환경변수, 볼륨, 네트워크, 의존 관계를 하나의 YAML 파일(`compose.yaml` 또는 `docker-compose.yml`)에 정의하고 한 번에 실행/관리하는 도구다. 현재는 Docker CLI 플러그인인 Compose V2(`docker compose`, 띄어쓰기)를 사용한다. 구버전은 `docker-compose`(하이픈) 독립 실행 파일이다.

### compose.yaml 구성 요소
```yaml
services:
  api:
    build: .                       # 현재 디렉터리 Dockerfile로 빌드
    image: cobip-api:local
    ports:
      - "8080:8080"                # 호스트:컨테이너
    environment:
      SPRING_PROFILES_ACTIVE: local
      DB_HOST: db                  # 서비스 이름으로 접속
      DB_PORT: "5432"              # 컨테이너 내부 포트
      REDIS_URL: redis://redis:6379
    env_file:
      - .env
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_started
    restart: unless-stopped

  db:
    image: postgres:16
    environment:
      POSTGRES_DB: app
      POSTGRES_USER: app
      POSTGRES_PASSWORD: ${DB_PASSWORD}   # .env 파일 값으로 치환
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U app -d app"]
      interval: 5s
      timeout: 3s
      retries: 10

  redis:
    image: redis:7

volumes:
  pgdata:
```
- `services`: 실행할 컨테이너 정의. 각 서비스 이름(`api`, `db`, `redis`)은 Compose 네트워크 안에서 DNS 이름(호스트명)이 된다.
- `build` vs `image`: `build`는 Dockerfile로 이미지를 빌드, `image`는 기존 이미지를 사용(둘 다 쓰면 빌드 결과에 그 이름을 붙임).
- `ports`: 호스트에 공개할 포트. 서비스 간 통신에는 필요 없다.
- `environment` / `env_file`: 컨테이너에 들어가는 환경변수.
- 프로젝트 디렉터리의 `.env` 파일은 compose 파일 안의 `${변수}` 치환에 사용된다.
- `volumes`: 영속 데이터(named volume)나 바인드 마운트.
- `depends_on`: 시작 순서를 정한다. 기본은 "컨테이너 시작"만 기다리며 애플리케이션 준비 완료는 보장하지 않는다. `condition: service_healthy`와 대상 서비스의 `healthcheck`를 함께 써야 준비 완료 후 시작한다.
- `healthcheck`: 컨테이너 상태를 `healthy`/`unhealthy`로 표시하는 검사 명령.

### Compose 네트워크 동작
Compose는 프로젝트마다 기본 네트워크(`<프로젝트명>_default`)를 만들고 모든 서비스를 연결한다. 서비스끼리는 `서비스이름:컨테이너포트`로 통신한다(예: `db:5432`, `redis:6379`, `qdrant:6333`). 호스트 포트 매핑과 무관하게 컨테이너 포트를 사용한다.

## 왜 필요한가

### Compose를 쓰는 이유
- `docker run` 명령을 서비스마다 길게 입력하지 않고, 설정을 파일로 버전 관리할 수 있다.
- 개발자 모두가 같은 로컬 환경(API + DB + Redis + Qdrant)을 `docker compose up` 한 번으로 재현한다.
- 단일 EC2 서버 배포에서도 여러 컨테이너를 간단하게 운영할 수 있다.
- 네트워크와 서비스 이름 기반 DNS를 자동 구성해 컨테이너 간 통신이 쉬워진다.

## 핵심 포인트

### Compose 명령어
```bash
docker compose up -d                 # 백그라운드로 모든 서비스 생성/실행
docker compose up -d --build         # 이미지를 다시 빌드한 뒤 실행(변경된 서비스 재생성)
docker compose up -d api             # 특정 서비스만 실행
docker compose ps                    # 서비스별 컨테이너 상태, 포트, health
docker compose logs -f api           # 특정 서비스 로그 실시간
docker compose logs --tail 100       # 전체 서비스 최근 로그
docker compose build                 # build가 정의된 서비스 이미지 빌드
docker compose build --no-cache api  # 캐시 없이 빌드
docker compose restart api           # 서비스 컨테이너 재시작(설정 변경은 반영 안 됨)
docker compose exec api sh           # 실행 중인 서비스 컨테이너 셸 접속
docker compose pull                  # image로 지정된 최신 이미지 받기
docker compose config                # 변수 치환이 적용된 최종 설정 확인
docker compose down                  # 컨테이너와 기본 네트워크 삭제(named volume 유지)
docker compose down -v               # named volume까지 삭제(DB 데이터 삭제 주의)
```
- `docker compose restart`는 기존 컨테이너를 재시작만 한다. compose 파일, 환경변수, 이미지 변경을 반영하려면 `docker compose up -d`(필요하면 `--build`)로 재생성해야 한다.
- `docker compose down`은 기본적으로 볼륨을 삭제하지 않는다. `-v`를 붙이면 DB 데이터가 사라진다.
- `docker compose stop`은 중지만 하고 컨테이너를 남기며, `down`은 컨테이너와 네트워크를 삭제한다.

## 실무 상황

### 로컬 개발 환경
FastAPI + Qdrant + Redis를 Compose로 띄우고, FastAPI 설정은 `QDRANT_URL=http://qdrant:6333`, `REDIS_URL=redis://redis:6379/0`처럼 서비스 이름을 사용한다. 호스트 PC의 브라우저나 curl에서는 `http://localhost:8000`처럼 매핑된 호스트 포트로 접근한다.

### 단일 EC2 서버 배포
```bash
cd /opt/cobip
aws ecr get-login-password --region ap-northeast-2 \
  | docker login --username AWS --password-stdin 123456789012.dkr.ecr.ap-northeast-2.amazonaws.com
docker compose pull
docker compose up -d
docker compose ps
docker compose logs --tail 100 api
curl -fsS http://localhost:8080/actuator/health
```
compose 파일의 `image:`에 ECR 이미지 주소와 태그(예: `${IMAGE_TAG}`)를 지정하고 배포 시 태그만 바꾼다.

## 장애/문제 상황

### 사례: api 서비스가 DB에 연결하지 못함
- `DB_HOST=localhost`로 설정: 컨테이너 안의 localhost는 api 컨테이너 자신이므로 실패한다. `DB_HOST=db`(서비스 이름)를 사용한다.
- `db:15432`처럼 호스트에 매핑한 포트를 사용: Compose 네트워크 안에서는 컨테이너 포트(`db:5432`)를 사용해야 한다.
- DB 초기화가 끝나기 전에 api가 시작: `depends_on`만으로는 준비 완료를 기다리지 않는다. `healthcheck` + `condition: service_healthy`와 애플리케이션 재시도 로직을 사용한다.
- 서비스가 서로 다른 네트워크에 있음: `networks` 설정을 확인한다.

### 사례: 설정을 바꿨는데 반영되지 않음
`docker compose restart`만 실행했다. `docker compose up -d`로 컨테이너를 재생성한다. 이미지 소스가 바뀌었다면 `docker compose up -d --build`.

### 사례: `docker compose down -v` 후 DB 데이터가 사라짐
`-v` 옵션은 named volume을 삭제한다. 운영 환경에서는 사용하지 않는다.

### 사례: 변수 치환 경고
`WARN The "DB_PASSWORD" variable is not set. Defaulting to a blank string.`: 프로젝트 디렉터리에 `.env`가 없거나 변수명이 다르다. `docker compose config`로 확인한다.

## 확인 방법

### Compose 점검 순서
```bash
docker compose ps                      # 상태(running/exited/restarting), health, 포트
docker compose logs --tail 200 api     # 오류 로그
docker compose config                  # 최종 설정(환경변수 치환 결과)
docker compose exec api env            # 컨테이너 환경변수
docker compose exec api getent hosts db   # 서비스 이름 DNS 해석 확인
docker network ls                      # <프로젝트>_default 네트워크 존재 확인
```

## 핵심 정리

### 퀴즈 출제용 핵심 사실
- Docker Compose는 여러 컨테이너 서비스를 YAML 파일 하나로 정의하고 관리하는 도구다.
- Compose 서비스끼리는 같은 기본 네트워크에서 서비스 이름으로 통신한다(예: `db:5432`).
- 서비스 간 통신에는 `ports` 매핑이 필요 없고, 컨테이너 포트를 사용한다.
- `depends_on`은 시작 순서만 보장하며, 준비 완료를 기다리려면 `healthcheck`와 `condition: service_healthy`를 사용한다.
- `docker compose up -d`는 서비스 생성/실행, `docker compose down`은 컨테이너와 네트워크 삭제, `down -v`는 볼륨까지 삭제한다.
- `docker compose logs -f <서비스>`로 서비스 로그를 실시간 확인한다.
- `docker compose restart`는 설정 변경을 반영하지 않으며, 변경 반영은 `docker compose up -d`로 한다.
- `docker compose build`는 build가 정의된 서비스의 이미지를 빌드한다.
