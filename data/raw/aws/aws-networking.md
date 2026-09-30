# AWS 네트워킹: VPC, Subnet, Internet Gateway, NAT Gateway, Elastic IP, Route 53

## 개념

### VPC (Virtual Private Cloud)
VPC는 AWS 안에 만드는 논리적으로 격리된 사설 네트워크다. 생성 시 IP 대역(CIDR)을 지정한다. 예: `10.0.0.0/16`(약 65,536개 IP). 하나의 Region에 속하며 여러 AZ에 걸쳐 Subnet을 만들 수 있다.

### Subnet
Subnet은 VPC의 IP 대역을 나눈 하위 네트워크이며 하나의 AZ에만 속한다. 예: `10.0.1.0/24`(ap-northeast-2a), `10.0.2.0/24`(ap-northeast-2c). AWS는 각 Subnet에서 5개 IP를 예약한다.
- Public Subnet: 연결된 라우팅 테이블에 `0.0.0.0/0 → Internet Gateway(igw-...)` 경로가 있는 Subnet. ALB, Bastion, NAT Gateway, 퍼블릭 IP가 있는 EC2를 둔다.
- Private Subnet: Internet Gateway로 가는 경로가 없는 Subnet. 인터넷에서 직접 접근할 수 없다. RDS, 내부 애플리케이션 서버, ECS Task를 둔다.
- Public/Private 구분은 이름이 아니라 라우팅 테이블 경로로 결정된다. 또한 Public Subnet의 인스턴스도 퍼블릭 IP(또는 Elastic IP)가 있어야 인터넷과 통신할 수 있다.

### 라우팅 테이블 (Route Table)
Subnet에서 나가는 트래픽의 목적지별 경로를 정의한다.
- `10.0.0.0/16 → local`: VPC 내부 통신(기본 경로, 삭제 불가).
- `0.0.0.0/0 → igw-xxxx`: 인터넷으로 가는 경로(Public Subnet).
- `0.0.0.0/0 → nat-xxxx`: NAT Gateway를 통한 아웃바운드 인터넷 경로(Private Subnet).

### Internet Gateway (IGW)
VPC와 인터넷 사이의 양방향 통신을 가능하게 하는 게이트웨이다. VPC당 하나를 연결한다. 퍼블릭 IP를 가진 인스턴스에 대해 인바운드와 아웃바운드 인터넷 통신을 모두 제공한다.

### NAT Gateway
Private Subnet의 자원이 인터넷으로 "나가는" 연결(아웃바운드)만 할 수 있게 해주는 관리형 서비스다.
- Public Subnet에 생성하고 Elastic IP를 연결한다(퍼블릭 NAT Gateway 기준).
- Private Subnet 라우팅 테이블에 `0.0.0.0/0 → NAT Gateway` 경로를 추가한다.
- 외부에서 Private Subnet의 자원으로 들어오는 새로운 연결(인바운드)은 제공하지 않는다. 내부에서 시작한 연결의 응답만 돌아온다.
- 용도: Private Subnet의 EC2가 패키지 설치(`dnf`, `apt`), 외부 API 호출, ECR 이미지 pull을 할 때.
- 시간당 요금과 처리 데이터 요금이 발생하므로 학습 환경에서는 삭제를 잊지 않는다. ECR/S3 트래픽은 VPC Endpoint로 NAT 비용을 줄일 수 있다.

### Elastic IP (EIP)
계정에 할당되는 고정 퍼블릭 IPv4 주소다. 인스턴스를 Stop/Start해도 주소가 유지된다. NAT Gateway에도 사용한다. 퍼블릭 IPv4는 사용 여부와 관계없이 시간당 요금이 부과되므로 사용하지 않는 EIP는 해제(release)한다.

### Route 53
AWS의 관리형 DNS 서비스다(글로벌 서비스).
- Hosted Zone: 도메인(`example.com`)의 DNS 레코드 묶음. Public Hosted Zone과 VPC 내부용 Private Hosted Zone이 있다.
- 주요 레코드: `A`(도메인 → IPv4), `AAAA`(IPv6), `CNAME`(도메인 → 다른 도메인), `MX`, `TXT`.
- Alias 레코드: Route 53 전용 기능으로 ALB, CloudFront, S3 웹사이트 등 AWS 자원에 도메인을 연결한다. 루트 도메인(zone apex, `example.com`)에도 사용할 수 있다. CNAME은 zone apex에 사용할 수 없다.
- TTL: 레코드를 캐시하는 시간. 값을 바꿔도 TTL 동안은 이전 값이 조회될 수 있다.
- 라우팅 정책: Simple, Weighted(가중치), Latency, Failover(헬스 체크 기반), Geolocation.

## 왜 필요한가

### 네트워크를 Public/Private으로 나누는 이유
- 인터넷에 노출할 자원(ALB)과 노출하면 안 되는 자원(DB, 내부 API)을 분리해 공격 표면을 줄인다.
- Private Subnet 자원도 업데이트나 외부 API 호출을 위해 인터넷으로 나가야 하므로 NAT Gateway가 필요하다.
- IP는 바뀔 수 있으므로 사용자는 Route 53 도메인으로 접근하게 하고, 도메인은 ALB에 Alias로 연결한다.

