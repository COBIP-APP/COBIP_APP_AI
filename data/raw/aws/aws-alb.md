# AWS ALB와 Target Group: 로드밸런싱, 리스너, 헬스 체크, Unhealthy Target 대응

## 개념

### ALB (Application Load Balancer)
ALB는 HTTP/HTTPS(L7) 요청을 받아 여러 서버로 분산하는 AWS 관리형 로드밸런서다.
- 리스너(Listener): ALB가 요청을 받는 프로토콜과 포트. 예: HTTP:80, HTTPS:443.
- 리스너 규칙(Rule): 경로(`/api/*`), 호스트(`api.example.com`), 헤더 등의 조건에 따라 어떤 Target Group으로 보낼지 결정한다. 기본 동작(default action)도 정의한다.
- HTTPS 리스너에는 ACM 인증서를 연결하며, ALB에서 TLS를 종료(termination)한 뒤 뒤쪽 서버에는 HTTP로 전달하는 구성이 흔하다.
- HTTP:80 리스너는 보통 HTTPS:443으로 리다이렉트하도록 설정한다.
- ALB는 최소 2개 AZ의 Subnet에 배치해야 하며, 인터넷용(internet-facing) ALB는 Public Subnet에 둔다.
- ALB의 IP는 고정되지 않으므로 DNS 이름(`xxx.ap-northeast-2.elb.amazonaws.com`)이나 Route 53 Alias로 접근한다.

### Target Group
Target Group은 ALB가 요청을 전달할 대상(Target)들의 묶음과 헬스 체크 설정을 정의한다.
- Target 유형: `instance`(EC2 인스턴스 ID), `ip`(IP 주소, ECS Fargate의 awsvpc 모드에서 사용), `lambda`.
- 대상 포트: 트래픽을 전달할 애플리케이션 포트(예: 8080). 인스턴스에서 Docker를 쓰면 호스트에 매핑된 포트를 지정한다.
- 헬스 체크 설정: 프로토콜, 경로(예: `/actuator/health`, `/health`), 포트(traffic port 기본), 정상 임계값(healthy threshold), 비정상 임계값(unhealthy threshold), 간격(interval), 제한 시간(timeout), 성공 코드(matcher, 기본 `200`).
- Target 상태: `initial`(등록 직후 검사 중), `healthy`, `unhealthy`, `unused`(ALB가 해당 AZ에서 활성화되지 않았거나 리스너 규칙에 연결 안 됨), `draining`(등록 해제 중).

### ALB와 Target Group의 역할 차이
| 구분 | ALB | Target Group |
| --- | --- | --- |
| 역할 | 클라이언트 요청 수신, 리스너 규칙으로 라우팅 | 요청을 받을 서버 목록과 헬스 체크 정의 |
| 설정 | 리스너(80/443), 인증서, 규칙, Security Group, Subnet | Target 유형, 대상 포트, 헬스 체크 경로/임계값 |
| 관계 | 하나의 ALB가 여러 Target Group으로 라우팅 가능 | 하나의 Target Group에 여러 EC2/ECS Task 등록 |

## 왜 필요한가

### ALB를 사용하는 이유
- 여러 서버에 트래픽을 분산하고, 헬스 체크에 실패한 서버에는 요청을 보내지 않아 가용성을 높인다.
- 서버를 Private Subnet에 두고 ALB만 인터넷에 노출해 보안을 강화한다.
- HTTPS 인증서를 ALB에서 일괄 관리한다.
- 롤링/블루그린 배포 시 Target을 교체하면서 무중단 배포를 구현할 수 있다.
- ECS Service, Auto Scaling Group과 연동해 Target이 자동으로 등록/해제된다.

## 핵심 포인트

### ALB 구성 핵심
- 트래픽 흐름: 클라이언트 → ALB 리스너(443) → 리스너 규칙 → Target Group → Target의 애플리케이션 포트(8080).
- Security Group 두 단계: ALB SG는 80/443을 인터넷에서 허용, 애플리케이션 SG는 대상 포트(8080)를 ALB SG에서 허용해야 한다. 헬스 체크 트래픽도 ALB에서 오므로 이 규칙이 없으면 unhealthy가 된다.
- 헬스 체크 경로는 인증 없이 200을 반환하는 가벼운 엔드포인트로 둔다. 로그인이 필요한 경로는 302/401을 반환해 unhealthy가 된다.
- 등록 해제 지연(Deregistration delay, 기본 300초): Target을 제거할 때 진행 중인 요청을 마칠 시간을 준다(connection draining).
- 모든 Target이 unhealthy이면 ALB는 fail-open으로 모든 Target에 요청을 보낸다. Target이 하나도 등록되지 않으면 503을 반환한다.

