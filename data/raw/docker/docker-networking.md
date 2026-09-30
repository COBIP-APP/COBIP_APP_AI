# Docker 네트워크: 컨테이너 간 통신, 서비스 이름 DNS, localhost 차이, 포트 매핑

## 개념

### Docker Network
Docker는 컨테이너마다 독립된 네트워크 네임스페이스(자기만의 IP, 포트 공간, localhost)를 부여하고, 네트워크 드라이버로 컨테이너를 연결한다.
- `bridge`(기본): 호스트 내부의 가상 브리지에 컨테이너를 연결한다. 컨테이너는 사설 IP(예: `172.17.0.2`)를 받는다.
- 기본 bridge 네트워크(`bridge`, docker0): 별도 지정 없이 `docker run`한 컨테이너가 연결된다. 컨테이너 이름으로 DNS 해석이 되지 않는다.
- 사용자 정의 bridge 네트워크(`docker network create cobip-net`): Docker 내장 DNS(`127.0.0.11`)가 컨테이너 이름/네트워크 별칭을 IP로 해석해준다. 실무에서는 이 방식을 사용한다.
- `host`: 컨테이너가 호스트 네트워크를 그대로 사용한다(Linux). 포트 매핑이 적용되지 않고 컨테이너 포트가 곧 호스트 포트다.
- `none`: 네트워크 없음.
- Docker Compose는 프로젝트마다 사용자 정의 bridge 네트워크(`<프로젝트>_default`)를 자동으로 만들고, 서비스 이름을 DNS 이름으로 등록한다.

### localhost의 의미: 호스트 vs 컨테이너
- 호스트(EC2, 내 PC)에서 `localhost`(127.0.0.1)는 호스트 자신이다.
- 컨테이너 안에서 `localhost`는 그 컨테이너 자신이다. 호스트나 다른 컨테이너를 가리키지 않는다.
- 따라서 api 컨테이너에서 `localhost:5432`로 DB에 접속하면, DB가 다른 컨테이너에 있을 때 `Connection refused`가 발생한다. 서비스 이름(`db:5432`)을 사용해야 한다.
- 컨테이너에서 호스트에 떠 있는 서비스에 접근하려면 `host.docker.internal`을 사용한다. Docker Desktop(Mac/Windows)은 기본 제공, Linux에서는 `--add-host=host.docker.internal:host-gateway`(Compose: `extra_hosts: ["host.docker.internal:host-gateway"]`)를 추가해야 한다.

### 컨테이너 IP를 직접 쓰지 않는 이유
컨테이너 IP(`172.18.0.3`)는 컨테이너를 재생성할 때마다 바뀔 수 있다. 같은 사용자 정의 네트워크에서는 컨테이너 이름이나 서비스 이름으로 통신한다.

### Port Mapping (포트 매핑, publish)
`-p <호스트포트>:<컨테이너포트>`는 호스트의 포트로 들어온 요청을 컨테이너 포트로 전달(NAT)한다.
- `-p 8080:8080`: 호스트의 모든 인터페이스(0.0.0.0) 8080 → 컨테이너 8080.
- `-p 80:8080`: 호스트 80 → 컨테이너 8080. 외부에서는 `http://서버IP/`로 접근.
- `-p 127.0.0.1:8080:8080`: 호스트 localhost에서만 접근 가능(외부 차단, 앞단 Nginx가 프록시할 때 사용).
- 포트 매핑은 외부(호스트 밖 또는 호스트 자신) → 컨테이너 방향에만 필요하다. 같은 네트워크의 컨테이너끼리는 매핑 없이 컨테이너 포트로 직접 통신한다.
- 컨테이너 안의 애플리케이션이 `127.0.0.1`에만 바인딩하면 포트 매핑이 있어도 접속이 되지 않는다. `0.0.0.0`으로 바인딩한다.

### Security Group과 Port Mapping의 역할 차이
| 구분 | AWS Security Group | Docker Port Mapping |
| --- | --- | --- |
| 위치 | AWS 네트워크 계층(EC2 ENI 앞) | EC2 호스트 OS 내부(Docker) |
| 역할 | 외부 → EC2 호스트 포트로의 트래픽 허용/차단 | 호스트 포트 → 컨테이너 포트 전달 |
| 설정 | 콘솔/CLI의 인바운드 규칙 | `docker run -p`, compose `ports` |
외부에서 EC2의 컨테이너에 접속하려면 두 가지가 모두 맞아야 한다. 예: `-p 80:8080`이면 Security Group에는 컨테이너 포트 8080이 아니라 호스트 포트 80을 열어야 한다.

## 왜 필요한가

### 네트워크 개념을 정확히 알아야 하는 이유
컨테이너 환경의 "연결 안 됨" 장애 대부분은 localhost 오해, 포트 매핑 누락, 호스트 포트와 컨테이너 포트 혼동, 바인딩 주소, 네트워크 분리에서 발생한다. 로컬 PC에서는 되는데 Compose나 EC2에서 안 되는 문제도 대개 여기에 해당한다.

