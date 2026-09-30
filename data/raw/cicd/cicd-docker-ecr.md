# CI/CD에서 Docker 이미지 빌드와 ECR Push: Container Registry, 이미지 태깅 전략

## 개념

### Container Registry
Container Registry는 Docker 이미지를 저장하고 버전(태그)별로 배포하는 저장소다. CI가 빌드한 이미지를 registry에 push하고, 배포 대상 서버(EC2, ECS)는 registry에서 pull해 실행한다.
- 대표 서비스: AWS ECR, Docker Hub, GitHub Container Registry(ghcr.io).
- 이미지 주소: `<레지스트리>/<리포지토리>:<태그>`. 예: `123456789012.dkr.ecr.ap-northeast-2.amazonaws.com/cobip-api:3f2a1c9`.
- Docker Image build: `docker build`로 Dockerfile에서 이미지를 만드는 단계.
- Docker Image push: `docker push`로 레지스트리에 업로드하는 단계. push 전에 `docker login`으로 인증하고 레지스트리 주소가 포함된 태그를 붙여야 한다.

### ECR을 사용하는 이유 (Docker Hub와 비교)
- ECR은 AWS 계정 안의 비공개 레지스트리로 IAM 권한으로 push/pull을 제어하고, EC2/ECS가 IAM Role로 별도 비밀번호 없이 pull할 수 있다.
- Docker Hub는 공개 이미지 배포와 공유에 주로 쓰이며, 비공개 저장소와 pull 횟수에 요금제/제한이 있다.
- 회사 서비스 이미지는 ECR에 저장하고, 베이스 이미지(`eclipse-temurin`, `python`)는 Docker Hub 같은 공개 레지스트리에서 받는 구성이 일반적이다.

### 이미지 태깅 전략
- 커밋 SHA 태그(`cobip-api:3f2a1c9` 또는 `${{ github.sha }}`): 어떤 코드가 배포됐는지 정확히 추적되고, 이전 SHA로 롤백할 수 있다. 권장.
- 시맨틱 버전 태그(`cobip-api:1.4.0`): 릴리스 단위 관리.
- `latest`: 편하지만 어떤 버전인지 알 수 없고, 서버의 캐시 때문에 새 이미지가 pull되지 않거나 롤백 대상이 사라진다. 운영 배포 기준 태그로 쓰지 않는다.
- ECR 태그 불변성(immutable)을 켜면 같은 태그를 덮어쓸 수 없어 배포 이력의 신뢰성이 높아진다.

## 왜 필요한가

### Docker 이미지를 Registry에 저장하는 이유
- 한 번 빌드하고 여러 곳에 배포(Build once, deploy many): CI에서 테스트를 통과한 바로 그 이미지를 스테이징과 운영에 동일하게 배포한다. 서버에서 다시 빌드하면 의존성 버전이나 빌드 환경 차이로 결과가 달라질 수 있다.
- 배포 서버에 소스 코드, 빌드 도구(JDK, Gradle, Node)를 설치할 필요가 없다.
- 태그별로 이전 버전이 남아 있어 롤백은 이전 태그를 다시 실행하는 것으로 끝난다.
- 여러 서버/ECS Task가 동시에 같은 이미지를 pull할 수 있다(확장).
- 레지스트리 스캔으로 이미지 취약점을 점검할 수 있다.

## 핵심 포인트

### 이미지 빌드·푸시 명령
```bash
REGION=ap-northeast-2
ACCOUNT_ID=123456789012
REGISTRY=${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com
REPO=cobip-api
TAG=$(git rev-parse --short HEAD)
```
ECR 리포지토리 생성(최초 1회)
```bash
aws ecr create-repository --repository-name ${REPO} --region ${REGION} \
  --image-tag-mutability IMMUTABLE --image-scanning-configuration scanOnPush=true
```
인증 → 빌드 → push
```bash
aws ecr get-login-password --region ${REGION} \
  | docker login --username AWS --password-stdin ${REGISTRY}
docker build --platform linux/amd64 -t ${REGISTRY}/${REPO}:${TAG} .
docker push ${REGISTRY}/${REPO}:${TAG}
```
- CI의 IAM 권한: `ecr:GetAuthorizationToken`, `ecr:BatchCheckLayerAvailability`, `ecr:InitiateLayerUpload`, `ecr:UploadLayerPart`, `ecr:CompleteLayerUpload`, `ecr:PutImage`(+ pull 시 `ecr:BatchGetImage`, `ecr:GetDownloadUrlForLayer`).
- 이미지는 CI 테스트가 통과한 뒤에 빌드/푸시한다.

