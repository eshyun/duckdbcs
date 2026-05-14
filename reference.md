# DuckDB Quack Remote Protocol 참고 문서

> **공식 문서**: https://duckdb.org/docs/current/quack/overview
>
> Quack은 DuckDB v1.5.2+ (core_nightly 저장소)에서 사용 가능한 공식 원격 프로토콜 확장 기능입니다.
> 2026년 5월 12일에 출시되었습니다.

## 1. Quack이란 무엇인가?

**Quack**은 DuckDB 인스턴스를 HTTP 서버로 변환하여, 다른 DuckDB 인스턴스(클라이언트)가 HTTP를 통해 연결할 수 있게 해주는 확장 기능입니다.

- **작동 방식**: 하나의 DuckDB 인스턴스를 서버로 띄우고, 클라이언트들이 HTTP/HTTPS를 통해 접속합니다.
- **핵심 이점**: 모든 데이터 수정 요청이 단일 서버 프로세스를 통해 처리되므로, 여러 프로세스가 동시에 `INSERT`, `UPDATE`를 수행해도 DuckDB의 트랜잭션 관리자가 안전하게 처리합니다.
- **기본 포트**: `9494`
- **URI 체계**: `quack:` (예: `quack:localhost:9494`)
- **직렬화**: `application/duckdb` 바이너리 프로토콜 (WAL과 동일한 코드 경로 사용)

## 2. 서버 사용법

### 서버 시작

```sql
LOAD quack;

-- 기본 (localhost 전용)
CALL quack_serve('quack:localhost');

-- 외부 접속 허용 (반드시 리버스 프록시로 TLS 보호 필요)
CALL quack_serve('quack:0.0.0.0:9494', allow_other_hostname => true);

-- 명시적 토큰 설정
CALL quack_serve('quack:0.0.0.0:9494', token := 'my_secure_token', allow_other_hostname => true);
```

`quack_serve()`는 listen URI, HTTP URL, auth_token을 반환합니다.

### URI 형식

| URI | 호스트 | 포트 |
|---|---|---|
| `quack:localhost` | localhost | 9494 |
| `quack://localhost` | localhost | 9494 |
| `quack:myhost:9000` | myhost | 9000 |
| `quack:127.0.0.1` | 127.0.0.1 | 9494 |
| `quack:[::1]:1234` | ::1 (IPv6) | 1234 |

검증 함수: `SELECT quack_uri_parser('quack:localhost', false);`

### 서버 중지

```sql
CALL quack_stop('quack:localhost');
```

### Node Identity (whoami)

각 Quack 노드는 `whoami()` 테이블 매크로를 통해 식별 정보를 노출합니다.

```sql
-- 서버 측에서 식별 정보 설정
CALL quack_identify(name => 'analytics-1', provider => 'ec2', region => 'eu-west-1');

-- 클라이언트가 서버의 식별 정보 조회
FROM remote_db.query('FROM whoami()');
```

## 3. 클라이언트 사용법

### Stateless 쿼리 (quack_query)

ATTACH 없이 직접 SQL 전송:

```sql
LOAD quack;

-- 로컬 서버 쿼리
FROM quack_query('quack:localhost', 'SELECT 42', token => 'MY_TOKEN');

-- 원격 서버 (HTTPS 자동 적용)
FROM quack_query('quack:remote.com', 'SELECT 42', token => 'MY_TOKEN');

-- 원격 서버 HTTPS 끄기
FROM quack_query('quack:remote.com', 'SELECT 42', token => 'MY_TOKEN', disable_ssl => true);
```

### ATTACH를 통한 원격 카탈로그 연결

서버를 로컬 카탈로그처럼 연결:

```sql
LOAD quack;

-- 로컬 서버 연결
ATTACH 'quack:localhost' AS remote_db (TOKEN 'MY_TOKEN');

-- 원격 서버 (HTTPS)
ATTACH 'quack:remote.com' AS remote_db (TOKEN 'MY_TOKEN');

-- 원격 서버 (HTTP 강제)
ATTACH 'quack:remote.com' AS remote_db (TOKEN 'MY_TOKEN', DISABLE_SSL true);
```

연결 후 사용:

```sql
-- DDL (원격에서 실행)
CREATE TABLE remote_db.t AS SELECT * FROM range(10);

-- DML (원격 쓰기)
INSERT INTO remote_db.t VALUES (42);

-- SELECT (원격 테이블 스캔)
FROM remote_db.t WHERE i = 42;

-- 트랜잭션 (원격으로 전달)
BEGIN;
INSERT INTO remote_db.t VALUES (1);
COMMIT;

-- Catalog-scoped query
FROM remote_db.query('SELECT 42');

-- 연결 해제
DETACH remote_db;
```

### 인증 (Authentication)

두 가지 방법으로 토큰 전달:

**방법 1 — Secret 사용 (권장):**
```sql
CREATE SECRET (TYPE quack, TOKEN 'MY_TOKEN', SCOPE 'quack:localhost');
ATTACH 'quack:localhost' AS remote_db (TYPE quack);
```

**방법 2 — TOKEN 옵션 직접 전달:**
```sql
ATTACH 'quack:localhost' AS remote_db (TOKEN 'MY_TOKEN');
```

## 4. 동적 파일 서빙 (Master Server 패턴)

클라이언트가 서버 측에 새로운 DB 파일을 ATTACH하도록 요청하려면, 서버의 `query()` 테이블 함수를 사용합니다.

### 서버 측 준비

서버는 메모리 DB나 관리용 파일 DB로 시작합니다:

```sql
LOAD quack;
CALL quack_serve('quack:0.0.0.0:9494', token := 'your_secure_token', allow_other_hostname => true);
```