## 핵심 포인트

### 네트워킹 핵심
- Public Subnet = 라우팅 테이블에 IGW 경로가 있는 Subnet.
- Private Subnet = IGW 경로가 없는 Subnet, 아웃바운드가 필요하면 NAT Gateway 경로를 추가.
- NAT Gateway는 아웃바운드 전용이며, Public Subnet에 위치한다.
- 인터넷 사용자가 Private Subnet 서비스에 접근하려면 Public Subnet의 ALB를 거쳐야 한다.
- ALB를 쓰는 구성에서는 도메인을 Route 53 Alias로 ALB에 연결한다(ALB IP는 고정되지 않는다).
- Elastic IP는 고정 퍼블릭 IPv4이며 미사용 시에도 비용이 발생한다.

## 실무 상황

### 권장 VPC 구성 예시
```
VPC 10.0.0.0/16 (ap-northeast-2)
├─ Public Subnet A 10.0.1.0/24 (2a)  : ALB, NAT Gateway      → RT: 0.0.0.0/0 → IGW
├─ Public Subnet C 10.0.2.0/24 (2c)  : ALB                   → RT: 0.0.0.0/0 → IGW
├─ Private App A 10.0.11.0/24 (2a)   : EC2 / ECS Task        → RT: 0.0.0.0/0 → NAT GW
├─ Private App C 10.0.12.0/24 (2c)   : EC2 / ECS Task        → RT: 0.0.0.0/0 → NAT GW
└─ Private DB A/C 10.0.21.0/24, 10.0.22.0/24 : RDS (DB Subnet Group) → 인터넷 경로 없음
```
요청 흐름: `사용자 → Route 53(api.example.com) → ALB(Public, 443) → EC2/ECS(Private, 8080) → RDS(Private, 5432)`.

### 소규모/학습용 단순 구성
Public Subnet 하나에 퍼블릭 IP(또는 Elastic IP)를 가진 EC2 한 대를 두고 Docker로 애플리케이션을 실행한다. 구성은 간단하지만 서버가 인터넷에 직접 노출되므로 Security Group을 엄격히 설정한다.

## 장애/문제 상황

### 사례: 퍼블릭 IP가 있는데도 인터넷 접속이 안 되는 EC2
Subnet 라우팅 테이블에 IGW 경로가 없으면 퍼블릭 IP가 있어도 인터넷과 통신할 수 없다. VPC에 IGW가 연결(attach)되어 있는지도 확인한다.

### 사례: Private Subnet의 EC2에서 `docker pull`이나 `dnf install`이 타임아웃
Private Subnet 라우팅 테이블에 NAT Gateway 경로가 없거나, NAT Gateway가 Private Subnet에 잘못 생성되었거나, NAT Gateway가 있는 Public Subnet에 IGW 경로가 없는 경우다. ECR pull만 필요하다면 ECR/S3 VPC Endpoint로도 해결할 수 있다.

### 사례: NAT Gateway를 만들었는데 외부에서 Private 서버로 접속이 안 됨
정상 동작이다. NAT Gateway는 인바운드 연결을 제공하지 않는다. 외부 공개가 필요하면 ALB를 Public Subnet에 두고 Private 서버를 Target으로 등록한다.

### 사례: 도메인을 바꿨는데 이전 서버로 접속됨
DNS TTL 동안 캐시가 남아 있다. `dig`로 실제 응답 값과 TTL을 확인한다.

## 확인 방법

### 네트워크 점검 명령어
Subnet이 사용하는 라우팅 테이블과 경로 확인
```bash
aws ec2 describe-route-tables \
  --filters Name=association.subnet-id,Values=subnet-0123456789abcdef0 \
  --query "RouteTables[].Routes"
```
VPC에 연결된 Internet Gateway 확인
```bash
aws ec2 describe-internet-gateways \
  --filters Name=attachment.vpc-id,Values=vpc-0123456789abcdef0
```
NAT Gateway 상태 확인
```bash
aws ec2 describe-nat-gateways --query "NatGateways[].[NatGatewayId,State,SubnetId]"
```
Private 서버에서 아웃바운드 인터넷 확인
```bash
curl -I https://www.google.com
```
DNS 확인
```bash
dig +short api.example.com
nslookup api.example.com
```

## 핵심 정리

### 퀴즈 출제용 핵심 사실
- VPC는 AWS 안의 격리된 사설 네트워크이며 CIDR 대역으로 정의한다.
- Subnet은 하나의 AZ에 속한다.
- Public Subnet은 라우팅 테이블에 `0.0.0.0/0 → Internet Gateway` 경로가 있는 Subnet이다.
- Private Subnet은 인터넷에서 직접 접근할 수 없으며, 아웃바운드는 NAT Gateway를 사용한다.
- NAT Gateway는 아웃바운드 전용이며 인바운드 연결을 제공하지 않는다. Public Subnet에 생성한다.
- Internet Gateway는 VPC와 인터넷 간 양방향 통신을 제공한다.
- Elastic IP는 Stop/Start 후에도 유지되는 고정 퍼블릭 IPv4 주소다.
- Route 53 Alias 레코드로 도메인을 ALB에 연결하며, zone apex에는 CNAME 대신 Alias를 사용한다.
