# CI/CD 장애 대응: 테스트·빌드 실패, 이미지 Push 실패, 배포 실패와 롤백 판단

## 개념

### CI/CD 장애 대응의 원칙
파이프라인 장애는 "어느 단계에서 실패했는가"를 먼저 특정한다. 단계는 크게 트리거 → 체크아웃/의존성 → 빌드 → 테스트 → 이미지 빌드 → 레지스트리 push → 배포 → 헬스 체크 → 검증으로 나뉘고, 단계마다 원인 유형이 다르다. 운영 영향이 있으면 원인 분석보다 롤백으로 서비스를 먼저 복구한다.

## 왜 필요한가

### 체계적 대응이 필요한 이유
- 실패 로그를 읽지 않고 재실행(re-run)만 반복하면 시간만 쓰고 불안정한(flaky) 테스트를 방치하게 된다.
- 배포 실패 시 빠른 롤백 기준이 없으면 장애 시간이 길어진다.
- 같은 실패가 반복되지 않도록 파이프라인(테스트, 헬스 체크, 권한)을 개선해야 한다.

## 핵심 포인트

### 단계별 실패 원인 요약
| 실패 단계 | 대표 증상 | 주요 원인 | 확인/조치 |
| --- | --- | --- | --- |
| 트리거 | workflow가 아예 실행 안 됨 | `.github/workflows` 경로, YAML 오류, 브랜치/paths 조건 | Actions 탭, YAML 검증 |
| 의존성 | 패키지 다운로드 실패 | 버전 충돌, 레지스트리 장애, lock 파일 불일치 | 버전 고정, 캐시 확인 |
| 빌드 | 컴파일 오류 | 머지 후 충돌, JDK/Python 버전 차이 | 로컬에서 동일 버전으로 재현 |
| 테스트 | 테스트 실패 | 실제 버그, 환경 차이, 순서 의존, 외부 서비스 의존 | 실패 테스트 로그, 로컬 재현 |
| 이미지 빌드 | `docker build` 실패 | Dockerfile 경로, COPY 대상 누락, `.dockerignore` 과다 | 로컬 `docker build` |
| push | `denied`, `no basic auth credentials` | 로그인 누락, IAM 권한, 리포지토리 없음, 불변 태그 | IAM/ECR 설정 |
| 배포 | SSH/SSM 실패, ECS 업데이트 실패 | 네트워크, 권한, 잘못된 인스턴스 ID | 배포 로그, SSM 명령 결과 |
| 헬스 체크 | 배포 후 unhealthy | 환경변수, DB 연결, 포트, 기동 지연 | `docker logs`, Target health |

### 롤백 판단 기준
- 배포 직후 헬스 체크 실패, 5xx 급증, 핵심 기능 장애 → 즉시 이전 버전으로 롤백 후 원인 분석.
- 원인이 명확하고 수정이 매우 작더라도, 운영 장애 중에는 롤백이 새 수정 배포(fix-forward)보다 대체로 안전하다.
- 롤백 후 실패한 버전의 로그, 이미지 태그, 변경 커밋을 기록한다.

## 실무 상황

### 사례: PR에서 CI 테스트가 실패
1. Checks 탭에서 실패한 job/step 로그를 연다.
2. 실패한 테스트 이름과 assertion 메시지를 확인한다.
3. 로컬에서 같은 명령(`./gradlew test --tests "*QuizServiceTest"`, `pytest tests/test_quiz.py -q`)으로 재현한다.
4. 로컬 통과/CI 실패라면 환경 차이(시간대, OS, 환경변수, 테스트 DB, 병렬 실행)를 의심한다.
5. 고친 뒤 같은 브랜치에 push하면 CI가 다시 실행된다.
6. 테스트를 삭제하거나 skip해서 통과시키지 않는다.

