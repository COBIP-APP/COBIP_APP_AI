# CI/CD 기본 개념: CI와 CD의 차이, Pipeline, Git 브랜치 전략, Pull Request와 Merge

## 개념

### CI (Continuous Integration, 지속적 통합)
CI는 개발자들이 코드 변경을 공유 브랜치에 자주 통합하고, 통합할 때마다 자동으로 빌드와 테스트를 실행해 문제를 빨리 발견하는 개발 방식이다.
- 트리거: Pull Request 생성/업데이트, 특정 브랜치로 push.
- 주요 단계: 소스 checkout → 의존성 설치 → 컴파일/빌드 → 린트/정적 분석 → 단위·통합 테스트 → (선택) Docker 이미지 빌드.
- 결과: 성공/실패 상태가 PR에 표시되고, 실패하면 merge를 막는다(브랜치 보호 규칙의 required status check).
- 목적: "통합했을 때 깨지는지"를 사람보다 먼저 자동으로 확인하는 것.

### CD (Continuous Delivery / Continuous Deployment)
CD는 CI를 통과한 결과물을 배포 가능한 상태로 만들고 실제 환경에 배포하는 과정을 자동화하는 것이다.
- Continuous Delivery(지속적 전달): 언제든 운영에 배포할 수 있는 상태(검증된 아티팩트, 스테이징 배포)까지 자동화하고, 운영 배포는 사람이 승인하거나 버튼을 눌러 실행한다.
- Continuous Deployment(지속적 배포): 테스트를 통과한 변경을 사람의 승인 없이 운영 환경까지 자동으로 배포한다.
- 주요 단계: 아티팩트(Docker 이미지) 레지스트리 push → 스테이징/운영 배포 → 헬스 체크 → 배포 검증 → 실패 시 롤백.

### CI와 CD의 차이
| 구분 | CI | CD |
| --- | --- | --- |
| 핵심 질문 | 이 변경을 합쳐도 코드가 깨지지 않는가? | 검증된 결과물을 안전하게 환경에 반영할 수 있는가? |
| 주요 작업 | 빌드, 테스트, 정적 분석 | 아티팩트 저장, 배포, 헬스 체크, 롤백 |
| 주 트리거 | PR, 모든 브랜치 push | main(또는 release) merge, 태그, 수동 실행 |
| 산출물 | 테스트 결과, 빌드 성공 여부, (이미지) | 실행 중인 새 버전 서비스 |
CI는 "통합과 검증", CD는 "전달과 배포"다. CI 없이 CD만 하면 검증되지 않은 코드가 자동으로 운영에 나간다.

### CI/CD Pipeline
코드 변경부터 운영 반영까지의 자동화 단계를 순서대로 연결한 것이다. 앞 단계가 실패하면 다음 단계로 진행하지 않는다.
```
feature 브랜치 개발
→ Pull Request 생성
→ CI: 빌드 + 테스트 (자동)
→ 코드 리뷰 + 승인
→ main(또는 develop) merge
→ CI: 테스트 재실행 + Docker 이미지 빌드
→ ECR(Container Registry)에 이미지 push
→ CD: EC2/ECS 배포
→ 헬스 체크
→ 배포 검증(스모크 테스트, 로그/메트릭 확인)
→ 실패 시 이전 이미지로 롤백
```

## 왜 필요한가

### CI에서 테스트가 필요한 이유
- 버그를 merge 전에 발견하면 수정 비용이 가장 낮다. 운영에서 발견되면 장애와 데이터 오류로 이어진다.
- 여러 개발자의 변경이 합쳐질 때 생기는 충돌·회귀(regression)를 자동으로 잡는다.
- 리뷰어가 동작 여부를 수동으로 확인하지 않아도 되고, 리뷰는 설계와 가독성에 집중할 수 있다.
- "테스트를 통과한 코드만 main에 들어간다"는 규칙이 main을 항상 배포 가능한 상태로 유지한다.

### CD가 필요한 이유
- 수동 배포는 사람마다 절차가 달라 실수(잘못된 서버, 누락된 환경변수, 잘못된 버전)가 생긴다.
- 자동화된 동일 절차로 자주, 작게 배포하면 변경 범위가 작아 문제 원인 파악과 롤백이 쉽다.
- 배포 이력(어떤 커밋/이미지가 언제 배포됐는지)이 남는다.

## 핵심 포인트

### Git 기반 개발 workflow
- `main`: 운영에 배포된(또는 배포 가능한) 안정 코드. 직접 push를 막고 PR로만 변경한다.
- `develop`: 다음 릴리스를 위한 통합 브랜치(Git Flow). 개발 서버에 자동 배포하는 경우가 많다.
- `feature/*`: 기능 단위 작업 브랜치. `develop`(또는 `main`)에서 분기해 작업 후 PR로 합친다. 예: `feature/quiz-generate-api`.
- `release/*`, `hotfix/*`: Git Flow에서 릴리스 준비와 운영 긴급 수정을 위한 브랜치.
- GitHub Flow: `main` + 짧게 사는 feature 브랜치만 사용하는 단순한 전략. 지속적 배포와 잘 맞는다.

