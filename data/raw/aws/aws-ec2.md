# AWS EC2: 인스턴스 운영, SSH 접속, 애플리케이션 배포와 외부 접속 장애 대응

## 개념

### EC2 (Elastic Compute Cloud)
EC2는 AWS에서 가상 서버(인스턴스)를 생성해 사용하는 컴퓨팅 서비스다. 인스턴스를 만들 때 다음을 선택한다.
- AMI(Amazon Machine Image): OS와 기본 소프트웨어가 담긴 이미지. 예: Amazon Linux 2023, Ubuntu 22.04/24.04.
- 인스턴스 유형: CPU/메모리 사양. 예: `t3.micro`, `t3.medium`, `m7i.large`. `t` 계열은 CPU 크레딧 기반 버스터블 인스턴스다.
- 키 페어(Key Pair): SSH 접속용 공개키/개인키. 개인키(`.pem`)는 생성 시 한 번만 다운로드할 수 있다.
- EBS 볼륨: 인스턴스에 연결되는 블록 스토리지(디스크). 루트 볼륨 크기가 작으면 Docker 이미지로 디스크가 금방 가득 찬다.
- 네트워크: VPC, Subnet, 퍼블릭 IP 자동 할당 여부, Security Group.
- IAM Instance Profile: 인스턴스가 AWS API(ECR, S3, CloudWatch)를 호출할 때 사용할 IAM Role.
- User Data: 인스턴스 최초 부팅 시 실행되는 스크립트(Docker 설치 등 초기 설정 자동화).

### 인스턴스 상태와 IP
- 상태: `pending` → `running` → `stopping` → `stopped` → (`terminated`).
- Stop(중지): 인스턴스를 끄지만 EBS 데이터는 유지된다. 자동 할당된 퍼블릭 IPv4는 Stop/Start 시 바뀐다. 재부팅(reboot)에서는 유지된다.
- Terminate(종료): 인스턴스를 삭제한다. 기본 설정에서 루트 EBS도 함께 삭제된다.
- 고정 퍼블릭 IP가 필요하면 Elastic IP를 연결한다. 운영 서비스는 보통 IP 대신 ALB + Route 53 도메인으로 접근한다.

## 왜 필요한가

### EC2를 사용하는 실무 이유
- OS 수준까지 직접 제어할 수 있어 Docker, Nginx, 모니터링 에이전트 등 원하는 소프트웨어를 자유롭게 설치할 수 있다.
- 소규모 서비스, 사이드 프로젝트, 학습용 배포에서 가장 이해하기 쉬운 배포 대상이다.
- ECS(EC2 launch type)나 Auto Scaling Group의 기반이 된다.

## 핵심 포인트

### EC2 운영 핵심
- SSH 접속은 22번 포트를 사용하며 Security Group 인바운드에서 허용해야 한다.
- 기본 사용자명: Amazon Linux는 `ec2-user`, Ubuntu는 `ubuntu`.
- 개인키 권한이 너무 넓으면 `UNPROTECTED PRIVATE KEY FILE` 오류가 나므로 `chmod 400 key.pem`을 적용한다.
- 22번 포트를 열지 않고 접속하려면 SSM Session Manager(인스턴스에 SSM Agent와 `AmazonSSMManagedInstanceCore` 권한 필요)나 EC2 Instance Connect를 사용한다.
- 상태 검사(Status Check): 시스템 상태 검사(AWS 하드웨어/네트워크 문제)와 인스턴스 상태 검사(OS 부팅 실패, 메모리 고갈 등 인스턴스 내부 문제)가 있다.
- 애플리케이션은 `0.0.0.0`에 바인딩해야 외부 인터페이스로 들어온 요청을 받을 수 있다. Uvicorn의 기본 host는 `127.0.0.1`이므로 `--host 0.0.0.0`을 지정한다.

## 실무 상황

### EC2 기반 애플리케이션 배포 절차 (Docker 사용)
1. 로컬에서 SSH 접속
```bash
chmod 400 my-key.pem
ssh -i my-key.pem ec2-user@<EC2-퍼블릭-IP>
```
2. Docker 설치 (Amazon Linux 2023)
```bash
sudo dnf install -y docker
sudo systemctl enable --now docker
sudo usermod -aG docker ec2-user   # 재접속 후 sudo 없이 docker 사용
```
3. 컨테이너 실행 (호스트 8080 → 컨테이너 8080)
```bash
docker run -d --name app --restart unless-stopped -p 8080:8080 \
  -e SPRING_PROFILES_ACTIVE=prod my-app:1.0.0
```
4. 서버 내부에서 헬스 체크
```bash
curl -i http://localhost:8080/actuator/health
```
외부 접속을 위해 Security Group 인바운드에 8080(또는 ALB 앞단 구성이라면 ALB SG로부터의 8080)을 허용한다.

