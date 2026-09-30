# Docker 명령어 실무 가이드: build, run, ps, logs, exec, stop, start, restart, rm, images, inspect

## 개념

### Docker CLI 명령어 구조
Docker 명령은 `docker <대상> <동작>` 또는 축약형 `docker <동작>`으로 실행한다. 예: `docker container ls` = `docker ps`, `docker image ls` = `docker images`. 컨테이너는 이름(`--name`) 또는 ID(앞 몇 자리만 써도 됨)로 지정한다.

## 왜 필요한가

### 명령어를 정확히 알아야 하는 이유
장애 대응은 대부분 "상태 확인(`docker ps -a`) → 로그 확인(`docker logs`) → 내부 확인(`docker exec`) → 설정 확인(`docker inspect`) → 재시작/재생성" 순서로 진행된다. 각 명령이 무엇을 보여주고 무엇을 바꾸는지 구분하지 못하면 데이터를 잃거나(`docker rm`, `-v`) 원인을 놓친다.

## 핵심 포인트

### 이미지 관련 명령어
현재 디렉터리의 Dockerfile로 이미지 빌드, -t로 이름:태그 지정
```bash
docker build -t cobip-api:1.0.0 .
```
다른 Dockerfile 경로와 대상 플랫폼 지정 (Apple Silicon에서 x86 서버용 빌드)
```bash
docker build -f docker/Dockerfile --platform linux/amd64 -t cobip-api:1.0.0 .
```
로컬 이미지 목록 (REPOSITORY, TAG, IMAGE ID, SIZE)
```bash
docker images
```
레지스트리용 태그 추가 후 push
```bash
docker tag cobip-api:1.0.0 123456789012.dkr.ecr.ap-northeast-2.amazonaws.com/cobip-api:1.0.0
docker push 123456789012.dkr.ecr.ap-northeast-2.amazonaws.com/cobip-api:1.0.0
```
이미지 삭제, 사용되지 않는 이미지 정리
```bash
docker rmi cobip-api:0.9.0
docker image prune -a
```
- `docker build`의 마지막 `.`은 빌드 컨텍스트(Docker 데몬에 보내는 파일 범위)이다.

### docker run (컨테이너 생성 + 실행)
```bash
docker run -d --name cobip-api \
  -p 8080:8080 \
  -e SPRING_PROFILES_ACTIVE=prod \
  --env-file ./prod.env \
  -v /opt/cobip/logs:/app/logs \
  --network cobip-net \
  --restart unless-stopped \
  cobip-api:1.0.0
```
| 옵션 | 의미 |
| --- | --- |
| `-d` | 백그라운드(detached) 실행 |
| `--name` | 컨테이너 이름 지정 |
| `-p 호스트:컨테이너` | 포트 매핑(호스트 8080 → 컨테이너 8080) |
| `-e`, `--env-file` | 환경변수 주입 |
| `-v` | 볼륨/바인드 마운트 |
| `--network` | 연결할 Docker 네트워크 |
| `--restart` | 재시작 정책 |
| `--rm` | 종료 시 컨테이너 자동 삭제(일회성 작업) |
| `-it` | 대화형 터미널(interactive + tty) |

### 상태/로그/내부 확인 명령어
```bash
docker ps              # 실행 중인 컨테이너만
docker ps -a           # 중지(Exited)된 컨테이너 포함 전체

docker logs cobip-api                 # 전체 로그(stdout/stderr)
docker logs -f cobip-api              # 실시간 추적(follow)
docker logs --tail 100 cobip-api      # 마지막 100줄
docker logs --since 10m cobip-api     # 최근 10분
docker logs -t cobip-api              # 타임스탬프 포함

docker exec -it cobip-api sh          # 실행 중인 컨테이너에 셸 접속 (bash가 없는 이미지는 sh)
docker exec cobip-api env             # 컨테이너 환경변수 출력
docker exec cobip-api curl -s localhost:8080/actuator/health   # 컨테이너 내부에서 요청(이미지에 curl이 있을 때)

docker inspect cobip-api              # 상태, 설정, 네트워크, 마운트를 JSON으로
docker inspect -f '{{.State.Status}} {{.State.ExitCode}} {{.State.OOMKilled}}' cobip-api
docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' cobip-api
docker port cobip-api                 # 포트 매핑 확인
docker stats --no-stream              # CPU/메모리 사용량
```
- `docker logs`는 컨테이너 메인 프로세스의 stdout/stderr만 보여준다. 애플리케이션이 파일에만 로그를 쓰면 `docker logs`에 나오지 않는다.
- `docker logs`는 중지된(Exited) 컨테이너에도 사용할 수 있다. 반면 `docker exec`는 실행 중인 컨테이너에서만 가능하다.

