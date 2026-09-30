# CI/CD AWS 배포: EC2/ECS 자동 배포, 헬스 체크, 배포 검증, 무중단 배포와 롤백

## 개념

### Deployment (배포)
배포는 레지스트리에 저장된 새 이미지(아티팩트)를 실행 환경에 반영하고, 정상 동작을 확인하는 과정이다. 배포는 "컨테이너를 교체했다"에서 끝나지 않고 헬스 체크와 배포 검증까지 통과해야 완료된다.

### 자동 배포와 수동 배포
- 자동 배포: main merge 또는 태그 push를 트리거로 파이프라인이 배포까지 실행한다(Continuous Deployment). 빠르고 일관적이지만 강한 테스트와 자동 롤백이 전제되어야 한다.
- 수동 배포: 사람이 `workflow_dispatch` 버튼, 승인 게이트(GitHub Environment 필수 리뷰어), 또는 서버에서 스크립트를 실행해 배포한다. 운영 반영 시점을 통제할 수 있다(Continuous Delivery).
- 서버에 SSH로 접속해 `git pull && ./gradlew build`를 직접 실행하는 방식은 재현성과 추적성이 낮아 권장하지 않는다.

### EC2 배포 방식
- SSH 방식: GitHub Actions가 SSH로 EC2에 접속해 배포 스크립트를 실행한다. EC2 Security Group 22번을 GitHub runner에 열어야 하는데 runner IP가 계속 바뀌어 보안상 불리하다.
- SSM 방식: `aws ssm send-command`로 EC2에서 명령을 실행한다. 22번 포트를 열 필요가 없다. EC2에 SSM Agent와 `AmazonSSMManagedInstanceCore` 권한이 필요하다.
- CodeDeploy 방식: AWS CodeDeploy 에이전트가 배포 스크립트와 훅을 실행하고, ALB와 연동한 인플레이스/블루그린 배포를 지원한다.

### ECS 배포 방식
새 이미지로 Task Definition 새 리비전을 등록하고 Service를 업데이트한다. ECS가 새 Task를 띄우고 ALB 헬스 체크를 통과하면 기존 Task를 종료한다(롤링 업데이트).

### Health Check와 Deployment Verification
- Health check: 애플리케이션이 요청을 처리할 수 있는지 자동으로 확인하는 엔드포인트/검사. 예: Spring Boot `/actuator/health`가 `{"status":"UP"}`, FastAPI `/health`가 200.
- Liveness(살아 있는가)와 Readiness(요청을 받을 준비가 됐는가)를 구분하기도 한다.
- Deployment verification: 헬스 체크 외에 실제 버전 확인, 주요 API 스모크 테스트, 에러율/응답 시간 메트릭, 로그 확인으로 배포가 의도대로 됐는지 검증하는 단계.

### 무중단 배포 (Zero-downtime Deployment)
사용자 요청이 끊기지 않게 새 버전으로 교체하는 방식이다.
- Rolling: 서버/Task를 일부씩 새 버전으로 교체한다. ECS `minimumHealthyPercent: 100`, `maximumPercent: 200`이면 새 Task를 먼저 띄운 뒤 기존 Task를 내린다.
- Blue/Green: 기존(Blue)과 동일한 새 환경(Green)을 띄우고 검증 후 ALB 트래픽을 한 번에 전환한다. 문제가 있으면 Blue로 즉시 되돌린다.
- Canary: 일부 트래픽(예: 10%)만 새 버전으로 보내 확인 후 점진적으로 늘린다(ALB 가중치 Target Group).
- 필수 요소: 로드밸런서(ALB), 2개 이상 인스턴스/Task, 헬스 체크, 등록 해제 지연(connection draining), 애플리케이션 graceful shutdown(SIGTERM 처리), 이전 버전과 호환되는 DB 스키마 변경.
- 단일 EC2에서 `docker stop` 후 `docker run` 하는 방식은 교체 사이에 다운타임이 발생한다.