### 배포 방식 비교
- JAR 직접 실행: `java -jar app.jar`를 `systemd` 서비스로 등록. 단순하지만 런타임 버전 관리가 서버마다 달라진다.
- Docker 컨테이너 실행: 이미지에 런타임과 의존성이 포함되어 환경 차이가 줄어든다. ECR에서 이미지를 pull해 실행하는 방식이 일반적이다.

## 장애/문제 상황

### 사례: EC2에 애플리케이션을 배포했는데 외부에서 접속되지 않는 경우
외부 → EC2 → 컨테이너 → 애플리케이션까지 트래픽 경로를 바깥에서 안쪽 순서로 확인한다.
1. 인스턴스 상태가 `running`이고 상태 검사 2/2 통과인지 확인.
2. 퍼블릭 IP가 있는지, Stop/Start 후 IP가 바뀌지 않았는지 확인.
3. Subnet 라우팅 테이블에 `0.0.0.0/0 → Internet Gateway` 경로가 있는지(Public Subnet인지) 확인.
4. Security Group 인바운드에 해당 포트가 올바른 소스로 열려 있는지 확인.
5. NACL과 OS 방화벽(ufw/firewalld)이 막고 있지 않은지 확인.
6. 서버 내부에서 `curl localhost:8080`이 되는지 확인. 안 되면 애플리케이션/컨테이너 문제다.
7. `ss -tlnp`로 `0.0.0.0:8080`에서 수신 중인지 확인(127.0.0.1이면 바인딩 문제).
8. Docker라면 `docker ps`의 PORTS 열에 `0.0.0.0:8080->8080/tcp`가 있는지 확인.
9. `docker logs`, `journalctl`로 애플리케이션 오류를 확인.

### 사례: SSH 접속이 안 되는 경우
- `Connection timed out`: Security Group 22번 미허용, 내 IP 변경, Private Subnet, 퍼블릭 IP 없음 등 네트워크 문제.
- `Permission denied (publickey)`: 잘못된 키 파일 또는 잘못된 사용자명(`ec2-user` vs `ubuntu`).
- `UNPROTECTED PRIVATE KEY FILE`: 키 파일 권한 문제, `chmod 400` 필요.

### 사례: 디스크 또는 메모리 부족
- `no space left on device`: Docker 이미지/로그가 루트 볼륨을 채움. `df -h`, `docker system df` 확인 후 `docker image prune`, EBS 볼륨 확장.
- `t3.micro`(메모리 1GiB)에서 Spring Boot + DB를 함께 띄우면 OOM으로 프로세스가 종료될 수 있다. `free -h`, `dmesg | grep -i oom`으로 확인한다.

## 확인 방법

### EC2 점검 명령어 모음
인스턴스 상태와 상태 검사
```bash
aws ec2 describe-instance-status --instance-ids i-0123456789abcdef0
```
서버 내부 점검
```bash
sudo ss -tlnp                  # 수신 중인 포트와 바인딩 주소
curl -i http://localhost:8080/actuator/health
df -h                          # 디스크 사용량
free -h                        # 메모리 사용량
sudo journalctl -u my-app -n 100 --no-pager   # systemd 서비스 로그
docker ps -a && docker logs --tail 100 app    # 컨테이너 상태와 로그
```
외부에서 점검
```bash
curl -i http://<EC2-퍼블릭-IP>:8080/actuator/health
nc -zv <EC2-퍼블릭-IP> 22
```

## 핵심 정리

### 퀴즈 출제용 핵심 사실
- EC2 SSH 접속 기본 포트는 22이며, Amazon Linux 기본 사용자는 `ec2-user`, Ubuntu는 `ubuntu`이다.
- 자동 할당 퍼블릭 IP는 Stop/Start 시 변경되고, 고정 IP가 필요하면 Elastic IP를 사용한다.
- Stop은 EBS를 유지하고, Terminate는 인스턴스를 삭제한다.
- 서버 내부 `curl localhost`는 성공하는데 외부 접속이 실패하면 Security Group, 라우팅, 바인딩 주소, Port Mapping을 확인한다.
- 애플리케이션이 `127.0.0.1`에만 바인딩되면 외부에서 접속할 수 없으므로 `0.0.0.0`으로 바인딩한다.
- `ss -tlnp`는 수신 중인 TCP 포트와 프로세스를 확인하는 명령어다.
- SSH `Connection timed out`은 네트워크/Security Group 문제, `Permission denied (publickey)`는 키/사용자명 문제다.
