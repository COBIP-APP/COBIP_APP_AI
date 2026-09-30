# Spring Boot AOP와 @Transactional 주의사항

## 1. Spring AOP 프록시 기반 메커니즘
Spring은 선언적 트랜잭션 관리(@Transactional)를 위해 런타임에 다이내믹 프록시(JDK Dynamic Proxy) 또는 CGLIB 프록시를 생성합니다.
클라이언트가 빈의 메서드를 호출하면 프록시 객체가 트랜잭션을 시작(Begin)하고, 실제 타깃 인스턴스의 비즈니스 로직을 호출한 후, 정상 종료 시 커밋(Commit), 런타임 예외 발생 시 롤백(Rollback)을 수행합니다.

## 2. Self-Invocation(내부 메서드 호출) 이슈
가장 흔히 발생하는 실무 장애 중 하나는 동일한 클래스 내에서 @Transactional이 없는 메서드가 @Transactional이 붙은 메서드를 호출(this.targetMethod())하는 경우입니다.
이때 호출은 프록시 객체를 거치지 않고 타깃 객체 내부에서 직접 발생하므로 트랜잭션 AOP 어드바이스가 전혀 적용되지 않습니다. 이를 해결하려면 별도 서비스 빈으로 책임을 분리하거나 TransactionTemplate을 프로그래밍 방식으로 사용해야 합니다.

## 3. 예외 롤백 기본 정책과 실무 팁
기본적으로 @Transactional은 언체크 예외(RuntimeException 및 Error) 발생 시에만 롤백을 수행하며, 체크 예외(Checked Exception - Exception의 하위 클래스 중 RuntimeException 제외)에 대해서는 커밋을 시도합니다.
따라서 비즈니스 체크 예외에서도 롤백이 필요하다면 `@Transactional(rollbackFor = Exception.class)` 옵션을 명시적으로 지정해야 데이터 정합성 불일치를 방지할 수 있습니다.