### Rollback (롤백)
배포한 새 버전에 문제가 있을 때 이전 정상 버전으로 되돌리는 것이다.
- EC2/Docker: 이전 이미지 태그로 컨테이너를 다시 실행한다(그래서 고유 태그가 필요하다).
- ECS: 이전 Task Definition 리비전으로 Service를 업데이트한다. Deployment Circuit Breaker의 `rollback: true`를 켜면 새 Task가 계속 실패할 때 자동 롤백된다.
- CodeDeploy/Blue-Green: 트래픽을 이전 환경으로 되돌린다.
- DB 마이그레이션은 되돌리기 어려우므로 컬럼 추가처럼 하위 호환되는 변경을 먼저 배포하고, 삭제는 나중 배포에서 한다(expand-contract).

## 왜 필요한가

### 배포 후 Health Check가 필요한 이유
- 컨테이너 프로세스가 시작됐다고 애플리케이션이 정상인 것은 아니다. DB 연결 실패, 환경변수 누락, 포트 바인딩 오류로 요청을 처리하지 못할 수 있다.
- 헬스 체크를 통과한 인스턴스에만 트래픽을 보내고(ALB), 실패하면 배포를 중단하거나 자동 롤백할 수 있다.
- 파이프라인이 "성공"으로 끝났는데 실제 서비스는 죽어 있는 상황을 막는다.

## 핵심 포인트

### EC2 자동 배포 workflow 예 (SSM 사용)
```yaml
  deploy:
    needs: build-push
    runs-on: ubuntu-latest
    environment: production
    steps:
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.AWS_DEPLOY_ROLE_ARN }}
          aws-region: ap-northeast-2
      - name: Deploy via SSM
        run: |
          aws ssm send-command \
            --instance-ids "${{ vars.EC2_INSTANCE_ID }}" \
            --document-name "AWS-RunShellScript" \
            --parameters 'commands=["/opt/cobip/deploy.sh ${{ github.sha }}"]' \
            --comment "deploy ${{ github.sha }}"
```

### EC2 배포 스크립트 예 (/opt/cobip/deploy.sh)
```bash
#!/usr/bin/env bash
set -euo pipefail
TAG="$1"
REGION=ap-northeast-2
REGISTRY=123456789012.dkr.ecr.${REGION}.amazonaws.com
IMAGE=${REGISTRY}/cobip-api:${TAG}
PREV_IMAGE=$(docker inspect -f '{{.Config.Image}}' cobip-api 2>/dev/null || true)

aws ecr get-login-password --region ${REGION} | docker login --username AWS --password-stdin ${REGISTRY}
docker pull "${IMAGE}"
docker rm -f cobip-api || true
docker run -d --name cobip-api --restart unless-stopped -p 8080:8080 --env-file /opt/cobip/.env "${IMAGE}"
```
헬스 체크: 최대 30회 x 5초
```bash
for i in $(seq 1 30); do
  if curl -fsS http://localhost:8080/actuator/health > /dev/null; then
    echo "deploy success: ${IMAGE}"; exit 0
  fi
  sleep 5
done

echo "health check failed, rolling back to ${PREV_IMAGE}"
docker logs --tail 100 cobip-api || true
if [ -n "${PREV_IMAGE}" ]; then
  docker rm -f cobip-api
  docker run -d --name cobip-api --restart unless-stopped -p 8080:8080 --env-file /opt/cobip/.env "${PREV_IMAGE}"
fi
exit 1
```

### ECS 배포 명령 예
```bash
aws ecs update-service --cluster cobip-cluster --service cobip-api --task-definition cobip-api:14
aws ecs wait services-stable --cluster cobip-cluster --services cobip-api
```
롤백
```bash
aws ecs update-service --cluster cobip-cluster --service cobip-api --task-definition cobip-api:13
```
GitHub Actions에서는 `aws-actions/amazon-ecs-render-task-definition`과 `aws-actions/amazon-ecs-deploy-task-definition`(옵션 `wait-for-service-stability: true`)으로 같은 과정을 자동화한다.

