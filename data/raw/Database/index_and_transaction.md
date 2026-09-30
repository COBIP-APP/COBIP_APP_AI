# Database 인덱스 및 트랜잭션 격리 수준

## 1. B-Tree 인덱스 동작 원리
B-Tree 인덱스는 데이터를 정렬된 상태로 유지하며, 탐색(Search), 삽입(Insert), 삭제(Delete) 모두 $O(\log N)$의 시간 복잡도를 보장하는 실무 표준 자료구조입니다.
인덱스를 효율적으로 사용하기 위해서는 카디널리티(Cardinality)가 높고 선택도(Selectivity)가 우수한 컬럼을 선두 컬럼으로 배치해야 합니다.

실무에서 복합 인덱스(Composite Index)를 구성할 때는 동등 조건(`=`)으로 조회되는 컬럼을 앞쪽에 배치하고, 범위 조건(`<`, `>`, `BETWEEN`, `LIKE`)으로 조회되는 컬럼을 뒤쪽에 배치해야 인덱스 레인지 스캔(Index Range Scan)의 효율이 극대화됩니다.

## 2. 트랜잭션 격리 수준 (Transaction Isolation Levels)
ACID 원칙 중 고립성(Isolation)을 보장하기 위해 ANSI/ISO SQL 표준은 4가지 격리 수준을 정의합니다.
1. Read Uncommitted: 커밋되지 않은 데이터 조회 가능 (Dirty Read 발생)
2. Read Committed: 커밋된 데이터만 조회 가능 (Non-Repeatable Read 발생 가능)
3. Repeatable Read: 트랜잭션 시작 시점의 스냅샷 데이터 보장 (Phantom Read 발생 가능)
4. Serializable: 가장 엄격한 수준으로 모든 동시성 이상 현상을 방지하지만 동시 처리량이 급감

MySQL InnoDB 스토리지 엔진은 Undo 로그를 활용한 MVCC(Multi-Version Concurrency Control) 메커니즘과 Next-Key Lock(Record Lock + Gap Lock)을 통해 Repeatable Read 수준에서도 Phantom Read를 대부분 방지합니다.

## 3. 실무 트러블슈팅 및 데드락(Deadlock) 방지
트랜잭션 내에서 여러 테이블이나 레코드를 갱신할 때는 모든 비즈니스 로직에서 자원 접근 순서(Lock Order)를 일관되게 유지해야 데드락을 예방할 수 있습니다.
또한 불필요하게 긴 트랜잭션은 커넥션 풀 고갈과 Undo 영역 팽창을 야기하므로 외부 API 호출이나 네트워크 I/O 작업은 트랜잭션 범위 밖으로 분리해야 합니다.