## 핵심 포인트

### Docker 네트워크 핵심
- 컨테이너 안의 localhost는 컨테이너 자신이다.
- 컨테이너 간 통신은 같은 사용자 정의 네트워크에서 컨테이너/서비스 이름으로 한다.
- 기본 bridge 네트워크에서는 이름 기반 DNS가 동작하지 않는다.
- 컨테이너 간 통신은 컨테이너 포트, 외부 접근은 호스트 포트를 사용한다.
- 외부 접속에는 Security Group(호스트 포트 허용) + Port Mapping(호스트 → 컨테이너) + 0.0.0.0 바인딩이 모두 필요하다.

## 실무 상황

### docker run으로 두 컨테이너 연결
```bash
docker network create cobip-net
docker run -d --name db --network cobip-net -e POSTGRES_PASSWORD=secret postgres:16
docker run -d --name api --network cobip-net -p 8080:8080 \
  -e DB_HOST=db -e DB_PORT=5432 cobip-api:1.0.0
```
api는 `db:5432`로 DB에 접속하고, 외부 사용자는 `http://<서버IP>:8080`으로 api에 접속한다. DB는 `-p`로 공개하지 않아 외부에서 접근할 수 없다.

### 같은 요청, 다른 주소
- 호스트에서 api 호출: `curl http://localhost:8080/health` (호스트 포트)
- 다른 컨테이너에서 api 호출: `curl http://api:8080/health` (서비스 이름 + 컨테이너 포트)
- 외부 PC에서 api 호출: `curl http://<EC2-퍼블릭-IP>:8080/health` (Security Group 허용 필요)

## 장애/문제 상황

### 사례: 컨테이너 간 통신에서 localhost를 사용해 실패
api 컨테이너 설정 `REDIS_URL=redis://localhost:6379` → `Connection refused`. api 컨테이너 안에는 Redis가 없다. `redis://redis:6379`로 바꾼다.

### 사례: 포트가 열려 있는데 외부에서 접속이 안 됨
1. `docker ps`의 PORTS에 `0.0.0.0:8080->8080/tcp`가 있는지 확인. 없으면 `-p` 누락.
2. `127.0.0.1:8080->8080/tcp`로 매핑되었으면 호스트 외부에서 접근 불가.
3. 호스트에서 `curl localhost:8080`은 되는데 외부에서 안 되면 Security Group/방화벽 문제.
4. 호스트에서도 `curl localhost:8080`이 `Connection reset`/`Empty reply`면 컨테이너 안 애플리케이션이 127.0.0.1에만 바인딩된 경우가 많다. `docker exec <컨테이너> ss -tlnp`(도구가 있을 때)나 애플리케이션 설정(`--host 0.0.0.0`, `server.address`)을 확인.
5. 호스트 포트와 컨테이너 포트를 뒤바꿔 매핑(`-p 8080:80`인데 앱은 8080에서 수신).

### 사례: 서비스 이름이 해석되지 않음
`could not translate host name "db"` / `Name or service not known`: 두 컨테이너가 서로 다른 네트워크에 있거나 기본 bridge 네트워크를 사용 중이다.

## 확인 방법

### 네트워크 점검 명령어
```bash
docker network ls
docker network inspect cobip-net          # 연결된 컨테이너와 IP
docker port api                           # 포트 매핑
docker ps --format "table {{.Names}}\t{{.Ports}}"
docker exec -it api sh -c "getent hosts db"     # 이름 해석 확인
docker exec -it api sh -c "nc -zv db 5432"      # 컨테이너 간 포트 도달 확인(nc 있을 때)
docker network connect cobip-net other-container  # 실행 중인 컨테이너를 네트워크에 연결
```

## 핵심 정리

### 퀴즈 출제용 핵심 사실
- 컨테이너 내부에서 localhost는 해당 컨테이너 자신을 의미한다.
- 컨테이너 간 통신에서 localhost를 쓰면 상대 컨테이너가 아닌 자기 자신에 접속하므로 실패한다.
- 사용자 정의 bridge 네트워크와 Docker Compose에서는 컨테이너/서비스 이름으로 DNS 해석이 된다.
- 기본 bridge 네트워크에서는 컨테이너 이름으로 통신할 수 없다.
- `-p 호스트포트:컨테이너포트`는 호스트 포트를 컨테이너 포트로 전달하며, 컨테이너 간 통신에는 필요 없다.
- Security Group은 EC2 호스트 포트로의 외부 트래픽을 허용하고, Port Mapping은 호스트 포트를 컨테이너로 전달한다.
- 컨테이너에서 호스트 서비스에 접근할 때는 `host.docker.internal`을 사용한다(Linux는 host-gateway 설정 필요).
- 컨테이너 IP는 재생성 시 바뀔 수 있으므로 이름 기반으로 통신한다.
