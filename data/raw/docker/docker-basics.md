# Docker 기본 개념: Image, Container, Dockerfile, Volume, 환경변수, Container Lifecycle

## 개념

### Docker란 무엇인가
Docker는 애플리케이션과 실행에 필요한 런타임, 라이브러리, 설정을 이미지로 묶고, 그 이미지를 격리된 프로세스(컨테이너)로 실행하는 플랫폼이다. 컨테이너는 호스트 OS 커널을 공유하며 리눅스 namespace(격리)와 cgroup(자원 제한)으로 분리된다. 가상 머신(VM)처럼 게스트 OS 전체를 띄우지 않으므로 가볍고 빠르게 시작된다.

### Image (이미지)
- 컨테이너를 만들기 위한 읽기 전용 템플릿이다. 여러 레이어(layer)로 구성된다.
- `이름:태그` 형식으로 식별한다. 예: `python:3.12-slim`, `cobip-api:1.0.0`. 태그를 생략하면 `latest`가 사용된다.
- Dockerfile로 빌드(`docker build`)하거나 레지스트리(Docker Hub, ECR)에서 pull한다.

### Container (컨테이너)
- 이미지를 실행한 인스턴스다. 이미지의 읽기 전용 레이어 위에 쓰기 가능한 레이어를 추가하고 프로세스를 실행한다.
- 하나의 이미지로 여러 컨테이너를 동시에 실행할 수 있다.
- 컨테이너 안에서 변경한 파일은 해당 컨테이너의 쓰기 레이어에만 존재하며, 컨테이너를 삭제(`docker rm`)하면 사라진다.
- 컨테이너는 메인 프로세스(PID 1, `CMD`/`ENTRYPOINT`로 실행한 프로세스)가 살아 있는 동안만 실행 상태다. 메인 프로세스가 종료되면 컨테이너도 `Exited`가 된다.

### Image와 Container의 차이
| 구분 | Image | Container |
| --- | --- | --- |
| 성격 | 읽기 전용 템플릿(설계도) | 이미지를 실행한 프로세스(실행 인스턴스) |
| 상태 | 변하지 않음 | 생성/실행/중지/삭제 상태를 가짐 |
| 저장 | 레지스트리에 push/pull | 호스트에서 실행, 레지스트리에 올리지 않음 |
| 관련 명령 | `docker build`, `docker images`, `docker pull`, `docker push` | `docker run`, `docker ps`, `docker stop`, `docker rm` |
비유: 이미지는 클래스, 컨테이너는 그 클래스로 만든 객체에 가깝다.

### Dockerfile
이미지를 만드는 절차를 적은 텍스트 파일이다.
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV APP_ENV=prod
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```
- `FROM`: 베이스 이미지. `WORKDIR`: 작업 디렉터리. `COPY`: 파일 복사. `RUN`: 빌드 시 명령 실행(레이어 생성). `ENV`: 환경변수 기본값. `ARG`: 빌드 시에만 쓰는 변수.
- `EXPOSE`: 컨테이너가 사용하는 포트를 문서화할 뿐, 실제로 호스트에 포트를 공개하지 않는다. 공개는 `docker run -p`가 한다.
- `CMD`: 컨테이너 시작 시 기본 명령(실행 인자로 덮어쓸 수 있음). `ENTRYPOINT`: 항상 실행할 실행 파일, `CMD`는 그 기본 인자로 쓰인다.
- 레이어 캐시: 변경이 적은 의존성 파일(`requirements.txt`, `build.gradle`)을 먼저 복사하고 설치하면 소스만 바뀔 때 빌드가 빨라진다.
- 멀티 스테이지 빌드: 빌드 도구가 있는 이미지에서 JAR를 만들고, 실행용 이미지(`eclipse-temurin:21-jre`)에는 결과물만 복사해 이미지 크기를 줄인다.
- `.dockerignore`: `.git`, `node_modules`, `.env`, 빌드 산출물 등을 빌드 컨텍스트에서 제외한다.

### Volume (볼륨)
컨테이너가 삭제되어도 데이터를 유지하기 위한 저장소다.
- Named volume: Docker가 관리하는 저장소(`-v pgdata:/var/lib/postgresql/data`). DB 데이터에 사용.
- Bind mount: 호스트 디렉터리를 그대로 마운트(`-v /opt/app/config:/app/config` 또는 `-v $(pwd):/app`). 설정 파일, 개발 중 소스 공유에 사용.
- tmpfs: 메모리에만 저장.

### Environment Variable (환경변수)
같은 이미지를 개발/운영 환경에 따라 다르게 설정하기 위해 사용한다.
- 실행 시 주입: `docker run -e DB_HOST=db -e DB_PORT=5432 ...` 또는 `--env-file .env`.
- Dockerfile의 `ENV`는 기본값이며 실행 시 `-e`로 덮어쓸 수 있다.
- 비밀번호·API 키는 이미지에 넣지 않는다(`docker history`, `docker inspect`로 노출될 수 있음). 실행 시 주입하거나 Secrets Manager 등을 사용한다.

### Container Lifecycle (컨테이너 생명주기)
`Created`(생성) → `Running`(실행) → `Paused`(일시정지, 선택) → `Exited`(중지) → 삭제(`docker rm`).
- `docker run` = 이미지 pull(없을 때) + `create` + `start`.
- `docker stop`: SIGTERM을 보내고 기본 10초 후에도 종료되지 않으면 SIGKILL을 보낸다.
- `docker kill`: 즉시 SIGKILL.
- `docker start`: 중지된 컨테이너를 같은 설정으로 다시 시작(쓰기 레이어 유지).
- `docker rm`: 중지된 컨테이너 삭제(`-f`는 실행 중이어도 강제 삭제).
- 재시작 정책(`--restart`): `no`(기본), `on-failure[:횟수]`, `always`, `unless-stopped`.

## 왜 필요한가

### Docker를 쓰는 이유
- "내 PC에서는 되는데 서버에서는 안 된다" 문제를 줄인다. 런타임 버전과 의존성이 이미지에 고정된다.
- 같은 이미지를 개발, 테스트, 운영에서 그대로 사용하므로 CI/CD에서 빌드 산출물로 쓰기 좋다.
- 여러 서비스(API, DB, Redis)를 서로 격리해 한 서버에서 실행할 수 있다.
- 배포와 롤백이 "이미지 태그 교체"로 단순해진다.

## 핵심 포인트

### Docker 기본 핵심
- 이미지는 읽기 전용 템플릿, 컨테이너는 이미지를 실행한 인스턴스다.
- 컨테이너는 메인 프로세스가 끝나면 종료된다. 백그라운드 데몬으로 실행되는 프로그램을 `CMD`로 쓰면 바로 `Exited (0)`이 될 수 있다.
- 컨테이너 쓰기 레이어의 데이터는 컨테이너 삭제 시 사라지므로 영속 데이터는 Volume을 사용한다.
- `EXPOSE`는 문서화, 실제 포트 공개는 `-p 호스트포트:컨테이너포트`.
- 서버 애플리케이션은 컨테이너 안에서 `0.0.0.0`에 바인딩해야 포트 매핑으로 들어온 요청을 받을 수 있다.
- 비밀 값은 이미지에 굽지 말고 실행 시 환경변수로 주입한다.

## 실무 상황

### Spring Boot 멀티 스테이지 Dockerfile 예
```dockerfile
FROM eclipse-temurin:21-jdk AS build
WORKDIR /workspace
COPY . .
RUN ./gradlew bootJar --no-daemon