### 생명주기 제어 명령어
```bash
docker stop cobip-api        # SIGTERM → 유예(기본 10초) 후 SIGKILL, 상태 Exited
docker stop -t 30 cobip-api  # 유예 시간 30초
docker start cobip-api       # 중지된 컨테이너를 같은 설정으로 다시 실행
docker restart cobip-api     # stop 후 start
docker rm cobip-api          # 중지된 컨테이너 삭제
docker rm -f cobip-api       # 실행 중이어도 강제 삭제
docker container prune       # 중지된 컨테이너 일괄 삭제
docker system df             # 이미지/컨테이너/볼륨 디스크 사용량
```

### 자주 헷갈리는 명령 비교
- `docker run` vs `docker start`: `run`은 이미지로 새 컨테이너를 만들고 실행, `start`는 이미 있는 중지된 컨테이너를 다시 실행한다. 새 이미지나 새 환경변수를 반영하려면 `start`가 아니라 삭제 후 `run`으로 재생성해야 한다.
- `docker stop` vs `docker rm`: `stop`은 중지(컨테이너와 쓰기 레이어 유지), `rm`은 삭제.
- `docker rm` vs `docker rmi`: `rm`은 컨테이너 삭제, `rmi`는 이미지 삭제.
- `docker ps` vs `docker images`: `ps`는 컨테이너 목록, `images`는 이미지 목록.
- `docker exec` vs `docker run`: `exec`는 실행 중인 컨테이너 안에서 추가 명령 실행, `run`은 새 컨테이너 생성.

## 실무 상황

### 새 버전으로 컨테이너 교체 배포
```bash
docker pull 123456789012.dkr.ecr.ap-northeast-2.amazonaws.com/cobip-api:1.1.0
docker stop cobip-api && docker rm cobip-api
docker run -d --name cobip-api -p 8080:8080 --env-file /opt/cobip/.env \
  --restart unless-stopped 123456789012.dkr.ecr.ap-northeast-2.amazonaws.com/cobip-api:1.1.0
docker ps --filter name=cobip-api
docker logs --tail 50 cobip-api
curl -fsS http://localhost:8080/actuator/health
```

### 일회성 작업 실행
DB 마이그레이션 같은 일회성 작업을 실행 후 자동 삭제
```bash
docker run --rm --env-file /opt/cobip/.env cobip-api:1.1.0 python -m app.migrate
```

## 장애/문제 상황

### 명령어 사용 중 자주 보는 오류
- `Conflict. The container name "/cobip-api" is already in use`: 같은 이름의 컨테이너(중지 상태 포함)가 있음 → `docker rm cobip-api` 후 다시 실행.
- `Bind for 0.0.0.0:8080 failed: port is already allocated`: 호스트 8080을 다른 컨테이너/프로세스가 사용 중 → `docker ps`, `sudo ss -tlnp | grep 8080`.
- `Error response from daemon: Container ... is not running`: 중지된 컨테이너에 `docker exec` 시도 → `docker logs`로 종료 원인 확인.
- `OCI runtime exec failed: exec: "bash": executable file not found`: 이미지에 bash가 없음 → `sh` 사용.
- `permission denied while trying to connect to the Docker daemon socket`: 현재 사용자가 `docker` 그룹이 아님 → `sudo usermod -aG docker $USER` 후 재로그인.
- `Cannot connect to the Docker daemon`: Docker 서비스 미실행 → `sudo systemctl start docker`.

## 확인 방법

### 장애 확인 명령 순서
1. `docker ps -a` — 상태(Up, Exited (코드), Restarting)와 PORTS 확인.
2. `docker logs --tail 200 <컨테이너>` — 예외, 설정 오류, 연결 실패 메시지 확인.
3. `docker inspect -f '{{.State.ExitCode}} {{.State.OOMKilled}}' <컨테이너>` — 종료 코드와 OOM 여부.
4. `docker exec -it <컨테이너> sh` — 파일, 환경변수(`env`), 내부 네트워크(`curl`, `nslookup`) 확인.
5. `docker port <컨테이너>` / `docker inspect` — 포트 매핑, 네트워크, 마운트 확인.
6. 설정 수정 후 `docker rm -f` + `docker run`으로 재생성.

## 핵심 정리

### 퀴즈 출제용 핵심 사실
- 컨테이너 로그 확인 명령어는 `docker logs`, 실시간 추적은 `docker logs -f`이다.
- 실행 중인 컨테이너 내부에서 명령을 실행하거나 셸에 접속하는 명령어는 `docker exec -it <컨테이너> sh`이다.
- `docker ps`는 실행 중인 컨테이너만, `docker ps -a`는 중지된 컨테이너까지 보여준다.
- `docker images`는 로컬 이미지 목록을 보여준다.
- `docker inspect`는 컨테이너/이미지의 상세 설정과 상태를 JSON으로 보여준다.
- `docker build -t 이름:태그 .`로 이미지를 빌드한다.
- `docker run -p 8080:80`은 호스트 8080 포트를 컨테이너 80 포트로 연결한다.
- `docker stop`은 컨테이너를 중지하고, `docker rm`은 컨테이너를 삭제하며, `docker rmi`는 이미지를 삭제한다.
- `docker start`는 기존 컨테이너를 재실행하므로 새 이미지/환경변수는 반영되지 않는다.