### ALB 응답 코드로 원인 추정
- 502 Bad Gateway: Target이 연결을 끊거나 잘못된 응답을 반환(애플리케이션 크래시, keep-alive 타임아웃 불일치 등).
- 503 Service Unavailable: Target Group에 등록된 Target이 없음.
- 504 Gateway Timeout: Target이 ALB idle timeout(기본 60초) 안에 응답하지 않음, 또는 Security Group/라우팅으로 연결 불가.
- `HTTPCode_ELB_5XX_Count`는 ALB가 직접 만든 5xx, `HTTPCode_Target_5XX_Count`는 애플리케이션이 반환한 5xx다.

## 실무 상황

### EC2 두 대 + ALB 구성
1. Target Group 생성: 유형 instance, 프로토콜 HTTP, 포트 8080, 헬스 체크 경로 `/actuator/health`.
2. EC2 두 대(서로 다른 AZ)를 Target으로 등록.
3. ALB 생성: internet-facing, Public Subnet 2개 선택, `alb-sg` 연결.
4. 리스너 HTTPS:443(ACM 인증서) → Target Group으로 전달, HTTP:80 → 443 리다이렉트.
5. `app-sg` 인바운드 8080 소스를 `alb-sg`로 설정.
6. Route 53에서 `api.example.com` A 레코드(Alias) → ALB.

### 경로 기반 라우팅
`/api/*` → Spring Boot Target Group(8080), `/ai/*` → FastAPI Target Group(8000), 기본 동작 → 프론트엔드 Target Group.

## 장애/문제 상황

### 사례: ALB에서 Target이 unhealthy가 되는 경우
원인과 확인 포인트:
1. 애플리케이션 Security Group이 ALB Security Group에서 오는 대상 포트/헬스 체크 포트를 허용하지 않음 → `Target.Timeout`.
2. 헬스 체크 경로가 존재하지 않거나(404), 인증이 필요해 302/401 반환 → `Target.ResponseCodeMismatch`.
3. Target Group 포트와 애플리케이션 실제 포트 불일치(Target Group은 8080, 컨테이너는 호스트 80에 매핑).
4. 애플리케이션이 `127.0.0.1`에만 바인딩되었거나 컨테이너가 죽어 있음 → `Target.FailedHealthChecks`/`Target.Timeout`.
5. 애플리케이션 기동이 느려(Spring Boot 초기화, DB 연결 대기) 임계값 안에 응답하지 못함 → 헬스 체크 유예(ECS의 healthCheckGracePeriodSeconds) 또는 interval/threshold 조정.
6. 헬스 체크 엔드포인트가 DB 등 외부 의존성까지 검사해 DB 장애 시 전체 Target이 unhealthy가 됨.

### 사례: 도메인으로 접속하면 504가 발생
ALB → Target 연결이 막혀 있을 가능성이 높다. 애플리케이션 SG 인바운드와 Target의 수신 포트를 확인한다. 응답이 60초 이상 걸리는 API라면 idle timeout을 조정하거나 비동기로 바꾼다.

## 확인 방법

### ALB/Target Group 점검 명령어
Target 상태와 unhealthy 사유 확인
```bash
aws elbv2 describe-target-health \
  --target-group-arn arn:aws:elasticloadbalancing:ap-northeast-2:123456789012:targetgroup/app-tg/0123456789abcdef
```
Target Group 헬스 체크 설정 확인
```bash
aws elbv2 describe-target-groups --names app-tg \
  --query "TargetGroups[].[Port,HealthCheckPath,HealthCheckPort,Matcher.HttpCode]"
```
Target(EC2) 내부에서 헬스 체크 경로 직접 호출
```bash
curl -i http://localhost:8080/actuator/health
```
ALB DNS로 외부 호출
```bash
curl -i http://app-alb-123456789.ap-northeast-2.elb.amazonaws.com/actuator/health
```
콘솔에서는 EC2 → Target Groups → Targets 탭의 Health status details에서 사유를 볼 수 있다. CloudWatch 메트릭 `HealthyHostCount`, `UnHealthyHostCount`, `TargetResponseTime`도 확인한다.

## 핵심 정리

### 퀴즈 출제용 핵심 사실
- ALB는 L7(HTTP/HTTPS) 로드밸런서이며, 리스너와 규칙으로 요청을 Target Group에 라우팅한다.
- Target Group은 대상 서버 목록, 대상 포트, 헬스 체크 설정을 정의한다.
- ALB는 최소 2개 AZ의 Subnet이 필요하다.
- 헬스 체크 기본 성공 코드는 200이며, 인증이 필요한 경로를 헬스 체크로 쓰면 unhealthy가 된다.
- Target unhealthy의 대표 원인은 애플리케이션 SG가 ALB SG를 허용하지 않은 경우와 헬스 체크 경로/포트 불일치다.
- `aws elbv2 describe-target-health`로 Target 상태와 사유를 확인한다.
- 502는 Target의 잘못된 응답, 503은 등록된 Target 없음, 504는 Target 응답 시간 초과를 의미하는 경우가 많다.
- 등록 해제 지연(deregistration delay)은 진행 중 요청을 마무리하게 해 무중단 배포에 기여한다.