### 클라이언트가 동적 ATTACH 요청

```sql
-- 서버에 연결
ATTACH 'quack:localhost' AS remote_server (TOKEN 'your_secure_token');

-- 서버 측에서 ATTACH 실행 (서버의 query() 함수 사용)
FROM remote_server.query('ATTACH ''/path/to/dynamic_file.db'' AS proj_a');

-- 이제 모든 클라이언트가 proj_a.test_table 사용 가능
INSERT INTO proj_a.logs VALUES (1, 'process_1_data');
```

> **참고**: 서버 측 ATTACH는 서버의 파일시스템 경로를 사용합니다.
> 클라이언트가 직접 파일 경로를 지정하는 것이 아니라, 서버에 명령을 보내 서버가 해당 파일을 열도록 합니다.

## 5. Python 예제

### 서버 (server.py)

```python
import duckdb

con = duckdb.connect(':memory:')
con.execute("LOAD quack;")

# 토큰은 quack_serve 호출 시 전달
con.execute("CALL quack_serve('quack:0.0.0.0:9494', token := 'secret_key', allow_other_hostname => true);")

print("DuckDB Quack Server is running on port 9494...")

import time
while True:
    time.sleep(60)
```

### 클라이언트 (client.py)

```python
import duckdb

con = duckdb.connect(':memory:')
con.execute("LOAD quack;")
con.execute("ATTACH 'quack:localhost' AS remote_server (TOKEN 'secret_key');")

# stateless 쿼리 (ATTACH 불필요)
result = con.execute("FROM quack_query('quack:localhost', 'SELECT 42', token => 'secret_key')").fetchall()

# ATTACH 후 일반 쿼리
con.execute("INSERT INTO remote_server.my_table VALUES (1, 'data')")
rows = con.execute("FROM remote_server.my_table").fetchall()

# 서버 측에 동적으로 DB 파일 ATTACH 요청
con.execute("FROM remote_server.query('ATTACH ''data/project_a.db'' AS proj_a')")
rows = con.execute("FROM proj_a.my_table").fetchall()
```

## 6. 보안

- 기본적으로 **localhost 전용** 바인딩
- 외부 접속 시 `allow_other_hostname => true` 필요
- 외부망 노출 시 **반드시 TLS 리버스 프록시** (nginx, Caddy) 사용
- 토큰 기반 인증 (기본: `quack_check_token` 함수)
- 커스텀 인증/인가 함수로 대체 가능 (SQL MACRO 지원)

### 커스텀 인증 예제 (MACRO)

```sql
-- 다중 토큰 테이블 기반 인증
CREATE TABLE allowed_tokens (token VARCHAR, user_name VARCHAR);
INSERT INTO allowed_tokens VALUES ('key-1', 'alice'), ('key-2', 'bob');

CREATE MACRO check_token(sid, client_token, server_token) AS (
    EXISTS (SELECT 1 FROM allowed_tokens WHERE token = client_token)
);
SET GLOBAL quack_authentication_function = 'check_token';
```

### 읽기 전용 권한 설정

```sql
CREATE MACRO read_only(sid, query) AS (
    regexp_matches(upper(trim(query)), '^(SELECT|FROM|WITH|EXPLAIN|DESCRIBE|SHOW)\b')
);
SET GLOBAL quack_authorization_function = 'read_only';
```

## 7. 함수 레퍼런스

### 서버 관리 함수

| 함수 | 설명 |
|---|---|
| `quack_serve(uri, token, allow_other_hostname, disable_ssl)` | 서버 시작 |
| `quack_stop(uri)` | 서버 중지 |
| `quack_identify(name, provider, hostname, region, meta)` | 노드 식별 정보 설정 |
| `whoami()` | 현재 노드의 식별 + 런타임 정보 반환 |

### 클라이언트 함수

| 함수 | 설명 |
|---|---|
| `quack_query(uri, query, token, disable_ssl)` | 원격 서버에 stateless 쿼리 실행 |
| `quack_query_by_name(catalog, query)` | ATTACH된 카탈로그에 쿼리 실행 |

### 유틸리티 함수

| 함수 | 설명 |
|---|---|
| `quack_uri_parser(uri, ssl)` | Quack URI 파싱 (host, port, ipv6, ssl, url) |
| `quack_check_token(sid, client_token, server_token)` | 기본 인증 함수 |
| `quack_nop_authorization(sid, query)` | 기본 인가 함수 (항상 허용) |

### ATTACH 옵션

| 옵션 | 타입 | 기본값 | 설명 |
|---|---|---|---|
| `TOKEN` | VARCHAR | (설정 안 됨) | 인증 토큰 |
| `DISABLE_SSL` | BOOLEAN | localhost: true, remote: false | HTTP/HTTPS 강제 |
| `TYPE` | VARCHAR | 자동 | Secret 타입 지정 |

## 8. 주의사항

- ⚠️ Quack은 **현재 개발 중(beta)**입니다. 프로토콜, 함수명, 설정 등이 변경될 수 있습니다.
- ⚠️ 모든 쓰기 작업이 단일 서버 프로세스를 통과하므로 동시성 문제가 해결되지만, 서버 자체가 단일 장애점(SPOF)이 됩니다.
- ⚠️ 프로덕션 환경에서는 **반드시 HTTPS 리버스 프록시**를 사용하세요.
- HTTP 기반이지만 DuckDB 전용 바이너리 프로토콜(`application/duckdb`)을 사용하므로 JSON 기반 API보다 훨씬 빠르고 데이터 손실이 없습니다.
- FETCH 응답은 여러 DataChunk를 배치 처리합니다 (`quack_fetch_batch_chunks` 설정, 기본값 12).