### Pull Request와 Merge
- Pull Request(PR): 내 브랜치의 변경을 대상 브랜치에 합쳐달라는 요청. 변경 내용(diff), 코드 리뷰, CI 결과가 한곳에 모인다.
- 코드 리뷰: 설계, 버그 가능성, 보안, 테스트 누락을 사람이 검토하고 승인(Approve)한다.
- 브랜치 보호 규칙: 리뷰 승인 수, 필수 CI 상태 체크 통과, 최신 브랜치 반영을 merge 조건으로 강제한다.
- Merge 방식: Merge commit(이력 보존), Squash and merge(PR을 커밋 하나로), Rebase and merge(선형 이력).
- Merge 충돌(conflict): 같은 부분을 서로 다르게 수정했을 때 발생하며, 로컬에서 해결 후 다시 push한다.

### Build, Test, Deployment 용어
- Build: 소스 코드를 실행 가능한 결과물(JAR, 번들, Docker 이미지)로 만드는 단계.
- Test: 단위 테스트, 통합 테스트, E2E 테스트로 동작을 검증하는 단계.
- Artifact: 빌드 산출물. 컨테이너 환경에서는 Docker 이미지가 대표적인 아티팩트다.
- Deployment: 아티팩트를 서버 환경(개발/스테이징/운영)에 반영해 실행하는 단계.
- 자동 배포: merge나 태그를 트리거로 파이프라인이 배포까지 실행. 수동 배포: 사람이 명령/버튼으로 실행(`workflow_dispatch`, 승인 게이트, SSH 후 스크립트 실행).

## 실무 상황

### 팀 개발 흐름 예시
1. `develop`에서 `feature/login-api` 브랜치를 만들고 작업한다.
2. push 후 `develop` 대상으로 PR을 만든다. GitHub Actions가 `./gradlew test`를 실행한다.
3. 테스트 실패 → PR에 빨간 X 표시, merge 버튼 비활성화 → 수정 후 다시 push.
4. 리뷰어 승인 + CI 통과 → Squash and merge.
5. `develop` merge → 개발 서버에 자동 배포.
6. 릴리스 시 `develop` → `main` PR → merge → Docker 이미지 빌드, ECR push, 운영 배포(Continuous Delivery라면 승인 후).
7. 배포 후 `/actuator/health` 헬스 체크와 주요 API 스모크 테스트로 검증한다.

## 장애/문제 상황

### CI/CD가 없거나 잘못 구성된 경우의 문제
- 테스트 없이 merge → main이 깨져 다른 개발자들도 빌드 실패.
- 로컬에서만 빌드해 서버에 파일 복사 → 누구의 PC에서 어떤 버전으로 빌드했는지 추적 불가.
- 배포 후 헬스 체크 없음 → 애플리케이션이 기동 실패했는데 "배포 성공"으로 표시되고 사용자가 먼저 장애를 발견.
- 긴 수명의 feature 브랜치 → merge 시 대규모 충돌.
- CI를 PR에는 돌리지 않고 main push에만 돌림 → 깨진 코드가 이미 main에 들어간 뒤 발견.

## 확인 방법

### 파이프라인 상태 확인
- GitHub PR 화면의 Checks 탭에서 각 job/step 로그를 확인한다.
- GitHub CLI: `gh run list --limit 5`, `gh run view <run-id> --log-failed`.
- 로컬에서 CI와 같은 명령을 실행해 재현한다: `./gradlew clean test`, `pytest -q`, `docker build -t test .`.
- 배포 후: `curl -fsS https://api.example.com/actuator/health`, ALB Target 상태, CloudWatch 로그/5xx 메트릭.

## 핵심 정리

### 퀴즈 출제용 핵심 사실
- CI는 코드 변경을 자주 통합하고 자동 빌드·테스트로 검증하는 과정이다.
- CD는 검증된 결과물을 배포 가능한 상태로 만들거나(Continuous Delivery) 운영까지 자동 배포(Continuous Deployment)하는 과정이다.
- Continuous Delivery는 운영 배포에 사람의 승인이 있고, Continuous Deployment는 승인 없이 자동 배포한다.
- CI에서 테스트가 필요한 이유는 merge 전에 버그와 회귀를 조기에 발견해 main을 항상 배포 가능한 상태로 유지하기 위해서다.
- 일반적인 흐름: feature 브랜치 → PR → 코드 리뷰 → merge → CI 테스트 → 이미지 빌드 → ECR push → 배포 → 헬스 체크 → 검증.
- `main`은 안정 코드, `develop`은 통합 브랜치, `feature/*`는 기능 작업 브랜치다.
- 브랜치 보호 규칙으로 CI 통과와 리뷰 승인을 merge 조건으로 강제할 수 있다.
