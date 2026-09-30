# AWS 컨테이너 배포: ECR, ECS, EC2에서 Docker 컨테이너 운영과 장애 확인 순서

## 개념

### ECR (Elastic Container Registry)
ECR은 AWS의 관리형 Docker(OCI) 컨테이너 이미지 레지스트리다.
- Repository: 이미지 저장 단위. 이미지 주소 형식은 `<계정ID>.dkr.ecr.<리전>.amazonaws.com/<리포지토리>:<태그>`. 예: `123456789012.dkr.ecr.ap-northeast-2.amazonaws.com/cobip-api:3f2a1c9`.
- Private Repository가 기본이며 IAM으로 push/pull 권한을 제어한다. 공개 배포용 ECR Public도 별도로 있다.
- 인증: `aws ecr get-login-password`로 발급한 임시 토큰(12시간 유효)으로 `docker login`한다.
- 태그 불변성(Tag immutability): 같은 태그로 덮어쓰기를 금지해 배포 이미지를 추적 가능하게 한다.
- 수명 주기 정책(Lifecycle policy): 오래된 이미지를 자동 삭제해 저장 비용을 줄인다.
- 이미지 스캔: push 시 취약점(CVE) 스캔을 수행할 수 있다.

### ECR과 Docker Hub의 차이
| 구분 | ECR (Private) | Docker Hub |
| --- | --- | --- |
| 운영 주체 | AWS | Docker Inc. |
| 권한 관리 | IAM 정책/Role | Docker Hub 계정, 액세스 토큰 |
| 주 용도 | 회사 서비스 이미지의 비공개 저장, AWS 배포 | 공개 베이스 이미지(`nginx`, `eclipse-temurin`, `python`) 배포/공유 |
| AWS 연동 | EC2/ECS가 IAM Role로 인증, VPC Endpoint 사용 가능 | 별도 로그인, 익명 pull은 rate limit 적용 |
둘 다 컨테이너 레지스트리라는 역할은 같지만, AWS 배포용 비공개 이미지는 ECR, 공개 베이스 이미지는 Docker Hub에서 받는 경우가 일반적이다.

### ECS (Elastic Container Service)
ECS는 AWS의 컨테이너 오케스트레이션 서비스다.
- Cluster: Task가 실행되는 논리적 그룹.
- Task Definition: 컨테이너 실행 명세(이미지, CPU/메모리, 포트 매핑, 환경변수, 로그 설정, IAM Role). 수정할 때마다 새 리비전(`cobip-api:12`)이 생긴다.
- Task: Task Definition으로 실행된 컨테이너 묶음(실행 인스턴스).
- Service: 원하는 개수(desired count)의 Task를 계속 유지하고, 죽은 Task를 교체하며, ALB Target Group에 Task를 등록한다.
- Launch type: EC2(직접 관리하는 EC2 위에서 실행)와 Fargate(서버 관리 없는 서버리스 컨테이너 실행).
- Task Execution Role: ECS 에이전트가 ECR 이미지 pull, CloudWatch Logs 전송, Secrets 조회에 쓰는 Role.
- Task Role: 컨테이너 안의 애플리케이션이 S3 등 AWS API를 호출할 때 쓰는 Role.

## 왜 필요한가

### 레지스트리와 오케스트레이션이 필요한 이유
- 서버에서 직접 소스 코드를 빌드하면 서버마다 결과가 달라질 수 있다. CI에서 한 번 빌드한 이미지를 레지스트리에 저장하고 모든 서버가 같은 이미지를 pull하면 동일한 결과물을 배포할 수 있다.
- 이미지 태그(커밋 SHA, 버전)로 어떤 코드가 배포됐는지 추적하고, 이전 태그로 롤백할 수 있다.
- EC2에서 `docker run`으로 직접 운영하면 서버 장애, 컨테이너 재시작, 확장, 무중단 배포를 사람이 관리해야 한다. ECS Service는 이를 자동화한다.

