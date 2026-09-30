# Docker 장애 대응: Exited 컨테이너, 재시작 반복, 접속 불가, docker logs와 docker exec 활용

## 개념

### Docker 장애 대응의 기본 원리
컨테이너 장애는 "컨테이너가 떠 있는가 → 왜 종료됐는가 → 네트워크/포트가 맞는가 → 설정(환경변수, 볼륨)이 맞는가" 순서로 좁혀간다. 컨테이너는 메인 프로세스가 끝나면 종료되므로, 종료 코드와 로그가 가장 중요한 단서다.

### 종료 코드(Exit Code) 해석
| 종료 코드 | 의미 | 대표 원인 |
| --- | --- | --- |
| 0 | 정상 종료 | 메인 프로세스가 할 일을 끝냄, 포그라운드 프로세스가 아님(`nginx`를 데몬 모드로 실행 등) |
| 1 | 애플리케이션 오류 | 예외, 설정/환경변수 누락, DB 연결 실패 |
| 125 | docker run 자체 실패 | 잘못된 옵션, 포트 충돌 등 |
| 126 | 명령 실행 불가 | 실행 권한 없음 |
| 127 | 명령을 찾을 수 없음 | `CMD` 경로/오타, 이미지에 해당 실행 파일 없음 |
| 137 | SIGKILL(128+9) | 메모리 초과(OOMKilled), `docker kill`, stop 유예 시간 초과 |
| 143 | SIGTERM(128+15) | `docker stop` 등으로 정상 종료 신호 수신 |

## 왜 필요한가

### 체계적인 확인 순서가 필요한 이유
재시작만 반복하면 원인이 남아 있어 같은 장애가 재발한다. `docker ps -a`, `docker logs`, `docker inspect`, `docker exec`로 원인을 확인하면 설정 문제인지, 코드 문제인지, 인프라(메모리/디스크/네트워크) 문제인지 빠르게 구분할 수 있다.

## 핵심 포인트

### 장애 대응 핵심
- 상태 확인은 `docker ps -a`(중지된 컨테이너까지 표시).
- 원인 확인은 `docker logs`(Exited 컨테이너에도 사용 가능).
- 종료 코드/OOM 확인은 `docker inspect -f '{{.State.ExitCode}} {{.State.OOMKilled}}'`.
- 내부 확인은 `docker exec -it <컨테이너> sh`(실행 중일 때만 가능).
- 바로 죽는 컨테이너는 엔트리포인트를 셸로 바꿔 실행해 내부를 확인한다: `docker run --rm -it --entrypoint sh <이미지>`.
- 설정을 고친 뒤에는 컨테이너를 재생성해야 반영된다.

## 실무 상황

### 표준 장애 확인 절차
1. 상태
```bash
docker ps -a --filter name=cobip-api
```
2. 로그(최근 200줄, 타임스탬프)
```bash
docker logs --tail 200 -t cobip-api
```
3. 종료 코드, OOM, 재시작 횟수
```bash
docker inspect -f 'status={{.State.Status}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}} restarts={{.RestartCount}}' cobip-api
```
4. 설정(환경변수, 포트, 마운트)
```bash
docker inspect -f '{{json .Config.Env}}' cobip-api
docker port cobip-api
docker inspect -f '{{json .Mounts}}' cobip-api
```
5. 내부 확인(실행 중일 때)
```bash
docker exec -it cobip-api sh
```
컨테이너 셸 안에서 실행하는 확인 명령(curl이 없는 이미지는 wget 사용):
```sh
env | grep DB_
ls -al /app
wget -qO- http://localhost:8080/actuator/health
```
자원 사용량 확인:
```bash
docker stats --no-stream
df -h && docker system df
```

## 장애/문제 상황

### 사례: 컨테이너가 Exited 상태인 경우
- `Exited (0)`: 메인 프로세스가 바로 끝났다. 예: `CMD ["nginx"]`로 데몬 모드 실행(포그라운드는 `nginx -g 'daemon off;'`), 스크립트가 서버를 백그라운드(`&`)로 띄우고 종료.
- `Exited (1)`: 로그에 예외가 있다. 예: `DB_PASSWORD` 환경변수 누락, `Connection refused`(DB 주소를 localhost로 설정), 설정 파일 경로 오류.
- `Exited (127)`: `CMD`의 실행 파일을 찾지 못함(`uvicorn: not found` — 의존성 미설치, 경로 오류).
- `Exited (137)` + `OOMKilled=true`: 메모리 제한(`--memory`) 또는 서버 메모리 부족. JVM 힙(`-Xmx`)과 컨테이너 메모리 제한을 맞추거나 인스턴스를 키운다.
- `exec format error`: 이미지 CPU 아키텍처(arm64/amd64) 불일치.

