# AWS 스토리지·데이터·모니터링: S3, RDS, CloudWatch 로그와 메트릭

## 개념

### S3 (Simple Storage Service)
S3는 파일을 객체(Object) 단위로 저장하는 객체 스토리지다.
- Bucket: 객체를 담는 컨테이너. 이름은 전 세계적으로 고유해야 하며 Region에 생성된다.
- Object/Key: 저장된 파일과 그 경로 역할의 키(`uploads/2026/09/profile.png`).
- 99.999999999%(11 9s) 내구성을 목표로 설계되어 있으며, 쓰기 직후 읽기에 강한 일관성(strong read-after-write consistency)을 제공한다.
- 퍼블릭 액세스 차단(Block Public Access)이 새 버킷에 기본 활성화되어 있다.
- 접근 제어: IAM 정책(주체 기준), 버킷 정책(리소스 기준), Presigned URL(제한 시간 동안만 업로드/다운로드 허용).
- 버전 관리(Versioning), 수명 주기 정책(Lifecycle), 스토리지 클래스(Standard, Standard-IA, Glacier 등)를 제공한다.
- EBS와의 차이: EBS는 EC2에 연결하는 블록 디스크(한 AZ), S3는 HTTP API로 접근하는 객체 저장소다.

### RDS (Relational Database Service)
RDS는 MySQL, PostgreSQL, MariaDB 등을 AWS가 관리해주는 관계형 DB 서비스다.
- AWS가 설치, 패치, 자동 백업, 장애 조치를 관리하고 사용자는 스키마, 쿼리, 파라미터를 관리한다.
- 엔드포인트: `mydb.xxxxxx.ap-northeast-2.rds.amazonaws.com` 형태의 DNS 이름으로 접속한다. IP가 아닌 엔드포인트를 사용해야 장애 조치 후에도 연결된다.
- DB Subnet Group: RDS가 배치될 Subnet 묶음(보통 Private Subnet 2개 이상, 서로 다른 AZ).
- Multi-AZ: 다른 AZ에 대기(standby) 인스턴스를 두고 장애 시 자동 장애 조치(failover). 고가용성 목적이며 읽기 성능 확장 용도가 아니다.
- Read Replica: 읽기 전용 복제본. 읽기 트래픽 분산 목적.
- 자동 백업과 특정 시점 복구(PITR), 수동 스냅샷을 지원한다.
- 기본 포트: MySQL 3306, PostgreSQL 5432.

### CloudWatch
CloudWatch는 AWS 자원과 애플리케이션의 메트릭, 로그, 알람을 수집하는 모니터링 서비스다.
- Metrics: EC2 `CPUUtilization`, `NetworkIn`, `StatusCheckFailed`; ALB `HTTPCode_Target_5XX_Count`, `TargetResponseTime`, `UnHealthyHostCount`; RDS `CPUUtilization`, `DatabaseConnections`, `FreeStorageSpace`.
- EC2의 메모리 사용률과 디스크 사용률은 기본 메트릭이 아니며, CloudWatch Agent를 설치해야 수집된다.
- Logs: Log Group(애플리케이션/서비스 단위) → Log Stream(인스턴스/컨테이너 단위) 구조. ECS의 `awslogs` 로그 드라이버나 CloudWatch Agent로 로그를 보낸다.
- Alarms: 메트릭이 임계값을 넘으면 SNS(이메일, Slack 연동)로 알리거나 Auto Scaling 동작을 트리거한다.
- Logs Insights: 로그를 쿼리 언어로 검색한다.

## 왜 필요한가

### 서버 밖에 데이터를 두는 이유
- 컨테이너나 EC2는 교체·삭제될 수 있으므로 업로드 파일은 S3, 영속 데이터는 RDS처럼 서버 밖에 둬야 배포 후에도 데이터가 유지된다.
- 여러 서버가 같은 파일과 데이터를 공유할 수 있다.
- CloudWatch가 없으면 장애 시 서버에 하나씩 SSH로 들어가 로그를 봐야 하며, 서버가 종료되면 로그도 사라진다.

## 핵심 포인트

### 스토리지/모니터링 핵심
- S3 버킷 이름은 전역 고유, 퍼블릭 액세스는 기본 차단.
- EC2 애플리케이션이 S3에 접근할 때는 IAM Role 권한을 사용한다.
- RDS는 Private Subnet에 두고 `publicly accessible`을 끄며, DB Security Group은 애플리케이션 SG만 허용한다.
- Multi-AZ는 가용성, Read Replica는 읽기 확장.
- 애플리케이션은 RDS 엔드포인트 DNS 이름으로 접속한다.
- CloudWatch 기본 EC2 메트릭에는 메모리/디스크 사용률이 없다.
- 컨테이너 로그는 stdout/stderr로 출력해 CloudWatch Logs로 수집하는 것이 표준이다.