## 핵심 포인트

### AWS 컨테이너 배포 핵심
- 배포 흐름: Docker 이미지 빌드 → ECR push → EC2에서 pull 후 `docker run` 또는 ECS Service 업데이트 → ALB 헬스 체크 → 배포 검증.
- EC2에서 ECR pull 권한: 인스턴스 Role에 `AmazonEC2ContainerRegistryReadOnly`(또는 `ecr:GetAuthorizationToken`, `ecr:BatchGetImage`, `ecr:GetDownloadUrlForLayer`) 권한이 필요하다.
- ECR 로그인 토큰은 12시간 후 만료되므로 배포 스크립트에서 매번 로그인한다.
- 이미지 아키텍처를 서버와 맞춘다. Apple Silicon(arm64)에서 빌드한 이미지를 x86_64 EC2에서 실행하면 `exec format error`가 발생하므로 `--platform linux/amd64`로 빌드한다(또는 Graviton arm64 인스턴스 사용).
- ECS Fargate는 `awsvpc` 네트워크 모드를 쓰므로 Target Group 유형은 `ip`여야 한다.
- ECS 롤링 업데이트는 `minimumHealthyPercent`/`maximumPercent`로 새 Task를 먼저 띄우고 기존 Task를 내리며, Deployment Circuit Breaker를 켜면 실패 시 자동 롤백된다.
- EC2의 Security Group은 호스트 포트까지만 허용한다. 컨테이너까지 도달하려면 `docker run -p 호스트포트:컨테이너포트` 매핑이 필요하다.

## 실무 상황

### EC2에서 ECR 이미지로 배포하기
EC2 (IAM Role에 ECR 읽기 권한 필요)
```bash
REGION=ap-northeast-2
REGISTRY=123456789012.dkr.ecr.${REGION}.amazonaws.com
IMAGE=${REGISTRY}/cobip-api:3f2a1c9

aws ecr get-login-password --region ${REGION} \
  | docker login --username AWS --password-stdin ${REGISTRY}

docker pull ${IMAGE}
docker stop cobip-api || true
docker rm cobip-api || true
docker run -d --name cobip-api --restart unless-stopped \
  -p 8080:8080 --env-file /opt/cobip/.env ${IMAGE}
```
헬스 체크
```bash
curl -fsS http://localhost:8080/actuator/health
```
이 방식은 컨테이너를 교체하는 동안 짧은 다운타임이 생긴다. 무중단이 필요하면 ALB 뒤에 여러 서버를 두고 한 대씩 교체하거나 ECS 롤링 배포를 사용한다.

### ECS Service 새 이미지 배포
Task Definition에 새 이미지 태그를 넣어 새 리비전 등록 후 서비스 업데이트
```bash
aws ecs update-service --cluster cobip-cluster --service cobip-api \
  --task-definition cobip-api:13
```
같은 태그(예: latest)를 다시 받아 재배포할 때
```bash
aws ecs update-service --cluster cobip-cluster --service cobip-api --force-new-deployment
```
배포 안정화 대기
```bash
aws ecs wait services-stable --cluster cobip-cluster --services cobip-api
```

## 장애/문제 상황

### 사례: EC2에서 Docker 컨테이너가 실행되지 않는 경우
1. `docker ps -a`로 상태 확인: `Exited (1)`이면 애플리케이션 오류, `Exited (137)`이면 SIGKILL(메모리 부족 OOM 가능), `Restarting`이면 재시작 반복.
2. `docker logs cobip-api`로 오류 확인: 환경변수 누락, DB 연결 실패, 포트 충돌 등.
3. `docker inspect -f '{{.State.ExitCode}} {{.State.OOMKilled}}' cobip-api`로 종료 코드와 OOM 여부 확인.
4. `exec format error`: 이미지 CPU 아키텍처 불일치.
5. `Bind for 0.0.0.0:8080 failed: port is already allocated`: 같은 호스트 포트를 쓰는 다른 컨테이너/프로세스가 있음.
6. `no space left on device`: 디스크 부족, `docker system df`로 확인.

