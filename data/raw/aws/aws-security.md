# AWS 보안 기본: IAM, Security Group, NACL, 포트와 인바운드/아웃바운드

## 개념

### IAM (Identity and Access Management)
IAM은 "누가(주체) 어떤 AWS 자원에 어떤 작업을 할 수 있는지"를 관리하는 글로벌 서비스다.
- User: 사람 또는 애플리케이션이 사용하는 장기 자격증명(비밀번호, Access Key) 주체.
- Group: User를 묶어 정책을 한 번에 부여하는 단위.
- Role: 임시 자격증명을 발급받는 주체. EC2, ECS Task, Lambda, GitHub Actions(OIDC) 등이 Role을 "맡아서(assume)" 사용한다.
- Policy: 허용/거부 권한을 JSON으로 정의한 문서. `Effect`, `Action`, `Resource`, `Condition`으로 구성된다.
- 평가 규칙: 기본은 암묵적 거부(implicit deny) → 명시적 Allow가 있으면 허용 → 명시적 Deny가 하나라도 있으면 최종 거부(명시적 Deny 우선).

### Security Group (보안 그룹)
Security Group은 EC2 인스턴스, RDS, ALB, ECS Task 등의 네트워크 인터페이스(ENI) 단위에 붙는 가상 방화벽이다.
- 허용(Allow) 규칙만 있고 거부(Deny) 규칙은 없다. 규칙에 없는 트래픽은 모두 차단된다.
- 상태 저장(Stateful): 인바운드로 허용된 요청의 응답 트래픽은 아웃바운드 규칙과 관계없이 자동으로 허용된다.
- 인바운드 규칙: 외부 → 자원으로 들어오는 트래픽을 제어한다(프로토콜, 포트, 소스).
- 아웃바운드 규칙: 자원 → 외부로 나가는 트래픽을 제어한다. 새로 만든 Security Group은 기본적으로 인바운드 규칙이 없고 아웃바운드는 전체 허용이다.
- 소스로 IP 대역(CIDR)뿐 아니라 다른 Security Group ID를 지정할 수 있다. 예: "ALB의 Security Group에서 오는 8080만 허용".

### NACL (Network ACL)
NACL은 Subnet 단위에 적용되는 방화벽이다.
- 허용(Allow)과 거부(Deny) 규칙을 모두 가진다.
- 규칙 번호가 낮은 것부터 평가하고, 처음 일치한 규칙을 적용한다.
- 상태 비저장(Stateless): 응답 트래픽도 별도로 허용해야 한다. 아웃바운드에 임시 포트(ephemeral port, 보통 1024-65535)를 열어야 응답이 나간다.
- 기본 NACL은 모든 인바운드/아웃바운드를 허용하므로, 실무 접속 문제는 대부분 Security Group에서 발생한다.

### Security Group과 NACL 비교
| 항목 | Security Group | NACL |
| --- | --- | --- |
| 적용 단위 | 인스턴스/ENI | Subnet |
| 규칙 | Allow만 | Allow + Deny |
| 상태 | Stateful(응답 자동 허용) | Stateless(응답도 규칙 필요) |
| 평가 | 모든 규칙을 종합 | 번호 순서, 첫 일치 적용 |
| 주 용도 | 서비스별 포트 허용 | 특정 IP 대역 차단 등 Subnet 단위 통제 |

## 왜 필요한가

### 최소 권한과 네트워크 차단이 필요한 이유
- Access Key가 코드나 GitHub에 노출되면 즉시 악용될 수 있다(채굴용 EC2 대량 생성 등). 그래서 EC2/ECS에는 Access Key 대신 IAM Role을 부여한다.
- 최소 권한 원칙(Least Privilege): 필요한 Action과 Resource만 허용해 사고 범위를 줄인다.
- DB 포트(3306, 5432)나 SSH(22)를 `0.0.0.0/0`으로 열면 전 세계에서 무차별 대입 공격 대상이 된다.

## 핵심 포인트

### 실무에서 기억할 포트
| 포트 | 용도 | 권장 인바운드 소스 |
| --- | --- | --- |
| 22 | SSH 원격 접속 | 내 IP(/32) 또는 사내 대역만. 가능하면 SSM Session Manager 사용 |
| 80 | HTTP | ALB SG: 0.0.0.0/0 (HTTPS로 리다이렉트 용도) |
| 443 | HTTPS | ALB SG: 0.0.0.0/0 |
| 8080 / 8000 등 | 애플리케이션 포트(Spring Boot 8080, FastAPI 8000) | ALB의 Security Group만 |
| 3306 / 5432 | MySQL / PostgreSQL | 애플리케이션 서버의 Security Group만 |
| 6379 | Redis | 애플리케이션 서버의 Security Group만 |