## 실무 상황

### 파일 업로드 기능
프론트엔드가 백엔드에 Presigned URL을 요청하고, 백엔드가 제한 시간(예: 5분)짜리 PUT URL을 발급하면 브라우저가 S3에 직접 업로드한다. 서버 부하와 대역폭을 줄일 수 있다.

### 애플리케이션과 RDS 연결
Spring Boot의 `SPRING_DATASOURCE_URL=jdbc:postgresql://mydb.xxxxxx.ap-northeast-2.rds.amazonaws.com:5432/app`처럼 엔드포인트를 환경변수로 주입한다. 비밀번호는 코드가 아니라 Secrets Manager나 SSM Parameter Store에 둔다.

### 장애 알람
ALB `HTTPCode_Target_5XX_Count`가 5분간 10회 이상이면 알람, RDS `FreeStorageSpace`가 일정 이하이면 알람을 설정한다.

## 장애/문제 상황

### 사례: 애플리케이션이 RDS에 연결되지 않음 (Connection timed out)
- DB Security Group 인바운드에 3306/5432가 애플리케이션 SG로부터 허용되지 않음(가장 흔함).
- RDS가 다른 VPC에 있음.
- 로컬 PC에서 Private Subnet의 RDS로 직접 접속 시도(원래 불가, Bastion/SSM 포트 포워딩 필요).
- 엔드포인트 오타 또는 잘못된 포트.
- `Access denied for user`는 네트워크가 아니라 계정/비밀번호 문제다.

### 사례: S3 업로드 시 AccessDenied
EC2 Role에 `s3:PutObject` 권한이 없거나 리소스 ARN이 잘못됨(`arn:aws:s3:::bucket/*` 필요), 버킷 정책의 Deny, KMS 암호화 키 권한 부족 등을 확인한다.

### 사례: 서버가 느린데 CPU는 낮음
메모리 부족(스왑)이나 디스크 부족일 수 있으나 기본 메트릭에 없으므로 CloudWatch Agent를 설치하거나 서버에서 `free -h`, `df -h`를 확인한다.

## 확인 방법

### 점검 명령어
S3
```bash
aws s3 ls s3://my-bucket/uploads/
aws s3 cp ./test.txt s3://my-bucket/uploads/test.txt
```
RDS 엔드포인트/상태 확인
```bash
aws rds describe-db-instances \
  --query "DBInstances[].[DBInstanceIdentifier,DBInstanceStatus,Endpoint.Address,Endpoint.Port,MultiAZ]"
```
애플리케이션 서버에서 RDS 포트 도달 여부 확인
```bash
nc -zv mydb.xxxxxx.ap-northeast-2.rds.amazonaws.com 5432
```
CloudWatch Logs 실시간 확인 (AWS CLI v2)
```bash
aws logs tail /ecs/cobip-api --follow --since 10m
```
EC2 CPU 메트릭 조회
```bash
aws cloudwatch get-metric-statistics --namespace AWS/EC2 --metric-name CPUUtilization \
  --dimensions Name=InstanceId,Value=i-0123456789abcdef0 \
  --start-time 2026-09-30T00:00:00Z --end-time 2026-09-30T01:00:00Z \
  --period 300 --statistics Average
```

## 핵심 정리

### 퀴즈 출제용 핵심 사실
- S3는 객체 스토리지이고 버킷 이름은 전 세계에서 고유해야 한다.
- Presigned URL은 제한 시간 동안만 S3 객체 업로드/다운로드를 허용한다.
- RDS Multi-AZ는 고가용성(자동 장애 조치), Read Replica는 읽기 확장 목적이다.
- RDS는 Private Subnet에 두고 애플리케이션 Security Group에서만 DB 포트를 허용한다.
- MySQL 기본 포트는 3306, PostgreSQL은 5432이다.
- CloudWatch Logs는 Log Group → Log Stream 구조이며, `aws logs tail`로 실시간 확인한다.
- EC2 메모리/디스크 사용률은 CloudWatch Agent를 설치해야 수집된다.
- RDS `Connection timed out`은 주로 Security Group/네트워크 문제, `Access denied`는 인증 문제다.