## 실무 상황

### 전체 실무 배포 흐름
1. `feature/*` 브랜치에서 개발 → PR 생성.
2. CI가 빌드와 테스트 실행, 리뷰어가 코드 리뷰 후 승인.
3. main으로 merge.
4. main push 트리거로 CI 테스트 재실행.
5. Docker 이미지 빌드(태그: 커밋 SHA).
6. ECR에 push.
7. EC2(SSM 배포 스크립트) 또는 ECS(Service 업데이트)에 배포.
8. 헬스 체크: 컨테이너 내부/로컬 `curl /actuator/health`, ALB Target healthy 확인.
9. 배포 검증: `curl https://api.example.com/api/version`으로 새 SHA 확인, 주요 API 스모크 테스트, CloudWatch 5xx/지연 시간 확인.
10. 실패 시 이전 태그/리비전으로 롤백하고 원인 분석.

## 장애/문제 상황

### 배포 관련 대표 문제
- 배포 후 헬스 체크 실패: 환경변수 누락, DB Security Group, 포트 불일치, 애플리케이션 기동 시간이 헬스 체크 대기보다 김.
- 무중단이라고 생각했는데 502 발생: 인스턴스가 1대뿐, 등록 해제 지연 없이 컨테이너 종료, 애플리케이션이 SIGTERM에서 진행 중 요청을 처리하지 않음.
- 롤백 불가: `latest` 태그만 사용해 이전 이미지를 특정할 수 없음, 파괴적인 DB 마이그레이션.
- SSH 배포 타임아웃: Security Group 22번이 GitHub runner IP를 허용하지 않음 → SSM 사용 권장.
- ECS 배포가 끝나지 않음: 새 Task가 헬스 체크를 통과하지 못해 교체를 반복 → Circuit Breaker로 자동 롤백 설정.

## 확인 방법

### 배포 검증 체크리스트
1. 서버/Task에서 실행 중인 이미지 태그가 새 커밋 SHA인지
```bash
docker inspect -f '{{.Config.Image}}' cobip-api
aws ecs describe-services --cluster cobip-cluster --services cobip-api \
  --query "services[].deployments[].[status,taskDefinition,rolloutState,runningCount]"
```
2. 헬스 체크
```bash
curl -fsS http://localhost:8080/actuator/health
curl -fsS https://api.example.com/actuator/health
```
3. ALB Target 상태
```bash
aws elbv2 describe-target-health --target-group-arn <target-group-arn>
```
4. 로그와 에러
```bash
docker logs --since 10m cobip-api
aws logs tail /ecs/cobip-api --since 10m
```

## 핵심 정리

### 퀴즈 출제용 핵심 사실
- 배포 후 헬스 체크가 필요한 이유는 컨테이너가 실행돼도 애플리케이션이 요청을 처리하지 못할 수 있기 때문이며, 실패 시 트래픽 차단·배포 중단·롤백의 근거가 된다.
- 자동 배포는 merge/태그 트리거로 파이프라인이 배포까지 수행하고, 수동 배포는 사람이 승인하거나 직접 실행한다.
- 무중단 배포 방식에는 Rolling, Blue/Green, Canary가 있으며 로드밸런서와 헬스 체크가 필요하다.
- 롤백은 이전 이미지 태그(또는 이전 ECS Task Definition 리비전)로 다시 배포하는 것이다.
- ECS Deployment Circuit Breaker는 배포 실패 시 자동 롤백할 수 있다.
- EC2 배포에서 SSM `send-command`를 쓰면 SSH 22번 포트를 열지 않아도 된다.
- 배포 검증은 헬스 체크 + 버전 확인 + 스모크 테스트 + 로그/메트릭 확인으로 한다.
- 단일 EC2에서 컨테이너를 중지 후 재실행하는 배포는 짧은 다운타임이 발생한다.