### 사례: 컨테이너가 계속 재시작되는 경우 (Restarting)
`--restart always`/`unless-stopped`/`on-failure` 정책 때문에 종료 즉시 다시 시작되어 `docker ps`에 `Restarting (1) 3 seconds ago`처럼 보인다. 재시작 정책은 원인을 해결하지 않는다.
- `docker logs --tail 100`으로 매번 반복되는 오류를 확인한다.
- `docker inspect -f '{{.RestartCount}}'`로 재시작 횟수를 확인한다.
- 흔한 원인: 필수 환경변수 누락, DB/Redis가 아직 준비되지 않아 연결 실패, 마이그레이션 실패, 메모리 부족, 포트 바인딩 오류.
- 디버깅 중에는 `docker update --restart=no cobip-api`로 재시작 정책을 끄거나, `docker run --rm -it --entrypoint sh <이미지>`로 수동 실행한다.

### 사례: 포트가 열려 있는데 외부에서 접속되지 않는 경우
1. 호스트에서 `curl -v http://localhost:<호스트포트>` 실행.
   - 성공: Docker는 정상, AWS Security Group/NACL/OS 방화벽/퍼블릭 IP를 확인.
   - 실패: 2번으로.
2. `docker ps`의 PORTS가 `0.0.0.0:8080->8080/tcp`인지 확인(`-p` 누락, `127.0.0.1` 바인딩, 포트 반대로 매핑).
3. 컨테이너 안에서 `wget -qO- localhost:8080` 확인. 실패면 애플리케이션이 떠 있지 않거나 다른 포트 사용.
4. 컨테이너 안에서는 되는데 호스트에서 안 되면 애플리케이션이 `127.0.0.1`에만 바인딩된 것 → `0.0.0.0`으로 변경(예: `uvicorn --host 0.0.0.0`, Flask `--host=0.0.0.0`).

### 사례: 설정/코드를 바꿨는데 반영되지 않음
- 이미지를 다시 빌드하지 않았거나, 새 이미지로 컨테이너를 재생성하지 않고 `docker restart`/`docker start`만 했다.
- 같은 `latest` 태그를 서버가 캐시하고 있어 `docker pull`을 하지 않았다. 배포는 고유 태그(커밋 SHA)로 한다.

### 사례: 디스크 부족 (no space left on device)
오래된 이미지, 중지된 컨테이너, 빌드 캐시, 커진 컨테이너 로그(json-file)가 원인이다.
```bash
docker system df
docker image prune -a      # 사용하지 않는 이미지 삭제
docker builder prune       # 빌드 캐시 삭제
```
로그 크기는 `--log-opt max-size=10m --log-opt max-file=3`으로 제한한다.

## 확인 방법

### 증상별 첫 명령
| 증상 | 첫 번째로 실행할 명령 |
| --- | --- |
| 컨테이너가 안 보임 | `docker ps -a` |
| Exited/Restarting | `docker logs --tail 200 <컨테이너>` |
| 137 종료 | `docker inspect -f '{{.State.OOMKilled}}' <컨테이너>`, `free -h` |
| 외부 접속 불가 | `docker ps`(PORTS), 호스트에서 `curl localhost:<포트>` |
| 다른 컨테이너 연결 실패 | `docker exec <컨테이너> env`, `getent hosts <서비스명>` |
| 설정 반영 안 됨 | `docker inspect -f '{{.Config.Image}}' <컨테이너>`, 이미지 태그 확인 |

## 핵심 정리

### 퀴즈 출제용 핵심 사실
- 컨테이너는 메인 프로세스가 종료되면 Exited 상태가 된다.
- 종료된 컨테이너의 원인은 `docker logs`로 확인할 수 있고, `docker exec`는 실행 중인 컨테이너에만 사용할 수 있다.
- 종료 코드 137은 SIGKILL로 인한 종료이며 OOMKilled 여부를 `docker inspect`로 확인한다.
- 종료 코드 127은 실행할 명령을 찾지 못한 경우다.
- 재시작 정책은 컨테이너를 다시 띄울 뿐 원인을 해결하지 않으며, Restarting 반복 시 로그를 확인해야 한다.
- 호스트에서 `curl localhost`는 성공하고 외부 접속만 실패하면 Security Group/방화벽 문제다.
- 컨테이너 내부에서만 응답하고 호스트에서 실패하면 애플리케이션 바인딩 주소(127.0.0.1)를 의심한다.
- 코드/설정 변경은 이미지 재빌드와 컨테이너 재생성으로 반영된다.