### GitHub Actions에서 ECR Push
```yaml
name: Build and Push to ECR
on:
  push:
    branches: [main]

permissions:
  id-token: write
  contents: read

env:
  AWS_REGION: ap-northeast-2
  ECR_REPOSITORY: cobip-api

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-java@v4
        with:
          distribution: temurin
          java-version: '21'
      - run: ./gradlew test

  build-push:
    needs: test
    runs-on: ubuntu-latest
    outputs:
      image: ${{ steps.meta.outputs.image }}
    steps:
      - uses: actions/checkout@v4
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.AWS_DEPLOY_ROLE_ARN }}
          aws-region: ${{ env.AWS_REGION }}
      - id: ecr
        uses: aws-actions/amazon-ecr-login@v2
      - id: meta
        run: echo "image=${{ steps.ecr.outputs.registry }}/${{ env.ECR_REPOSITORY }}:${{ github.sha }}" >> "$GITHUB_OUTPUT"
      - name: Build and push
        run: |
          docker build -t ${{ steps.meta.outputs.image }} .
          docker push ${{ steps.meta.outputs.image }}
```

## 실무 상황

### 이미지 흐름
1. main merge → CI 테스트 통과.
2. `docker build`로 커밋 SHA 태그 이미지를 만든다.
3. ECR에 push한다(ECR 수명 주기 정책으로 최근 30개만 유지 등).
4. 배포 job이 EC2/ECS에 새 태그를 전달한다.
5. 서버는 ECR에서 해당 태그를 pull해 컨테이너를 교체한다.
6. 문제 발생 시 직전 태그로 다시 배포한다.

### 빌드 속도 개선
- Dockerfile에서 의존성 파일을 먼저 COPY해 레이어 캐시를 활용한다.
- `.dockerignore`로 빌드 컨텍스트를 줄인다.
- 멀티 스테이지 빌드로 최종 이미지를 작게 만든다(push/pull 시간 단축).

## 장애/문제 상황

### ECR push/pull 실패 사례
- `no basic auth credentials`: `docker login`을 하지 않았거나 토큰(12시간) 만료.
- `denied: User ... is not authorized to perform: ecr:InitiateLayerUpload`: CI Role에 push 권한 없음.
- `name unknown: The repository with name 'cobip-api' does not exist`: 리포지토리 미생성 또는 다른 리전/계정.
- `tag invalid: The image tag '...' already exists ... cannot be overwritten because the repository is immutable`: 불변 태그 리포지토리에 같은 태그 재push → 고유 태그(SHA) 사용.
- 태그에 레지스트리 주소를 붙이지 않고 `docker push cobip-api:1.0` → Docker Hub(`docker.io/library/...`)로 push를 시도해 거부됨.
- 서버에서 `exec format error`: arm64에서 빌드한 이미지를 amd64 서버에서 실행 → `--platform linux/amd64`로 빌드.
- 서버가 이전 버전을 계속 실행: `latest` 태그 사용 + `docker pull` 누락.

## 확인 방법

### 레지스트리 확인 명령
```bash
aws ecr describe-repositories --region ap-northeast-2
aws ecr list-images --repository-name cobip-api --region ap-northeast-2
aws ecr describe-images --repository-name cobip-api --image-ids imageTag=3f2a1c9
docker inspect -f '{{.Architecture}}' 123456789012.dkr.ecr.ap-northeast-2.amazonaws.com/cobip-api:3f2a1c9
docker inspect -f '{{.Config.Image}}' cobip-api   # 서버에서 실행 중인 컨테이너의 이미지 태그
```

## 핵심 정리

### 퀴즈 출제용 핵심 사실
- Container Registry는 Docker 이미지를 태그별로 저장하고 배포 서버가 pull하는 저장소다.
- Docker 이미지를 Registry에 저장하는 이유는 CI에서 검증된 동일 이미지를 여러 환경에 배포하고, 버전 추적과 롤백을 하기 위해서다.
- ECR push 순서: `aws ecr get-login-password | docker login` → `docker build -t <ECR주소>/<repo>:<tag>` → `docker push`.
- 이미지 태그는 `latest`보다 커밋 SHA 같은 고유 태그가 추적과 롤백에 유리하다.
- ECR은 IAM으로 권한을 제어하는 AWS 비공개 레지스트리이고, Docker Hub는 공개 이미지 공유 중심 레지스트리다.
- ECR 로그인 토큰은 12시간 유효하다.
- 이미지 빌드/푸시는 CI 테스트 통과 후에 실행한다.
