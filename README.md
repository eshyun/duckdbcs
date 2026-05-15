# DuckDB Quack Client/Server (`duckdbcs`)

**duckdbcs**는 [DuckDB Quack Remote Protocol](https://duckdb.org/docs/current/quack/overview)의 고수준 Python 클라이언트/서버 패키지입니다. Quack은 DuckDB v1.5.2+에 내장된 확장 기능으로, DuckDB 인스턴스를 HTTP 서버로 변환하여 **여러 프로세스가 동시에 읽고 쓸 수 있는 환경**을 제공합니다 (`Database is locked` 에러 없음).

---

## 목차

1. [설치](#설치)
2. [CLI 사용법 (상세)](#cli-사용법-상세)
   - [서버 명령어](#서버-명령어)
   - [클라이언트 명령어](#클라이언트-명령어)
   - [전체 워크플로우 예시](#전체-워크플로우-예시)
3. [Python 패키지 사용법 (상세)](#python-패키지-사용법-상세)
   - [QuackServer](#quackserver)
   - [QuackClient](#quackclient)
   - [QuackResult](#quackresult)
   - [sql() — 원샷 원격 쿼리](#sql--원샷-원격-쿼리)
   - [동적 데이터베이스 ATTACH (Master Server 패턴)](#동적-데이터베이스-attach-master-server-패턴)
   - [커스텀 인증/인가](#커스텀-인증인가)
   - [고급 사용법](#고급-사용법)
4. [설정 (환경변수)](#설정-환경변수)
5. [보안](#보안)
6. [참고 자료](#참고-자료)

---

## 설치

### pip 설치

```bash
pip install duckdbcs
```

### 소스에서 설치

```bash
git clone <repo-url>
cd duckdbcs
pip install -e .
# 개발 의존성 포함:
pip install -e ".[dev]"
```

### 요구사항

- Python 3.11+
- DuckDB **v1.5.2** 이상 (Quack은 `core_nightly` 저장소에서 제공)

> ⚠️ **참고:** Quack은 현재 개발 중(beta)입니다. 프로토콜, 함수명, 설정 등이 변경될 수 있습니다.

---

## CLI 사용법 (상세)

CLI는 `duckdbcs` 명령어로 사용하며, `server`와 `client` 두 개의 하위 명령어 그룹으로 구성됩니다.

```bash
duckdbcs --help
duckdbcs server --help
duckdbcs client --help
```

### 서버 명령어

#### `duckdbcs server start` — 서버 시작

Quack 서버를 시작합니다. 서버는 메모리 DB(`:memory:`)로 시작하며, 클라이언트의 연결을 기다립니다.

```bash
# 기본 실행 (localhost:9494, 토큰 자동 생성)
duckdbcs server start

# 호스트/포트/토큰 지정
duckdbcs server start \
  --host 0.0.0.0 \
  --port 9494 \
  --token "my_secure_token"

# 외부 접속 허용 (프로덕션에서는 반드시 HTTPS 리버스 프록시 필요)
duckdbcs server start \
  --host 0.0.0.0 \
  --port 9494 \
  --token "my_secure_token" \
  --allow-external

# 시작과 동시에 DB 파일 ATTACH (--attach path:alias 형식, 여러 번 가능)
duckdbcs server start \
  --host 0.0.0.0 \
  --port 9494 \
  --token "my_secure_token" \
  --attach /data/analytics.db:analytics \
  --attach /data/users.db:users

# 상세 로그 출력
duckdbcs server start --verbose
```

**옵션:**

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--host` | `0.0.0.0` | 바인딩할 호스트 주소 |
| `--port` | `9494` | 리슨 포트 |
| `--token` | `QUACK_TOKEN` 환경변수 또는 자동 생성 | 인증 토큰 |
| `--allow-external` | `False` | 외부 접속 허용 여부 |
| `--attach` | — | 시작 시 ATTACH할 DB 파일 (`path:alias` 형식, 반복 가능) |
| `--verbose`, `-v` | `False` | 상세 로그 출력 |

#### `duckdbcs server stop` — 서버 중지

> CLI에서 직접 서버를 중지하는 기능은 Python API를 통해 사용하세요.

```bash
duckdbcs server stop
# 출력: Use the Python API to stop the server:
#   from duckdbcs import QuackServer
#   with QuackServer(token='...') as server:
#       server.start()
#       server.stop()
```

#### `duckdbcs server status` — 서버 상태 확인

```bash
duckdbcs server status
```

#### `duckdbcs server attach` — DB 파일 ATTACH

```bash
duckdbcs server attach /path/to/database.db --as mydb
```

#### `duckdbcs server detach` — DB DETACH

```bash
duckdbcs server detach mydb
```

#### `duckdbcs config add-client-attach` — 클라이언트 auto-attach 목록에 추가

```bash
duckdbcs config add-client-attach /data/analytics.db --as analytics
# Added client auto-attach: analytics -> /data/analytics.db
# This database will be attached automatically on 'duckdbcs client connect'.
```

#### `duckdbcs config remove-client-attach` — 클라이언트 auto-attach 목록에서 제거

```bash
duckdbcs config remove-client-attach analytics
# Removed client auto-attach: analytics
```


---

### 클라이언트 명령어

#### `duckdbcs client connect` — 서버에 연결

Quack 서버에 ATTACH하여 원격 카탈로그로 연결합니다.

```bash
# 기본 연결 (localhost:9494)
duckdbcs client connect

# 호스트/포트/토큰 지정
# --attach 로 연결 후 자동으로 서버 측 DB ATTACH
duckdbcs client connect 192.168.1.100 \
  --port 9494 \
  --token "my_secure_token" \
  --attach /data/analytics.db:analytics \
  --attach /data/logs.db:logs

# 카탈로그 별칭 지정
# config add-client-attach 로 등록된 DB들은 자동으로 ATTACH됨
duckdbcs client connect localhost \
  --port 9494 \
  --alias my_server

**옵션:**

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `host` (인자) | `localhost` | 서버 호스트명 |
| `--port` | `9494` | 서버 포트 |
| `--token` | `QUACK_TOKEN` 환경변수 | 인증 토큰 |
| `--alias` | `remote_server` | 로컬 카탈로그 별칭 |
| `--attach` | — | 연결 후 서버 측에 ATTACH할 DB 파일 (`path:alias` 형식, 반복 가능) |
| `--verbose`, `-v` | `False` | 상세 로그 |
#### `duckdbcs client query` — SELECT 쿼리 실행

연결된 서버에 SELECT 쿼리를 실행하고 결과를 테이블 형태로 출력합니다.

```bash
# 기본 쿼리
duckdbcs client query "SELECT 42 AS answer"

# 특정 데이터베이스의 테이블 조회
duckdbcs client query "SELECT * FROM listings.listings LIMIT 10"

# 또는 --database 옵션으로 특정 DB 지정
duckdbcs client query "SELECT * FROM listings LIMIT 10" --database listings

# 호스트/포트/토큰 직접 지정 (connect 없이)
duckdbcs client query "SELECT 42" --host localhost --port 9494 --token "my_token"
```

**출력 예시:**
```
answer
-------
42
```

#### `duckdbcs client execute` — SQL 실행 (INSERT, UPDATE, CREATE 등)

```bash
# 테이블 생성
duckdbcs client execute "CREATE TABLE remote_server.test AS SELECT * FROM range(10)"

# 데이터 삽입
duckdbcs client execute "INSERT INTO remote_server.test VALUES (42)"

# 데이터 수정
duckdbcs client execute "UPDATE remote_server.test SET i = i + 1 WHERE i = 0"
```

#### `duckdbcs client stateless` — Stateless 쿼리 (ATTACH 불필요)

ATTACH 없이 `quack_query()` 함수를 통해 직접 SQL을 전송합니다.

```bash
# 기본 stateless 쿼리
duckdbcs client stateless "SELECT 42 AS answer" --host localhost --port 9494 --token "my_token"

# 결과 예시:
# answer
# -------
# 42
```

#### `duckdbcs client attach` — 서버 측에 DB 파일 ATTACH 요청

클라이언트가 서버에게 특정 DB 파일을 열도록 요청합니다. 서버의 파일시스템 경로를 사용합니다.

```bash
# 서버 측 DB 파일 ATTACH 요청
duckdbcs client attach /Users/euhyun/.cache/stock-data/listings.duckdb \
  --as listings \
  --host localhost \
  --port 9494 \
  --token "my_token"

# 성공 시 출력:
# Server attached '/Users/euhyun/.cache/stock-data/listings.duckdb' as 'listings'.

# ATTACH 후 해당 DB의 테이블 조회
duckdbcs client query "SELECT * FROM listings.listings LIMIT 5"
```

#### `duckdbcs client detach` — 서버 측 DB DETACH 요청

```bash
duckdbcs client detach listings --host localhost --port 9494 --token "my_token"
# 출력: Server detached 'listings'.
```

#### `duckdbcs client tables` — 테이블 목록 조회

```bash
# 연결된 서버의 모든 테이블 조회
duckdbcs client tables --host localhost --port 9494 --token "my_token"

# 특정 데이터베이스의 테이블만 조회
duckdbcs client tables --database listings --host localhost --port 9494 --token "my_token"

# 출력 예시:
# Schema               Table
# --------------------------------------------------
# main                 listings
# information_schema   tables
# information_schema   schemata
```

#### `duckdbcs client databases` — 데이터베이스 목록 조회

```bash
duckdbcs client databases --host localhost --port 9494 --token "my_token"

# 출력 예시:
# listings (/Users/euhyun/.cache/stock-data/listings.duckdb)
# remote_server (:memory:)
```

#### `duckdbcs client disconnect` — 연결 해제

```bash
duckdbcs client disconnect --host localhost --port 9494 --token "my_token"
# 출력: Disconnected.
```

#### `duckdbcs client status` — 연결 상태 확인

```bash
duckdbcs client status --host localhost --port 9494 --token "my_token"

# 출력 예시:
# connected: True
# uri: quack:localhost:9494
# attach_alias: remote_server
# host: localhost
# port: 9494
# token_set: True
```

---

### 전체 워크플로우 예시

#### 시나리오: stock-data DB를 Quack으로 서빙하기

**터미널 1 — 서버 실행:**

```bash
# QUACK_TOKEN 환경변수 설정 (매번 --token 생략 가능)
export QUACK_TOKEN="my_dev_token"

# 서버 시작 (listings DB를 미리 ATTACH)
duckdbcs server start \
  --host 0.0.0.0 \
  --port 9494 \
  --attach /Users/euhyun/.cache/stock-data/listings.duckdb:listings \
  --verbose
```

**터미널 2 — 클라이언트로 조회:**

```bash
# 같은 토큰 설정
export QUACK_TOKEN="my_dev_token"

# 서버에 연결
duckdbcs client connect localhost --port 9494

# listings DB의 테이블 목록 확인
duckdbcs client tables --database listings

# 데이터 조회
duckdbcs client query "SELECT symbol, name FROM listings.listings LIMIT 10"

# 다른 클라이언트 프로세스에서도 동시에 INSERT 가능
duckdbcs client execute "INSERT INTO listings.listings VALUES ('AAPL', 'Apple Inc.')"

# stateless 쿼리로 빠르게 확인
duckdbcs client stateless "SELECT count(*) FROM listings.listings" --host localhost --port 9494

# 연결 해제
duckdbcs client disconnect
```

#### 시나리오: 여러 개의 DB 파일을 동적으로 ATTACH

```bash
export QUACK_TOKEN="my_dev_token"

# 서버 시작 (기본 메모리 DB)
duckdbcs server start --host 0.0.0.0 --port 9494

# (다른 터미널에서) 첫 번째 DB ATTACH 요청
duckdbcs client attach /data/project_a.db --as project_a

# 두 번째 DB ATTACH 요청
duckdbcs client attach /data/project_b.db --as project_b

# 두 DB의 데이터를 조인해서 조회
duckdbcs client query "
  SELECT a.id, a.name, b.score
  FROM project_a.users a
  JOIN project_b.scores b ON a.id = b.user_id
  LIMIT 20
"

# 사용 완료 후 DETACH
duckdbcs client detach project_a
duckdbcs client detach project_b
```

---

## Python 패키지 사용법 (상세)

#### `from_config()` — 설정 파일로 서버 자동 실행

```python
from duckdbcs import QuackServer

# ~/.config/duckdbcs/config.toml 의 server 섹션을 읽어서
# 서버 자동 시작 + attach_on_startup DB 자동 ATTACH
server = QuackServer.from_config()
# 서버가 실행 중이고, config에 지정된 DB들이 ATTACH된 상태
print(server.status())
server.close()
```


### QuackServer

`QuackServer`는 DuckDB 인스턴스를 Quack 프로토콜 서버로 실행합니다.

#### 기본 사용법

```python
from duckdbcs import QuackServer

# 서버 인스턴스 생성 (토큰 지정)
server = QuackServer(token="my_secure_token")

# 서버 시작
status = server.start(
    host="0.0.0.0",
    port=9494,
    allow_other_hostname=False,  # 외부 접속 허용하려면 True
    disable_ssl=False,
)
print(status)
# {
#     'running': True,
#     'listen_uri': 'quack:0.0.0.0:9494',
#     'url': 'http://0.0.0.0:9494',
#     'auth_token': 'my_secure_token',
#     'allow_other_hostname': False,
#     'attached_databases': {}
# }

# 서버 중지
server.stop()

# 컨텍스트 매니저로 사용 (자동 close)
with QuackServer(token="my_token") as server:
    server.start()
    # ... 서버 실행 중 ...
    # with 블록 종료 시 자동으로 stop() + close() 호출
```

#### DB 파일 ATTACH

```python
server = QuackServer(token="my_token")
server.start()

# 서버 측에서 직접 DB 파일 ATTACH
server.attach_database("/path/to/analytics.db", alias="analytics")
server.attach_database("/path/to/users.db", alias="users")

# alias 생략 시 파일명에서 자동 추출
server.attach_database("/path/to/listings.duckdb")  # alias = "listings"

# ATTACH된 DB 목록 확인
print(server.list_databases())  # ['analytics', 'users', 'listings']

# DETACH
server.detach_database("analytics")

# 서버 상태 확인
print(server.status())
```

#### 커스텀 인증 설정

```python
server = QuackServer(token="my_token")
server.start()

# 다중 토큰 인증 (MACRO 사용)
server.set_authentication("""
    CREATE MACRO multi_token_auth(sid, client_token, server_token) AS (
        client_token IN ('token_alice', 'token_bob', 'token_admin')
    );
""")

# 읽기 전용 권한 설정
server.set_authorization("""
    CREATE MACRO read_only(sid, query) AS (
        regexp_matches(upper(trim(query)), '^(SELECT|FROM|WITH|EXPLAIN|DESCRIBE|SHOW)\\b')
    );
""")
```

#### 서버를 포그라운드로 실행

```python
from duckdbcs.server import run_server_forever

# Ctrl+C 누를 때까지 실행
run_server_forever(
    host="0.0.0.0",
    port=9494,
    token="my_token",
    allow_other_hostname=True,
    databases=[
        ("/data/analytics.db", "analytics"),
        ("/data/users.db", "users"),
    ],
)
```

---

#### `from_config()` — 설정 파일로 클라이언트 자동 연결

```python
from duckdbcs import QuackClient

# ~/.config/duckdbcs/config.toml 의 client 섹션을 읽어서
# 자동 연결 + attach_on_startup DB들을 서버 측에 ATTACH
client = QuackClient.from_config()
# 클라이언트가 이미 연결되어 있고, config에 지정된 DB들이 ATTACH된 상태
print(client.query("SELECT 42").fetchall())
client.close()
```


### QuackClient

`QuackClient`는 Quack 서버에 연결하여 쿼리를 실행합니다.

#### 기본 사용법

```python
from duckdbcs import QuackClient

# 클라이언트 생성 (토큰은 서버와 동일해야 함)
client = QuackClient(token="my_secure_token")

# 서버에 연결 (ATTACH)
client.connect(
    host="localhost",
    port=9494,
    attach_alias="remote_server",  # 로컬 카탈로그 별칭
)

# 연결 상태 확인
print(client.status())
# {
#     'connected': True,
#     'uri': 'quack:localhost:9494',
#     'attach_alias': 'remote_server',
#     'host': 'localhost',
#     'port': 9494,
#     'token_set': True
# }

# SELECT 쿼리 실행 (결과는 dict 리스트로 반환)
results = client.query("SELECT 42 AS answer, 'hello' AS greeting")
print(results.fetchall())
# [{'answer': 42, 'greeting': 'hello'}]

# SQL 실행 (INSERT, UPDATE, CREATE 등)
client.execute("CREATE TABLE remote_server.test AS SELECT * FROM range(10)")
client.execute("INSERT INTO remote_server.test VALUES (42)")

# 연결 해제
client.disconnect()

# 컨텍스트 매니저 사용
with QuackClient(token="my_token") as client:
    client.connect("localhost", 9494)
    results = client.query("SELECT 42")
    results.show()
```

#### 자동 서버 시작/중지 (`auto_start_server`, `auto_stop_server`)

`host`를 지정할 때 서버가 실행 중이지 않으면 기본적으로 연결 오류가 발생합니다.
`auto_start_server=True`를 설정하면 서버가 없을 때 **자동으로 in-process 서버를 시작**한 후 연결합니다.

```python
from duckdbcs import QuackClient

# 서버가 없으면 자동으로 시작하고, 클라이언트 종료 시 자동 중지
client = QuackClient(
    token="my_secure_token",
    host="localhost",
    port=9494,
    auto_start_server=True,   # 서버가 없으면 자동 시작
    auto_stop_server=True,    # 클라이언트 종료 시 자동 중지 (기본값)
)

# 바로 쿼리 실행 가능 (서버가 자동으로 시작됨)
results = client.query("SELECT 42 AS answer")
print(results.fetchall())

# 클라이언트 종료 시 auto_start_server로 시작된 서버도 함께 종료됨
client.close()
```

> **참고:** `auto_start_server=True`로 시작된 서버는 `auto_stop_server=True`(기본값)일 때
> `client.close()` 또는 컨텍스트 매니저 종료 시 자동으로 중지됩니다.
> `auto_stop_server=False`로 설정하면 클라이언트가 종료되어도 서버가 계속 실행됩니다.


#### `client.sql()` — 기존 연결로 쿼리 실행 (instance method)

`client.sql()`은 **인스턴스 메서드**로, `client.query()`와 동일하게 기존 연결을 재사용합니다.
`duckdb.sql()`과 유사한 사용감을 제공합니다.

> **`client.query()`와의 관계:**
> `client.sql()`은 내부적으로 `self.query()`를 호출하므로 `client.query()`와 완전히 동일하게 동작합니다.
> 둘 다 기존 클라이언트 연결을 사용하며, `client.close()` 후에는 반환된 `QuackResult`를 사용할 수 없습니다.
> 사용자 취향에 따라 선택하세요.

```python
from duckdbcs import QuackClient

client = QuackClient(token="my_secure_token")
client.connect("localhost", 9494)

# client.sql()은 client.query()와 동일하게 기존 연결 사용
results = client.sql("SELECT 42 AS answer")
print(results.fetchall())  # [{'answer': 42}]

# 체이닝
client.sql("SELECT 42 AS n").query("SELECT n + 1 FROM result").show()

client.disconnect()
```

> **원샷(one-shot) 쿼리가 필요하다면?**
> `duckdbcs.sql()` 모듈 레벨 함수를 사용하세요. 임시 클라이언트를 생성하고 쿼리 실행 후
> 자동으로 종료하며, 반환된 `QuackResult`는 독립적인 로컬 연결을 기반으로 안전합니다.
> ```python
> from duckdbcs import sql
> results = sql("SELECT 42", token="my_token")
> ```

**파라미터:**

| 파라미터 | 기본값 | 설명 |
|---|---|---|
| `query` | — | 실행할 SQL 쿼리 |
| `host` | `localhost` | 서버 호스트명 |
| `port` | `9494` | 서버 포트 |
| `token` | `QUACK_TOKEN` 환경변수 | 인증 토큰 |
| `attach_alias` | `remote_server` | 로컬 카탈로그 별칭 |
| `database` | `None` | 라우팅할 카탈로그 (자동 라우팅 시도) |
| `disable_ssl` | `False` | HTTP 사용 강제 |
| `verbose` | `False` | 상세 로그 출력 |

#### Stateless 쿼리 (ATTACH 없이)

```python
client = QuackClient(token="my_token")

# ATTACH 없이 직접 SQL 전송
results = client.stateless_query(
    host="localhost",
    port=9494,
    sql="SELECT 42 AS answer",
    token="my_token",  # 생략 시 생성자에서 지정한 토큰 사용
)
print(results.fetchall())  # [{'answer': 42}]
```

#### 데이터베이스/테이블 목록 조회

```python
client = QuackClient(token="my_token")
client.connect("localhost", 9494)

# 서버에 연결된 모든 데이터베이스 조회
dbs = client.list_databases()
print(dbs)
# ['listings (/path/to/listings.duckdb)', 'remote_server (:memory:)']

# 특정 데이터베이스의 테이블 목록 조회
tables = client.list_tables(database="listings")
print(tables)
# [{'schema': 'main', 'table': 'listings'}, {'schema': 'main', 'table': 'prices'}]

# 연결된 서버의 모든 테이블 조회
tables = client.list_tables()  # 기본 attach_alias 사용
```

#### 자동 라우팅 (Auto-Routing)

클라이언트가 서버에 연결되면, 서버 측에만 존재하는 데이터베이스를 자동으로 감지합니다. `query()`와 `execute()`는 SQL에 참조된 데이터베이스가 서버 전용인지 확인하고, 필요시 자동으로 서버를 통해 라우팅합니다.

```python
# 서버가 /data/analytics.db를 'analytics'라는 이름으로 ATTACH한 상태
client = QuackClient(token="my_token")
client.connect("localhost", 9494)

# analytics DB가 서버 전용이면 자동으로 서버를 통해 라우팅됨
results = client.query("SELECT * FROM analytics.reports LIMIT 10")
print(results.fetchall())

# database 파라미터로 명시적 라우팅도 가능
results = client.query("SELECT * FROM reports LIMIT 10", database="remote_server")
```

**동작 방식:**

1. 연결 시 서버의 `duckdb_databases()`를 조회하여 서버 전용 DB 목록을 캐싱
2. SQL에서 참조된 데이터베이스(catalog)가 서버 전용이면 자동으로 `FROM remote_server.query('...')` 형태로 래핑
3. 로컬 실행 실패 시에도 자동으로 서버를 통해 재시도 (fallback)
4. `database` 파라미터로 명시적 라우팅 가능

---

### QuackResult

`QuackResult`는 원격 Quack 서버의 쿼리 결과를 래핑하는 클래스입니다. `duckdb.DuckDBPyRelation`과 유사한 변환 메서드를 제공합니다.

`client.query()`와 `sql()` 함수가 `QuackResult`를 반환합니다.

#### 출력 포맷 변환

```python
from duckdbcs import QuackClient, sql

client = QuackClient(token="my_token")
client.connect("localhost", 9494)

result = client.query("SELECT 42 AS answer, 'hello' AS greeting")

# Python 객체 (dict 리스트)
rows = result.fetchall()
print(rows)  # [{'answer': 42, 'greeting': 'hello'}]

# Pandas DataFrame
df = result.df()

# Polars DataFrame
pl_df = result.pl()

# Apache Arrow Table
arrow_table = result.arrow()

# NumPy structured arrays
numpy_arrays = result.fetchnumpy()

# Pretty print (duckdb 스타일)
result.show()
# ┌────────┬──────────┐
# │ answer │ greeting │
# ├────────┼──────────┤
# │   42   │ hello    │
# └────────┴──────────┘
```

#### 쿼리 체이닝

`QuackResult.query()`로 이전 결과를 `result`라는 이름의 뷰로 참조하여 체이닝할 수 있습니다.

```python
# 체이닝 예시
result = (
    client.query("SELECT 42 AS n")
    .query("SELECT n + 1 AS m FROM result")
    .query("SELECT m * 2 AS final FROM result")
)
result.show()

# 모듈 레벨 sql()과 함께 사용
from duckdbcs import sql
sql("SELECT 42 AS n").query("SELECT n + 1 FROM result").show()
```

#### 리스트형 프로토콜

`QuackResult`는 리스트처럼 동작합니다 (iterable, indexable, len, bool).

```python
result = client.query("SELECT * FROM range(5)")

# iteration
for row in result:
    print(row)

# indexing
first = result[0]

# 길이
count = len(result)

# truthy (데이터 존재 여부)
if result:
    print("데이터가 있습니다")
```

---

### `sql()` — 모듈 레벨 원샷 원격 쿼리

`duckdbcs.sql()`은 모듈 레벨 편의 함수로, `duckdb.sql()`과 유사하게 원격 Quack 서버에 한 번에 쿼리합니다. 임시 클라이언트를 생성하고 연결하여 쿼리를 실행한 후 `QuackResult`를 반환합니다.

> **`client.query()`와의 차이점:**
> - `client.query()`는 **인스턴스 메서드**로, 이미 연결된 클라이언트의 DuckDB 연결을 그대로 사용합니다.
>   반환된 `QuackResult`는 클라이언트 연결에 의존하므로, `client.close()` 후에는 사용할 수 없습니다.
> - `sql()`은 **모듈 레벨 함수**로, 내부적으로 임시 클라이언트를 생성 → 연결 → 쿼리 실행 →
>   **결과를 로컬 메모리로 eager materialize** → 임시 클라이언트 종료합니다.
>   반환된 `QuackResult`는 **독립적인 로컬 DuckDB 연결**을 기반으로 하므로,
>   원본 클라이언트와 무관하게 안전하게 사용할 수 있습니다.
> - `sql()`은 `host`, `port`, `token` 등을 매번 지정해야 하지만,
>   `client.query()`는 이미 연결된 클라이언트를 재사용합니다.

```python
from duckdbcs import sql

# 기본 사용
results = sql("SELECT 42 AS answer", token="my_token")
print(results.fetchall())  # [{'answer': 42}]

# Pandas DataFrame
df = sql("SELECT * FROM data", token="my_token").df()

# Polars DataFrame
pl_df = sql("SELECT * FROM data", token="my_token").pl()

# 서버 측 DB 조회
results = sql(
    "SELECT * FROM listings.market_listings LIMIT 10",
    host="localhost", port=9494, token="my_token",
)

# 체이닝
sql("SELECT 42 AS n").query("SELECT n + 1 FROM result").show()
```

> **참고:** `QuackClient.sql()`(클래스 메서드)과 `duckdbcs.sql()`(모듈 레벨 함수)은
> 내부적으로 동일한 `sql()` 함수를 호출하므로 완전히 동일하게 동작합니다.
> 사용자 편의에 따라 두 가지 방식 중 선택하여 사용할 수 있습니다.

**파라미터:**

| 파라미터 | 기본값 | 설명 |
|---|---|---|
| `query` | — | 실행할 SQL 쿼리 |
| `host` | `localhost` | 서버 호스트명 |
| `port` | `9494` | 서버 포트 |
| `token` | `QUACK_TOKEN` 환경변수 | 인증 토큰 |
| `attach_alias` | `remote_server` | 로컬 카탈로그 별칭 |
| `database` | `None` | 라우팅할 카탈로그 (자동 라우팅 시도) |
| `disable_ssl` | `False` | HTTP 사용 강제 |
| `verbose` | `False` | 상세 로그 출력 |


### 동적 데이터베이스 ATTACH (Master Server 패턴)

클라이언트가 서버에게 특정 DB 파일을 열도록 요청하는 패턴입니다. 서버의 파일시스템 경로를 사용합니다.

```python
client = QuackClient(token="my_token")
client.connect("localhost", 9494)

# 서버 측에 DB 파일 ATTACH 요청
alias = client.attach_remote("/path/to/project_a.db", alias="proj_a")
print(f"Server attached as '{alias}'")

# ATTACH 후 해당 DB의 테이블 조회
results = client.query("SELECT * FROM proj_a.users")
print(results.fetchall())

# 다른 클라이언트도 같은 DB를 볼 수 있음 (서버가 공유)
# 다른 프로세스에서도 동시에 INSERT 가능
client.execute("INSERT INTO proj_a.logs VALUES (1, 'data')")

# 사용 완료 후 DETACH
client.detach_remote("proj_a")
```

**전체 예제:**

```python
from duckdbcs import QuackClient
from duckdbcs import QuackServer
import threading
import time

# 서버를 별도 스레드에서 실행
def run_server():
    server = QuackServer(token="shared_token")
    server.start(host="0.0.0.0", port=9494)
    while True:
        time.sleep(1)

server_thread = threading.Thread(target=run_server, daemon=True)
server_thread.start()
time.sleep(1)  # 서버가 시작될 때까지 대기

# 클라이언트 1: DB ATTACH 요청
client1 = QuackClient(token="shared_token")
client1.connect("localhost", 9494)
client1.attach_remote("/data/project.db", "project")
client1.execute("INSERT INTO project.logs VALUES (1, 'from client1')")
print(client1.query("SELECT * FROM project.logs").fetchall())
client1.disconnect()

# 클라이언트 2: 같은 DB 조회 (동시 접근)
client2 = QuackClient(token="shared_token")
client2.connect("localhost", 9494)
print(client2.query("SELECT * FROM project.logs").fetchall())  # client1이 INSERT한 데이터 보임
client2.execute("INSERT INTO project.logs VALUES (2, 'from client2')")
client2.disconnect()
```

---

### 커스텀 인증/인가

#### 다중 사용자 토큰 인증

```python
server = QuackServer(token="admin_token")
server.start()

# 토큰 테이블 생성 및 인증 MACRO 등록
server.set_authentication("""
    CREATE TABLE IF NOT EXISTS auth_tokens (
        token VARCHAR PRIMARY KEY,
        username VARCHAR,
        role VARCHAR
    );
    INSERT INTO auth_tokens VALUES
        ('alice_key', 'alice', 'reader'),
        ('bob_key', 'bob', 'writer'),
        ('admin_key', 'admin', 'admin');

    CREATE MACRO table_auth(sid, client_token, server_token) AS (
        EXISTS (SELECT 1 FROM auth_tokens WHERE token = client_token)
    );
""")
```

#### 읽기 전용 모드

```python
server = QuackServer(token="my_token")
server.start()

server.set_authorization("""
    CREATE MACRO read_only(sid, query) AS (
        regexp_matches(upper(trim(query)), '^(SELECT|FROM|WITH|EXPLAIN|DESCRIBE|SHOW)\\b')
    );
""")
```

#### SQL 블랙리스트 기반 인가

```python
server.set_authorization("""
    CREATE MACRO block_dangerous(sid, query) AS (
        NOT regexp_matches(upper(query), 'DROP|DELETE|TRUNCATE|ALTER')
    );
""")
```

---

### 고급 사용법

#### 여러 서버에 동시 연결

```python
client1 = QuackClient(token="token_a")
client2 = QuackClient(token="token_b")

client1.connect("server-a.local", 9494, attach_alias="server_a")
client2.connect("server-b.local", 9494, attach_alias="server_b")

# 두 서버의 데이터 조인
results_a = client1.query("SELECT id, name FROM server_a.users")
results_b = client2.query("SELECT user_id, score FROM server_b.scores")

# Python에서 조인
combined = [
    {**a, **b}
    for a in results_a
    for b in results_b
    if a["id"] == b["user_id"]
]
```

#### 토큰을 환경변수로 관리

```python
import os

# .env 파일 또는 shell에서 설정
os.environ["QUACK_TOKEN"] = "my_secure_token"

# token 생략 가능 (자동으로 QUACK_TOKEN 읽음)
server = QuackServer()  # QUACK_TOKEN 환경변수 사용
server.start()

client = QuackClient()  # QUACK_TOKEN 환경변수 사용
client.connect("localhost", 9494)
```

#### `.quack_secret` 파일로 토큰 관리

```bash
# 프로젝트 루트에 .quack_secret 파일 생성
echo -n "my_secure_token" > .quack_secret
```

```python
# .quack_secret 파일이 있으면 자동으로 읽음
server = QuackServer()  # .quack_secret 파일에서 토큰 읽기
server.start()
```

---

## 설정 (환경변수)

| 변수 | 기본값 | 설명 | 사용처 |
|---|---|---|---|
| `QUACK_TOKEN` | — | 인증 토큰 | 서버/클라이언트 공용 |
| `QUACK_HOST` | `0.0.0.0` | 서버 바인딩 호스트 | 서버 |
| `QUACK_PORT` | `9494` | 서버 리슨 포트 | 서버 |
| `QUACK_ALLOW_EXTERNAL` | `false` | 외부 접속 허용 | 서버 |
| `QUACK_REMOTE_HOST` | `localhost` | 원격 서버 호스트명 | 클라이언트 |
| `QUACK_REMOTE_PORT` | `9494` | 원격 서버 포트 | 클라이언트 |
| `QUACK_ATTACH_ALIAS` | `remote_server` | 로컬 카탈로그 별칭 | 클라이언트 |

**우선순위:** CLI `--token` 옵션 > `QUACK_TOKEN` 환경변수 > `.quack_secret` 파일 > 자동 생성 (서버)

---

## 보안

- Quack은 기본적으로 **localhost 전용**으로 바인딩됩니다.
- 외부 접속이 필요하면 `--allow-external` 또는 `allow_other_hostname=True`를 설정하고, **반드시 TLS 리버스 프록시(nginx, Caddy)를 앞단에 배치**하세요.
- **암호화되지 않은 Quack 서버를 공개 인터넷에 노출하지 마세요.**
- 토큰은 `QUACK_TOKEN` 환경변수나 `.quack_secret` 파일로 관리하는 것을 권장합니다.
- 프로덕션 배포는 [공식 보안 가이드](https://duckdb.org/docs/current/quack/security)를 참고하세요.

---

## 참고 자료

- [Quack Remote Protocol 개요](https://duckdb.org/docs/current/quack/overview)
- [Quack 레퍼런스 (함수/설정)](https://duckdb.org/docs/current/quack/reference)
- [Quack 보안 가이드](https://duckdb.org/docs/current/quack/security)
- [DuckDB 공식 문서](https://duckdb.org/docs/)
