# GitHub Actions: workflow, trigger, job, step, runner, secret, environment variable

## 개념

### GitHub Actions
GitHub Actions는 GitHub 저장소에 내장된 CI/CD 자동화 도구다. 저장소의 `.github/workflows/` 디렉터리에 YAML 파일로 workflow를 정의하면, 이벤트가 발생할 때 GitHub가 제공하는 runner(가상 머신)에서 자동으로 실행된다.

### 구성 요소
- Workflow: 자동화 전체 프로세스. `.github/workflows/ci.yml` 파일 하나가 하나의 workflow다.
- Trigger(Event, `on`): workflow를 실행시키는 이벤트.
  - `push`: 특정 브랜치/태그에 push될 때(`branches: [main]`, `tags: ['v*']`).
  - `pull_request`: PR 생성/업데이트 시(`branches: [main, develop]`는 대상 브랜치 기준).
  - `workflow_dispatch`: Actions 탭에서 사람이 수동 실행(입력값 지정 가능) → 수동 배포에 사용.
  - `schedule`: cron 일정 실행.
  - `paths`: 특정 경로 변경 시에만 실행.
- Job: 하나의 runner에서 실행되는 step 묶음. 여러 job은 기본적으로 병렬 실행되며, `needs: [test]`로 순서(의존성)를 정한다. job마다 새 runner를 사용하므로 파일은 공유되지 않는다(아티팩트 업로드/다운로드 필요).
- Step: job 안에서 순서대로 실행되는 개별 작업. `run`(셸 명령) 또는 `uses`(재사용 가능한 Action, 예: `actions/checkout@v4`)로 정의한다. 같은 job의 step은 같은 runner의 파일 시스템을 공유한다.
- Runner: job을 실행하는 머신. `runs-on: ubuntu-latest` 같은 GitHub-hosted runner나 직접 운영하는 self-hosted runner.
- Action: 재사용 가능한 step 단위 컴포넌트. 예: `actions/checkout`, `actions/setup-java`, `actions/setup-python`, `aws-actions/configure-aws-credentials`, `aws-actions/amazon-ecr-login`.

### Secret과 Environment Variable
- Secret: 비밀번호, 토큰, 키 같은 민감 값. 저장소 Settings → Secrets and variables → Actions에 저장하고 `${{ secrets.EC2_SSH_KEY }}`로 참조한다. 로그에 출력되면 `***`로 마스킹되며, 한 번 저장하면 값을 다시 볼 수 없다.
- Variables: 민감하지 않은 설정 값(`${{ vars.AWS_REGION }}`).
- 환경변수 `env`: workflow/job/step 수준에서 정의(`env: IMAGE_NAME: cobip-api`)하고 셸에서 `$IMAGE_NAME`으로 사용한다.
- 기본 제공 컨텍스트: `${{ github.sha }}`(커밋 SHA), `${{ github.ref_name }}`(브랜치/태그 이름), `${{ github.event_name }}`.
- Environment(배포 환경): `production`, `staging` 같은 환경을 만들고 환경별 secret과 보호 규칙(필수 리뷰어 승인, 브랜치 제한)을 설정한다. job에 `environment: production`을 지정하면 승인 후에만 실행되게 할 수 있다.
- 포크(fork)에서 온 `pull_request` 실행에는 저장소 secret이 전달되지 않는다.

## 왜 필요한가

### GitHub Actions를 쓰는 이유
- 코드 저장소와 CI/CD가 한곳에 있어 PR 화면에서 바로 테스트 결과를 보고 merge 조건으로 사용할 수 있다.
- 별도 CI 서버(Jenkins 등)를 운영하지 않아도 된다.
- Marketplace의 Action으로 AWS 인증, Docker 빌드, 캐시 등을 쉽게 구성한다.

## 핵심 포인트

### CI workflow 예시 (Spring Boot)
```yaml
name: CI

on:
  pull_request:
    branches: [main, develop]
  push:
    branches: [develop]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-java@v4
        with:
          distribution: temurin
          java-version: '21'
          cache: gradle
      - name: Test
        run: ./gradlew clean test
```

### CI workflow 예시 (FastAPI)
```yaml
name: CI
on:
  pull_request:
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
          cache: pip
      - run: pip install -r requirements.txt
      - run: pytest -q
```