FROM eclipse-temurin:21-jre
WORKDIR /app
COPY --from=build /workspace/build/libs/*.jar app.jar
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "/app/app.jar"]
```
```bash
docker build -t cobip-api:1.0.0 .
docker run -d --name cobip-api -p 8080:8080 \
  -e SPRING_PROFILES_ACTIVE=prod --env-file ./prod.env cobip-api:1.0.0
```

### DB 데이터 보존
```bash
docker volume create pgdata
docker run -d --name db -e POSTGRES_PASSWORD=secret -v pgdata:/var/lib/postgresql/data postgres:16
```
`db` 컨테이너를 삭제 후 다시 만들어도 `pgdata` 볼륨을 연결하면 데이터가 유지된다.

## 장애/문제 상황

### 자주 발생하는 기본 문제
- 컨테이너를 재생성했더니 DB 데이터가 사라짐: Volume 없이 컨테이너 쓰기 레이어에 저장했다.
- 이미지를 다시 빌드했는데 변경이 반영되지 않음: 기존 컨테이너를 그대로 `docker start`했다. 새 이미지로 컨테이너를 다시 생성해야 한다.
- 이미지 크기가 수 GB: 빌드 도구, 캐시, `.git`이 포함됨 → 멀티 스테이지, slim 베이스, `.dockerignore`.
- `.env`의 비밀번호가 이미지에 포함됨: `COPY . .` 시 `.dockerignore`에 `.env`를 넣지 않았다.

## 확인 방법

### 기본 확인 명령어
```bash
docker images                     # 로컬 이미지 목록
docker ps -a                      # 전체 컨테이너와 상태
docker history cobip-api:1.0.0    # 이미지 레이어 확인
docker volume ls                  # 볼륨 목록
docker inspect -f '{{json .Config.Env}}' cobip-api   # 컨테이너 환경변수 확인
docker inspect -f '{{json .Mounts}}' db              # 마운트된 볼륨 확인
```

## 핵심 정리

### 퀴즈 출제용 핵심 사실
- Docker Image는 읽기 전용 템플릿이고 Container는 이미지를 실행한 인스턴스다. 둘은 같은 개념이 아니다.
- 하나의 이미지로 여러 컨테이너를 실행할 수 있다.
- Dockerfile의 `EXPOSE`는 포트를 문서화할 뿐이며, 호스트 공개는 `docker run -p`로 한다.
- `CMD`는 덮어쓸 수 있는 기본 명령, `ENTRYPOINT`는 고정 실행 파일이다.
- 컨테이너 삭제 시 쓰기 레이어 데이터는 사라지므로 영속 데이터는 Volume에 저장한다.
- 환경변수는 `-e` 또는 `--env-file`로 주입하며 비밀 값은 이미지에 포함하지 않는다.
- `docker stop`은 SIGTERM 후 유예 시간(기본 10초)이 지나면 SIGKILL을 보낸다.
- 재시작 정책에는 `no`, `on-failure`, `always`, `unless-stopped`가 있다.