### 사례: ECR에서 이미지를 pull할 수 없음
- `no basic auth credentials`: `docker login`을 하지 않았거나 토큰 만료.
- `AccessDeniedException`/`denied`: EC2 Role에 ECR 권한 없음, 다른 계정/리전의 리포지토리.
- `manifest unknown` / `not found`: 태그 오타 또는 해당 태그가 push되지 않음.
- 타임아웃: Private Subnet에 NAT Gateway나 ECR VPC Endpoint가 없음.

### 사례: ECS Task가 계속 STOPPED 됨
`aws ecs describe-tasks`의 `stoppedReason`과 컨테이너 `exitCode`, CloudWatch Logs를 확인한다. `CannotPullContainerError`는 이미지/권한/네트워크 문제, `Essential container in task exited`는 애플리케이션 종료, ALB 헬스 체크 실패로 Task가 교체되는 경우도 많다.

### AWS 장애 발생 시 기본 확인 순서
1. 증상 범위 확인: 전체 사용자인지 일부인지, 특정 API인지, 언제부터인지(최근 배포 여부).
2. DNS: `dig api.example.com`이 올바른 ALB를 가리키는지.
3. ALB: 리스너/규칙, Target Group의 Target 상태(healthy/unhealthy)와 5xx 메트릭.
4. 네트워크: Security Group(ALB SG → App SG → DB SG), 라우팅 테이블, NACL.
5. 서버/컨테이너: EC2 상태 검사, `docker ps -a`, 포트 매핑, 바인딩 주소.
6. 애플리케이션 로그: `docker logs`, CloudWatch Logs에서 예외 스택트레이스.
7. 의존성: RDS 연결, 외부 API, 디스크/메모리.
8. 최근 배포가 원인이면 이전 이미지 태그로 롤백한 뒤 원인 분석.

## 확인 방법

### 컨테이너 배포 점검 명령어
```bash
aws ecr describe-images --repository-name cobip-api \
  --query "sort_by(imageDetails,&imagePushedAt)[-5:].[imageTags,imagePushedAt]"
docker ps -a
docker logs --tail 200 cobip-api
docker inspect -f '{{.State.Status}} {{.State.ExitCode}} {{.State.OOMKilled}}' cobip-api
aws ecs describe-services --cluster cobip-cluster --services cobip-api \
  --query "services[].{running:runningCount,desired:desiredCount,events:events[:5].message}"
aws ecs describe-tasks --cluster cobip-cluster --tasks <task-arn> \
  --query "tasks[].{reason:stoppedReason,containers:containers[].[name,exitCode,reason]}"
```

## 핵심 정리

### 퀴즈 출제용 핵심 사실
- ECR은 AWS의 관리형 비공개 컨테이너 이미지 레지스트리이고, Docker Hub는 Docker Inc.가 운영하는 공개 레지스트리 중심 서비스다.
- ECR 로그인: `aws ecr get-login-password --region <리전> | docker login --username AWS --password-stdin <레지스트리>`.
- EC2에서 ECR 이미지를 pull하려면 인스턴스 IAM Role에 ECR 읽기 권한이 필요하다.
- ECS의 Task Definition은 컨테이너 실행 명세, Service는 원하는 Task 개수를 유지하고 ALB에 등록한다.
- Task Execution Role은 이미지 pull/로그 전송용, Task Role은 애플리케이션의 AWS API 호출용이다.
- Fargate(awsvpc)와 ALB를 연결할 때 Target Group 유형은 `ip`다.
- `exec format error`는 이미지 CPU 아키텍처와 서버 아키텍처가 다를 때 발생한다.
- 종료 코드 137은 SIGKILL(OOM 등), 143은 SIGTERM에 의한 종료를 의미한다.