### job 의존성과 조건
```yaml
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: ./gradlew test
  build-and-push:
    needs: test                       # test 성공 후에만 실행
    if: github.ref == 'refs/heads/main' && github.event_name == 'push'
    runs-on: ubuntu-latest
    steps:
      - run: echo "build image ${{ github.sha }}"
```

### AWS 인증 방식
- 권장: OIDC. GitHub가 발급한 토큰으로 IAM Role을 assume하므로 장기 Access Key를 secret에 저장하지 않는다. workflow에 `permissions: id-token: write, contents: read`가 필요하다.
```yaml
permissions:
  id-token: write
  contents: read
steps:
  - uses: aws-actions/configure-aws-credentials@v4
    with:
      role-to-assume: arn:aws:iam::123456789012:role/github-actions-deploy
      aws-region: ap-northeast-2
```
- 대안: IAM User Access Key를 `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` secret으로 저장. 유출 위험이 있어 최소 권한과 주기적 교체가 필요하다.

## 실무 상황

### 수동 배포 workflow
```yaml
name: Deploy (manual)
on:
  workflow_dispatch:
    inputs:
      image_tag:
        description: '배포할 이미지 태그(커밋 SHA)'
        required: true
jobs:
  deploy:
    runs-on: ubuntu-latest
    environment: production          # 필수 리뷰어 승인 후 실행
    steps:
      - run: echo "Deploying ${{ inputs.image_tag }}"
```
롤백할 때 이전 태그를 입력해 같은 workflow로 재배포할 수 있다.

## 장애/문제 상황

### GitHub Actions에서 자주 겪는 문제
- workflow가 실행되지 않음: 파일 위치가 `.github/workflows/`가 아님, YAML 들여쓰기 오류, `on.branches` 조건 불일치(PR은 대상 브랜치 기준).
- `Permission denied` on `./gradlew`: 실행 권한 없음 → `git update-index --chmod=+x gradlew` 또는 step에서 `chmod +x gradlew`.
- secret이 빈 값: 이름 오타, environment secret인데 job에 `environment` 미지정, 포크 PR 실행.
- `Could not assume role` / `Not authorized to perform sts:AssumeRoleWithWebIdentity`: `permissions: id-token: write` 누락, IAM Role 신뢰 정책의 저장소/브랜치 조건 불일치.
- job 간 파일이 없음: job마다 runner가 다르므로 `actions/upload-artifact`/`download-artifact` 사용 또는 같은 job으로 합치기.
- 로컬에서는 통과하는데 CI에서 실패: OS/시간대/환경변수/테스트 DB 차이, 테스트 순서 의존성.

## 확인 방법

### workflow 디버깅
- Actions 탭 → 실패한 run → 실패한 job → 빨간 step의 로그 확인.
- `gh run list --workflow ci.yml --limit 5`, `gh run view <run-id> --log-failed`, `gh run rerun <run-id> --failed`.
- 디버그 로그: 저장소 secret/variable에 `ACTIONS_STEP_DEBUG=true` 설정.
- secret 값은 출력하지 말고 존재 여부만 확인: `test -n "$TOKEN" && echo set`.

## 핵심 정리

### 퀴즈 출제용 핵심 사실
- GitHub Actions workflow 파일은 `.github/workflows/` 디렉터리에 YAML로 작성한다.
- workflow를 실행시키는 이벤트는 `on`(trigger)으로 정의하며, `push`, `pull_request`, `workflow_dispatch`, `schedule` 등이 있다.
- job은 하나의 runner에서 실행되는 step의 묶음이며, 여러 job은 기본적으로 병렬 실행되고 `needs`로 순서를 정한다.
- step은 job 안에서 순차 실행되는 개별 작업이며 `run` 또는 `uses`로 정의한다.
- 민감 정보는 secret에 저장하고 `${{ secrets.NAME }}`으로 참조하며, 로그에서는 마스킹된다.
- `workflow_dispatch`는 수동 실행 트리거로 수동 배포/롤백에 사용한다.
- AWS 인증은 장기 Access Key보다 OIDC + IAM Role(`id-token: write`)이 권장된다.
- `environment`에 필수 리뷰어를 설정하면 운영 배포 전 승인 단계를 둘 수 있다.
