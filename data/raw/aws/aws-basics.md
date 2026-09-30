# AWS 기본 구조와 실무 운영 개요 (Region, AZ, 계정, 서비스 구성)

## 개념

### AWS란 무엇인가
AWS(Amazon Web Services)는 서버, 네트워크, 스토리지, 데이터베이스 같은 IT 인프라를 인터넷을 통해 필요한 만큼 빌려 쓰는 클라우드 서비스다. 물리 서버를 직접 구매·설치하지 않고 콘솔, AWS CLI, SDK, IaC(Terraform, CloudFormation)로 자원을 생성하고 사용한 만큼 비용을 낸다.

### Region과 Availability Zone(AZ)
- Region: 지리적으로 분리된 AWS 데이터센터 묶음이다. 예: 서울 리전 `ap-northeast-2`, 버지니아 북부 `us-east-1`.
- Availability Zone(AZ): 하나의 Region 안에 있는 물리적으로 분리된 데이터센터 그룹이다. 예: `ap-northeast-2a`, `ap-northeast-2c`.
- 대부분의 자원(EC2, VPC, RDS, ECR 리포지토리)은 Region 단위로 생성되므로, 콘솔에서 Region을 잘못 선택하면 "자원이 사라진 것처럼" 보인다.
- IAM, Route 53, CloudFront는 글로벌 서비스다.

### 실무에서 자주 쓰는 AWS 서비스 분류
| 분류 | 서비스 | 역할 |
| --- | --- | --- |
| 컴퓨팅 | EC2, ECS, Fargate, Lambda | 애플리케이션 실행 |
| 네트워크 | VPC, Subnet, Internet Gateway, NAT Gateway, Route 53, ALB | 트래픽 경로와 도메인 |
| 보안 | IAM, Security Group, NACL, ACM | 권한, 방화벽, 인증서 |
| 스토리지 | S3, EBS | 파일/객체 저장, 디스크 |
| 데이터베이스 | RDS, ElastiCache | 관리형 DB, 캐시 |
| 컨테이너 | ECR, ECS | 이미지 저장, 컨테이너 운영 |
| 모니터링 | CloudWatch | 메트릭, 로그, 알람 |

## 왜 필요한가

### 클라우드를 쓰는 이유
- 서버를 몇 분 만에 생성하고 필요 없으면 삭제할 수 있어 초기 비용이 적다.
- 여러 AZ에 서버와 DB를 분산해 하나의 데이터센터 장애에도 서비스를 유지할 수 있다(고가용성).
- 로드밸런서(ALB), 관리형 DB(RDS), 컨테이너 레지스트리(ECR)처럼 직접 구축하기 어려운 인프라를 서비스로 바로 사용한다.
- 공동 책임 모델(Shared Responsibility Model): AWS는 데이터센터·하드웨어·가상화 계층 보안을 책임지고, 사용자는 OS 패치, Security Group 설정, IAM 권한, 데이터 암호화 등 "클라우드 안의 보안"을 책임진다.

## 핵심 포인트

### AWS 기본 구조 핵심
- 계정(Account) → Region → VPC → Subnet(AZ 단위) → EC2/RDS 등 자원 순서로 포함 관계를 이해한다.
- Subnet은 하나의 AZ에만 속한다. 고가용성을 위해 최소 2개 AZ에 Subnet을 만든다.
- 일반적인 웹 서비스 구조: 사용자 → Route 53(도메인) → ALB(Public Subnet) → EC2/ECS(Private 또는 Public Subnet) → RDS(Private Subnet).
- 루트 계정은 일상 작업에 쓰지 않고, IAM 사용자/역할로 작업한다.
- 비용은 실행 시간, 데이터 전송량, 저장 용량 단위로 발생한다. 중지(stop)한 EC2도 연결된 EBS 볼륨과 할당만 해둔 Elastic IP/퍼블릭 IPv4는 비용이 발생한다.

## 실무 상황

### 일반적인 Spring Boot/FastAPI 서비스의 AWS 배포 구조
1. 개발자가 GitHub에 코드를 push하면 GitHub Actions가 테스트 후 Docker 이미지를 빌드해 ECR에 push한다.
2. EC2(또는 ECS)가 ECR에서 이미지를 pull해 컨테이너로 실행한다.
3. ALB가 443(HTTPS) 요청을 받아 Target Group에 등록된 EC2/ECS의 애플리케이션 포트(예: 8080, 8000)로 전달한다.
4. Route 53이 `api.example.com`을 ALB로 연결한다(Alias 레코드).
5. 애플리케이션은 Private Subnet의 RDS에 접속하고, 파일은 S3에 저장한다.
6. 로그와 메트릭은 CloudWatch에서 확인한다.

## 장애/문제 상황

### 자주 겪는 기본 구조 관련 문제
- 콘솔에서 EC2가 보이지 않음: 다른 Region을 보고 있는 경우가 대부분이다.
- CLI 명령이 "자원을 찾을 수 없음"으로 실패: `--region` 또는 `AWS_REGION`/`~/.aws/config`의 기본 리전이 다르다.
- 단일 AZ에만 서버를 배치해 AZ 장애 시 서비스 전체가 중단됨.
- 테스트용 자원을 삭제하지 않아 예상치 못한 비용 발생(NAT Gateway, 미사용 Elastic IP, 큰 EBS 스냅샷 등).

## 확인 방법

### 기본 확인 명령어
현재 CLI가 어떤 계정/사용자(역할)로 인증되어 있는지 확인
```bash
aws sts get-caller-identity
```
현재 설정된 기본 리전 확인
```bash
aws configure get region
```
특정 리전의 EC2 인스턴스 목록 확인
```bash
aws ec2 describe-instances --region ap-northeast-2 \
  --query "Reservations[].Instances[].[InstanceId,State.Name,PublicIpAddress]" --output table
```
사용 가능한 AZ 확인
```bash
aws ec2 describe-availability-zones --region ap-northeast-2
```

## 핵심 정리

### 퀴즈 출제용 핵심 사실
- Region은 지리적 영역, AZ는 Region 안의 물리적으로 분리된 데이터센터 그룹이다.
- 서울 리전 코드는 `ap-northeast-2`이다.
- Subnet은 하나의 AZ에 속하며, 고가용성을 위해 여러 AZ에 자원을 분산한다.
- IAM과 Route 53은 글로벌 서비스, EC2·VPC·RDS·ECR은 리전 서비스다.
- 공동 책임 모델에서 Security Group 설정과 OS 패치는 사용자 책임이다.
- `aws sts get-caller-identity`는 현재 인증된 AWS 계정과 IAM 주체를 확인하는 명령어다.
- 일반 웹 서비스 흐름: Route 53 → ALB → EC2/ECS → RDS, 정적 파일은 S3, 로그는 CloudWatch.