### 보안 기본 수칙
- 루트 계정에는 MFA를 설정하고 일상 작업에 쓰지 않는다.
- 사람은 IAM Identity Center(SSO) 또는 IAM User + MFA, 서버/CI는 IAM Role을 사용한다.
- EC2에서 AWS API를 호출할 때는 Instance Profile(IAM Role)을 연결한다. 코드나 `.env`에 Access Key를 하드코딩하지 않는다.
- GitHub Actions는 장기 Access Key 대신 OIDC로 Role을 assume하는 방식을 권장한다.
- Security Group 소스는 IP보다 다른 Security Group을 참조하는 방식이 관리하기 쉽다(서버 IP가 바뀌어도 규칙 유지).
- HTTPS 인증서는 ACM(AWS Certificate Manager)에서 발급해 ALB에 연결한다.

## 실무 상황

### 3계층 서비스의 Security Group 설계 예
- `alb-sg`: 인바운드 80, 443 ← `0.0.0.0/0`
- `app-sg`: 인바운드 8080 ← `alb-sg`, 인바운드 22 ← 관리자 IP/32 (또는 22 미개방 + SSM)
- `db-sg`: 인바운드 5432 ← `app-sg`
- 결과: 인터넷에서는 ALB만 보이고, 애플리케이션과 DB는 직접 접근할 수 없다.

### EC2에서 S3 접근 권한 부여
EC2가 S3에 파일을 업로드해야 한다면 `s3:PutObject` 권한을 가진 IAM Role을 만들어 Instance Profile로 연결한다. AWS SDK는 Instance Metadata Service(IMDS)에서 임시 자격증명을 자동으로 가져온다.

## 장애/문제 상황

### 사례: Security Group에서 포트를 열었는데 접속이 안 되는 경우
Security Group 인바운드에 8080을 열어도 접속이 안 될 수 있다. 확인할 원인:
1. 규칙을 다른 Security Group에 추가했다(인스턴스에 실제 연결된 SG가 아님).
2. 소스를 "내 IP"로 설정했는데 현재 네트워크 IP가 바뀌었다.
3. 애플리케이션이 `127.0.0.1`(localhost)에만 바인딩되어 외부 인터페이스에서 수신하지 않는다. `0.0.0.0`으로 바인딩해야 한다.
4. Docker 컨테이너 포트가 호스트에 매핑되지 않았다(`-p 8080:8080` 누락). Security Group은 EC2 호스트까지의 트래픽만 허용할 뿐, 컨테이너로의 전달은 Docker Port Mapping이 담당한다.
5. 인스턴스가 Private Subnet에 있거나 퍼블릭 IP가 없다.
6. NACL 또는 OS 방화벽(ufw, firewalld)이 차단한다.

### 사례: AccessDenied 오류
- `An error occurred (AccessDenied) when calling the PutObject operation`: 현재 주체에 해당 Action 권한이 없거나 명시적 Deny, 또는 버킷 정책이 거부한다.
- EC2에서 `Unable to locate credentials`: 인스턴스에 IAM Role이 연결되지 않았다.

## 확인 방법

### 보안 설정 점검 명령어
현재 인증 주체 확인 (EC2에서 실행하면 연결된 Role 정보가 나온다)
```bash
aws sts get-caller-identity
```
인스턴스에 연결된 Security Group 확인
```bash
aws ec2 describe-instances --instance-ids i-0123456789abcdef0 \
  --query "Reservations[].Instances[].SecurityGroups"
```
Security Group 인바운드 규칙 확인
```bash
aws ec2 describe-security-groups --group-ids sg-0123456789abcdef0 \
  --query "SecurityGroups[].IpPermissions"
```
서버 내부에서 애플리케이션이 어느 주소/포트에서 수신하는지 확인
```bash
sudo ss -tlnp | grep 8080
```
외부(내 PC)에서 포트 도달 여부 확인
```bash
nc -zv <EC2-퍼블릭-IP> 8080
```
- `ss` 결과가 `127.0.0.1:8080`이면 외부 접속 불가, `0.0.0.0:8080` 또는 `*:8080`이면 모든 인터페이스에서 수신 중이다.

## 핵심 정리

### 퀴즈 출제용 핵심 사실
- Security Group은 인스턴스(ENI) 단위, Stateful, Allow 규칙만 가진다.
- NACL은 Subnet 단위, Stateless, Allow/Deny 규칙을 번호 순서로 평가한다.
- 인바운드 규칙은 들어오는 트래픽, 아웃바운드 규칙은 나가는 트래픽을 제어한다.
- SSH는 22번, HTTP는 80번, HTTPS는 443번 포트를 사용한다.
- IAM 정책 평가에서 명시적 Deny는 Allow보다 우선한다.
- EC2/ECS에는 Access Key 대신 IAM Role을 부여한다.
- DB 포트는 애플리케이션 Security Group에서만 허용하고 `0.0.0.0/0`으로 열지 않는다.
- Security Group은 EC2 호스트까지의 트래픽을 허용하고, Docker Port Mapping은 호스트 포트를 컨테이너 포트로 전달한다. 둘은 서로 다른 계층이다.