### 사례: main merge 후 이미지 push 실패
- `Error: Could not assume role`: OIDC 신뢰 정책의 `sub` 조건(`repo:ORG/REPO:ref:refs/heads/main`)과 실제 브랜치 불일치 또는 `id-token: write` 누락.
- `denied: ... not authorized to perform: ecr:PutImage`: Role 정책에 push 권한 추가.
- `repository ... does not exist`: 리포지토리 생성 또는 리전 확인.
- `tag ... already exists` (immutable): 커밋 SHA 태그 사용, 같은 커밋 재실행이면 push 단계를 건너뛰는 조건 추가.

### 사례: 배포는 성공했는데 서비스가 안 됨
1. 파이프라인에 헬스 체크 단계가 없어서 성공으로 표시된 경우다. 헬스 체크를 파이프라인에 추가한다.
2. EC2에서 `docker ps -a`, `docker logs --tail 200 cobip-api`로 기동 오류 확인.
3. 새 환경변수가 서버 `.env`나 ECS Task Definition에 추가되지 않았는지 확인.
4. ALB Target Group 상태(`describe-target-health`)와 헬스 체크 경로 확인.
5. 필요 시 이전 이미지 태그로 롤백.

### 사례: 로컬(Mac)에서 빌드한 이미지는 되는데 EC2에서 `exec format error`
CI(ubuntu-latest, amd64)에서 빌드하거나 `docker build --platform linux/amd64`로 빌드한다. Graviton(arm64) 인스턴스라면 arm64 이미지를 빌드한다.

### 사례: 배포 중 일시적 502/503
인스턴스 1대에서 컨테이너를 교체하는 동안 발생하는 다운타임이다. 2대 이상 + ALB 롤링 배포, 등록 해제 지연, graceful shutdown을 적용한다.

## 장애/문제 상황

### 반복되는 문제와 예방
- Flaky 테스트: 시간/랜덤/외부 API 의존 → 고정된 시계, 목(mock), 테스트 격리.
- secret 만료/교체 누락: Access Key 대신 OIDC 사용, secret 목록 문서화.
- 설정 드리프트: 서버에서 수동으로 바꾼 설정이 파이프라인에 없음 → 설정을 코드/Task Definition/Parameter Store로 관리.
- 디스크 부족으로 배포 실패: 배포 스크립트에 `docker image prune -f` 추가, ECR 수명 주기 정책.
- 운영 DB에 파괴적 마이그레이션: 롤백 불가 → 하위 호환 마이그레이션 원칙.

## 확인 방법

### 파이프라인 장애 확인 명령
GitHub Actions 실행 이력과 실패 로그
```bash
gh run list --limit 10
gh run view <run-id> --log-failed
gh run rerun <run-id> --failed
```
SSM 배포 명령 결과
```bash
aws ssm list-command-invocations --command-id <command-id> --details \
  --query "CommandInvocations[].CommandPlugins[].[Status,Output]"
```
서버/서비스 상태
```bash
docker ps -a && docker logs --tail 200 cobip-api
aws ecs describe-services --cluster cobip-cluster --services cobip-api --query "services[].events[:10].message"
aws elbv2 describe-target-health --target-group-arn <target-group-arn>
```

## 핵심 정리

### 퀴즈 출제용 핵심 사실
- 파이프라인 장애는 먼저 실패한 단계(트리거, 빌드, 테스트, push, 배포, 헬스 체크)를 특정한다.
- CI 테스트 실패는 로그 확인 후 로컬에서 같은 명령으로 재현하며, 테스트를 삭제해 통과시키지 않는다.
- `no basic auth credentials`는 레지스트리 로그인 누락/만료, `not authorized to perform ecr:PutImage`는 IAM 권한 부족이다.
- 헬스 체크 단계가 없으면 서비스가 죽어도 배포가 성공으로 표시될 수 있다.
- 운영 장애 시에는 이전 이미지 태그로 롤백해 먼저 복구하고 원인을 분석한다.
- `exec format error`는 이미지 아키텍처 불일치이며 `--platform linux/amd64`로 해결한다.
- 단일 인스턴스 컨테이너 교체는 다운타임을 만들며, 무중단을 위해 ALB + 다중 인스턴스 + 롤링 배포를 사용한다.
