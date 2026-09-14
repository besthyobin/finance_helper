# KIS 1분봉 수집기 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 평일 장 마감 후 `symbols.txt` 종목의 당일 1분봉을 KIS Open API에서 받아 PostgreSQL에 저장하고, 당일 최종 실행 후 실패가 남으면 텔레그램으로 알리는 Python 수집기를 만든다.

**Architecture:** `trader/` 폴더의 Python 스크립트 4개. `kis.py`(KIS 통신: 토큰 캐시·호출 간격·재시도·페이지 순회), `store.py`(DB 저장/조회), `notify.py`(텔레그램), `collector.py`(진입점: 종목 순회·상태 기록·알림). 작업 스케줄러가 평일 16:00~23:00 매시 실행하고, 매 실행은 오늘 완료되지 않은 종목만 수집한다.

**Tech Stack:** Python 3.11, requests, psycopg 3, python-dotenv, pytest, PostgreSQL 15

**Spec:** `docs/superpowers/specs/2026-09-14-kis-minute-collector-design.md`

### 스펙과 다르게 구체화한 점

- 스펙의 `get_token()`/`fetch_minute_bars(symbol)` 함수는 테스트 대역 주입을 위해 `KisClient` 클래스의 메서드로 두고, `fetch_minute_bars(symbol, today)`로 날짜를 인자로 받는다.
- 테스트 DB 주소를 `.env`의 `TEST_DATABASE_URL`로 받는다.
- 테스트는 `tests/test_kis.py`, `tests/test_store.py`, `tests/test_collector.py`로 나눈다. KIS 응답은 필드 형태 검증용 샘플 JSON 1개(`tests/fixtures/minute_page.json`)와, 페이지 순회용으로 코드에서 생성한 행을 함께 쓴다.

## Global Constraints

- Python 3.11 (이 PC `python --version` = 3.11.0), 가상환경 `trader/.venv`
- 의존성은 `requests`, `psycopg[binary]`, `python-dotenv`, `pytest` 4개만. 모킹 라이브러리·ORM·비동기·설정 클래스 금지
- DB: 이 PC의 PostgreSQL 15 서비스 `postgresql-x64-15`, `localhost:5432`, psql 경로 `D:\PIE\PostgreSQL_15\bin\psql.exe`
- 시간대: `KST = timezone(timedelta(hours=9))` (Windows에 tz 데이터베이스가 없으므로 `zoneinfo` 사용 금지)
- 앱키·시크릿·접근 토큰·텔레그램 토큰을 로그, 예외 메시지, DB `error` 컬럼에 넣지 않는다
- 함수/메서드마다 무엇을 하는지 한국어 한 줄 주석(docstring)을 단다
- 커밋 메시지는 한국어로 간결하게. 커밋에는 해당 태스크 파일만 `git add <경로>`로 추가한다 (작업 트리에 무관한 FMP 수정 파일이 있음, `git add -A` 금지)
- `main`에서 바로 커밋하지 말고 작업 브랜치 `feat/kis-minute-collector`에서 작업한다
- 모든 명령은 PowerShell 기준, 작업 디렉터리는 `D:\dev\antigravity_workspace\finance_helper\trader`
- KIS 호출 경로·TR ID·파라미터·필드명·오류 코드는 스펙 1절 "미확인 사항" 값을 그대로 쓴다. 실제 값 검증은 Task 6 수동 검증에서 한다

---

### Task 1: 프로젝트 골격, 스키마, 테스트 DB

**Files:**
- Create: `trader/requirements.txt`
- Create: `trader/.env.example`
- Create: `trader/.gitignore`
- Create: `trader/schema.sql`
- Create: `trader/tests/conftest.py`
- Test: `trader/tests/test_store.py`

**Interfaces:**
- Consumes: 없음
- Produces: pytest 픽스처 `conn` — 스키마가 적용되고 두 테이블이 비워진 `psycopg.Connection`(autocommit=True). 테이블 `minute_bars(symbol, ts, open, high, low, close, volume)`, `collect_runs(symbol, trade_date, bar_count, status, error, collected_at)`

- [ ] **Step 1: 브랜치 생성**

```powershell
git switch -c feat/kis-minute-collector
```

- [ ] **Step 2: 의존성·환경 파일 작성**

`trader/requirements.txt`:

```
requests>=2.32,<3
psycopg[binary]>=3.2,<4
python-dotenv>=1.0,<2
pytest>=8.3,<9
```

`trader/.env.example`:

```
KIS_APP_KEY=
KIS_APP_SECRET=
KIS_BASE_URL=https://openapi.koreainvestment.com:9443
KIS_RPS=15
DATABASE_URL=postgresql://trader:비밀번호@localhost:5432/trader
TEST_DATABASE_URL=postgresql://trader:비밀번호@localhost:5432/trader_test
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

`trader/.gitignore`:

```
.env
.token.json
logs/
.venv/
__pycache__/
.pytest_cache/
```

- [ ] **Step 3: 스키마 작성**

`trader/schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS minute_bars (
    symbol  text        NOT NULL,
    ts      timestamptz NOT NULL,
    open    numeric     NOT NULL,
    high    numeric     NOT NULL,
    low     numeric     NOT NULL,
    close   numeric     NOT NULL,
    volume  bigint      NOT NULL,
    PRIMARY KEY (symbol, ts)
);

CREATE TABLE IF NOT EXISTS collect_runs (
    symbol        text        NOT NULL,
    trade_date    date        NOT NULL,
    bar_count     int         NOT NULL,
    status        text        NOT NULL CHECK (status IN ('ok', 'empty', 'error')),
    error         text,
    collected_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, trade_date)
);
```

- [ ] **Step 4: 가상환경 생성·의존성 설치**

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
```

Expected: `Successfully installed ... psycopg ... pytest ... python-dotenv ... requests ...`

- [ ] **Step 5: DB와 사용자 생성 (사용자 작업)**

postgres 관리자 비밀번호가 필요하므로 **사용자에게 요청**한다. 비밀번호는 사용자가 정한다.

```powershell
D:\PIE\PostgreSQL_15\bin\psql.exe -h localhost -p 5432 -U postgres -c "CREATE USER trader WITH PASSWORD '비밀번호'" -c "CREATE DATABASE trader OWNER trader" -c "CREATE DATABASE trader_test OWNER trader"
```

그다음 사용자가 `.env.example`을 `.env`로 복사하고 `DATABASE_URL`, `TEST_DATABASE_URL`의 비밀번호를 채운다. KIS·텔레그램 값은 비워둔다.

- [ ] **Step 6: 실패하는 테스트 작성**

`trader/tests/test_store.py`:

```python
def test_schema_creates_tables(conn):
    """스키마 적용 후 두 테이블이 존재하는지 확인한다."""
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    ).fetchall()
    assert {"minute_bars", "collect_runs"} <= {r[0] for r in rows}
```

- [ ] **Step 7: 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests\test_store.py -v`
Expected: ERROR `fixture 'conn' not found`

- [ ] **Step 8: 픽스처 구현**

`trader/tests/conftest.py`:

```python
import os
from pathlib import Path

import psycopg
import pytest
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


@pytest.fixture
def conn():
    """테스트 DB에 스키마를 적용하고 테이블을 비운 연결을 제공한다."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.fail("trader/.env에 TEST_DATABASE_URL을 설정하세요")
    with psycopg.connect(url, autocommit=True) as c:
        c.execute((ROOT / "schema.sql").read_text(encoding="utf-8"))
        c.execute("TRUNCATE minute_bars, collect_runs")
        yield c
```

- [ ] **Step 9: 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests\test_store.py -v`
Expected: `1 passed`

- [ ] **Step 10: 커밋**

```powershell
git add trader/requirements.txt trader/.env.example trader/.gitignore trader/schema.sql trader/tests/conftest.py trader/tests/test_store.py
git commit -m "수집기 골격과 DB 스키마 추가"
```

---

### Task 2: KIS 클라이언트 — 토큰 캐시, 호출 간격, 재시도

**Files:**
- Create: `trader/kis.py`
- Test: `trader/tests/test_kis.py`

**Interfaces:**
- Consumes: 없음
- Produces (`kis.py`):
  - `KST: timezone` — UTC+9
  - `class KisError(Exception)` — 속성 `code: str` (KIS `msg_cd`/`error_code`, 또는 `"NETWORK"`, `"HTTP<status>"`, `"PAGINATION"`)
  - `class KisClient(app_key: str, app_secret: str, base_url: str, rps: float, token_path: Path, send=requests.request, sleep=time.sleep, clock=time.monotonic, now=lambda: datetime.now(KST))`
    - `get_token() -> str`
    - `_get(path: str, tr_id: str, params: dict) -> dict` — `rt_cd == "0"`인 응답 본문
  - `send`는 `send(method, url, headers=..., params=..., json=..., timeout=10)` 형태로 호출되며 `.status_code`와 `.json()`을 가진 객체를 반환한다

- [ ] **Step 1: 실패하는 테스트 작성**

`trader/tests/test_kis.py`:

```python
import itertools
import json
from datetime import datetime, timedelta

import pytest
import requests

from kis import KST, KisClient, KisError

NOW = datetime(2026, 9, 14, 16, 0, tzinfo=KST)


class FakeResponse:
    """requests.Response 대역: status_code와 json()만 흉내 낸다."""

    def __init__(self, body, status=200):
        self.status_code = status
        self._body = body

    def json(self):
        """본문이 None이면 JSON이 아닌 응답처럼 ValueError를 낸다."""
        if self._body is None:
            raise ValueError("not json")
        return self._body


class FakeKis:
    """KIS 서버 대역. 토큰 요청과 GET 요청에 준비된 응답을 순서대로 돌려준다."""

    def __init__(self, get_responses=(), token_responses=None, on_get=None):
        self.get_responses = list(get_responses)
        self.token_responses = list(
            token_responses if token_responses is not None else [token_ok("tok1")]
        )
        self.on_get = on_get
        self.calls = []

    def __call__(self, method, url, **kwargs):
        """요청을 기록하고 준비된 응답(또는 예외)을 반환한다."""
        self.calls.append((method, url, kwargs))
        if url.endswith("/oauth2/tokenP"):
            item = self.token_responses.pop(0)
        elif self.on_get:
            item = self.on_get(kwargs["params"])
        else:
            item = self.get_responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def gets(self):
        """GET 요청 기록만 반환한다."""
        return [c for c in self.calls if c[0] == "GET"]


def token_ok(token):
    """정상 토큰 발급 응답을 만든다."""
    return FakeResponse({"access_token": token, "token_type": "Bearer", "expires_in": 86400})


def ok(rows=()):
    """정상 시세 응답을 만든다."""
    return FakeResponse({"rt_cd": "0", "msg_cd": "MCA00000", "msg1": "정상처리 되었습니다.",
                         "output1": {}, "output2": list(rows)})


def err(code):
    """KIS 오류 응답을 만든다."""
    return FakeResponse({"rt_cd": "1", "msg_cd": code, "msg1": "오류"}, status=500)


def make_client(tmp_path, fake, sleeps=None, clock=None):
    """테스트용 KisClient를 만든다. sleep 인자는 sleeps에 기록되고 기본 clock은 매번 10초씩 흐른다."""
    return KisClient(
        "key", "secret", "https://kis.test", 1000, tmp_path / ".token.json",
        send=fake,
        sleep=(sleeps if sleeps is not None else []).append,
        clock=clock or itertools.count(0.0, 10.0).__next__,
        now=lambda: NOW,
    )


def get(client):
    """임의 경로로 _get을 호출한다."""
    return client._get("/uapi/test", "TR0001", {"A": "1"})


def test_token_is_cached_to_file(tmp_path):
    """발급한 토큰을 파일에 저장하고 다른 클라이언트가 재사용한다."""
    assert make_client(tmp_path, FakeKis()).get_token() == "tok1"
    reuse = FakeKis(token_responses=[])
    assert make_client(tmp_path, reuse).get_token() == "tok1"
    assert reuse.calls == []


def test_expired_cached_token_is_reissued(tmp_path):
    """만료가 5분 이내로 남은 캐시 토큰은 쓰지 않고 새로 발급한다."""
    (tmp_path / ".token.json").write_text(json.dumps(
        {"access_token": "old", "expires_at": (NOW + timedelta(minutes=4)).isoformat()}
    ), encoding="utf-8")
    fake = FakeKis(token_responses=[token_ok("new")])
    assert make_client(tmp_path, fake).get_token() == "new"


def test_token_issue_limit_waits_60s(tmp_path):
    """토큰 발급 1분 제한(EGW00133)이면 60초 기다렸다가 한 번 더 발급한다."""
    limited = FakeResponse({"error_code": "EGW00133", "error_description": "1분당 1회"}, status=403)
    sleeps = []
    client = make_client(tmp_path, FakeKis(token_responses=[limited, token_ok("tok1")]), sleeps)
    assert client.get_token() == "tok1"
    assert sleeps == [60]


def test_get_returns_body_with_auth_headers(tmp_path):
    """정상 응답 본문을 반환하고 인증 헤더와 TR ID를 보낸다."""
    fake = FakeKis(get_responses=[ok()])
    assert get(make_client(tmp_path, fake))["rt_cd"] == "0"
    _, url, kwargs = fake.gets()[0]
    assert url == "https://kis.test/uapi/test"
    assert kwargs["params"] == {"A": "1"}
    assert kwargs["headers"]["authorization"] == "Bearer tok1"
    assert kwargs["headers"]["tr_id"] == "TR0001"
    assert kwargs["timeout"] == 10


def test_get_retries_rate_limit(tmp_path):
    """호출 한도 초과(EGW00201)면 1초 쉬고 재시도한다."""
    sleeps = []
    client = make_client(tmp_path, FakeKis(get_responses=[err("EGW00201"), ok()]), sleeps)
    assert get(client)["rt_cd"] == "0"
    assert sleeps == [1]


def test_get_raises_after_three_rate_limit_retries(tmp_path):
    """호출 한도 초과가 재시도 3회 후에도 계속되면 예외를 낸다."""
    sleeps = []
    client = make_client(tmp_path, FakeKis(get_responses=[err("EGW00201")] * 4), sleeps)
    with pytest.raises(KisError) as e:
        get(client)
    assert e.value.code == "EGW00201"
    assert sleeps == [1, 1, 1]


def test_get_refreshes_expired_token_once(tmp_path):
    """토큰 만료(EGW00123)면 토큰을 재발급해 같은 호출을 다시 한다."""
    fake = FakeKis(get_responses=[err("EGW00123"), ok()],
                   token_responses=[token_ok("tok1"), token_ok("tok2")])
    assert get(make_client(tmp_path, fake))["rt_cd"] == "0"
    assert fake.gets()[1][2]["headers"]["authorization"] == "Bearer tok2"


def test_get_retries_network_errors_then_raises(tmp_path):
    """네트워크 오류는 1·2·4초 간격으로 3회 재시도한 뒤 NETWORK 예외를 낸다."""
    sleeps = []
    fake = FakeKis(get_responses=[requests.ConnectionError("down")] * 4)
    with pytest.raises(KisError) as e:
        get(make_client(tmp_path, fake, sleeps))
    assert e.value.code == "NETWORK"
    assert sleeps == [1, 2, 4]


def test_get_retries_5xx_without_kis_code(tmp_path):
    """KIS 오류 코드 없는 5xx는 네트워크 오류처럼 재시도한다."""
    sleeps = []
    client = make_client(tmp_path, FakeKis(get_responses=[FakeResponse(None, 502), ok()]), sleeps)
    assert get(client)["rt_cd"] == "0"
    assert sleeps == [1]


def test_get_raises_unknown_error_without_secrets(tmp_path):
    """알 수 없는 오류 코드는 바로 예외를 내고, 메시지에 앱키·시크릿·토큰이 없다."""
    fake = FakeKis(get_responses=[err("EGW99999")])
    with pytest.raises(KisError) as e:
        get(make_client(tmp_path, fake))
    assert e.value.code == "EGW99999"
    assert not {"key", "secret", "tok1"} & set(str(e.value).split())


def test_throttle_waits_between_calls(tmp_path):
    """직전 호출로부터 1/rps초가 지나지 않았으면 남은 시간만큼 기다린다."""
    sleeps = []
    client = make_client(tmp_path, FakeKis(get_responses=[ok()]), sleeps, clock=lambda: 0.0)
    client.rps = 10
    get(client)
    assert sleeps == [pytest.approx(0.1)]
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests\test_kis.py -v`
Expected: ERROR `ModuleNotFoundError: No module named 'kis'`

- [ ] **Step 3: 구현**

`trader/kis.py`:

```python
"""한국투자증권 Open API 클라이언트: 토큰 캐시, 호출 간격 제한, 재시도."""
import json
import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

KST = timezone(timedelta(hours=9))
NETWORK_DELAYS = (1, 2, 4)
RATE_LIMIT_RETRIES = 3


class KisError(Exception):
    """KIS 호출 실패. code에 KIS 오류 코드나 NETWORK/HTTP 구분을 담는다."""

    def __init__(self, code, message=""):
        super().__init__(f"{code} {message}".strip())
        self.code = code


def _json(resp):
    """응답 본문을 dict로 파싱하고, JSON 객체가 아니면 빈 dict를 반환한다."""
    try:
        body = resp.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


class KisClient:
    """KIS REST 호출 담당. send/sleep/clock/now는 테스트에서 대역으로 바꾼다."""

    def __init__(self, app_key, app_secret, base_url, rps, token_path: Path,
                 send=requests.request, sleep=_time.sleep, clock=_time.monotonic,
                 now=lambda: datetime.now(KST)):
        """접속 정보와 대체 가능한 부수효과 함수를 받는다."""
        self.app_key = app_key
        self.app_secret = app_secret
        self.base_url = base_url
        self.rps = rps
        self.token_path = token_path
        self._send = send
        self._sleep = sleep
        self._clock = clock
        self._now = now
        self._token = None
        self._last_call = None

    def _throttle(self):
        """직전 호출로부터 1/rps초가 지나도록 대기한다."""
        if self._last_call is not None:
            wait = self._last_call + 1 / self.rps - self._clock()
            if wait > 0:
                self._sleep(wait)
        self._last_call = self._clock()

    def get_token(self):
        """캐시 토큰이 5분 이상 유효하면 재사용하고, 아니면 발급해 파일에 저장한다."""
        if self._token:
            return self._token
        try:
            cached = json.loads(self.token_path.read_text(encoding="utf-8"))
            if datetime.fromisoformat(cached["expires_at"]) > self._now() + timedelta(minutes=5):
                self._token = cached["access_token"]
                return self._token
        except (OSError, ValueError, KeyError):
            pass

        for attempt in range(2):
            self._throttle()
            resp = self._send(
                "POST", self.base_url + "/oauth2/tokenP",
                headers={"content-type": "application/json"},
                json={"grant_type": "client_credentials",
                      "appkey": self.app_key, "appsecret": self.app_secret},
                timeout=10,
            )
            body = _json(resp)
            if body.get("access_token"):
                expires_at = self._now() + timedelta(seconds=int(body.get("expires_in", 86400)))
                self.token_path.write_text(json.dumps(
                    {"access_token": body["access_token"], "expires_at": expires_at.isoformat()}
                ), encoding="utf-8")
                self._token = body["access_token"]
                return self._token
            if body.get("error_code") == "EGW00133" and attempt == 0:
                self._sleep(60)
                continue
            raise KisError(body.get("error_code") or f"HTTP{resp.status_code}",
                           body.get("error_description", ""))

    def _drop_token(self):
        """메모리와 파일의 토큰 캐시를 지운다."""
        self._token = None
        self.token_path.unlink(missing_ok=True)

    def _headers(self, tr_id):
        """시세 조회 공통 헤더를 만든다."""
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.get_token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    def _get(self, path, tr_id, params):
        """GET 호출. 토큰 만료·호출 한도·네트워크 오류를 재시도하고 정상 응답 본문을 반환한다."""
        network_tries = rate_tries = 0
        token_refreshed = False
        while True:
            headers = self._headers(tr_id)
            self._throttle()
            try:
                resp = self._send("GET", self.base_url + path, headers=headers,
                                  params=params, timeout=10)
            except requests.RequestException as e:
                if network_tries < len(NETWORK_DELAYS):
                    self._sleep(NETWORK_DELAYS[network_tries])
                    network_tries += 1
                    continue
                raise KisError("NETWORK", type(e).__name__) from None

            body = _json(resp)
            if body.get("rt_cd") == "0":
                return body
            code = body.get("msg_cd")
            if code == "EGW00123" and not token_refreshed:
                token_refreshed = True
                self._drop_token()
                continue
            if code == "EGW00201" and rate_tries < RATE_LIMIT_RETRIES:
                rate_tries += 1
                self._sleep(1)
                continue
            if code is None and resp.status_code >= 500 and network_tries < len(NETWORK_DELAYS):
                self._sleep(NETWORK_DELAYS[network_tries])
                network_tries += 1
                continue
            raise KisError(code or f"HTTP{resp.status_code}", body.get("msg1", ""))
```

`raise ... from None`과 `type(e).__name__`만 남기는 이유: requests 예외 문자열에는 요청 URL·헤더 정보가 섞일 수 있어 메시지에 넣지 않는다.

- [ ] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests\test_kis.py -v`
Expected: `11 passed`

- [ ] **Step 5: 커밋**

```powershell
git add trader/kis.py trader/tests/test_kis.py
git commit -m "KIS 클라이언트 토큰 캐시와 재시도 추가"
```

---

### Task 3: KIS 당일 1분봉 페이지 순회

**Files:**
- Modify: `trader/kis.py` (import, `Bar`, 상수, `_to_bar`, `KisClient.fetch_minute_bars` 추가)
- Create: `trader/tests/fixtures/minute_page.json`
- Test: `trader/tests/test_kis.py` (테스트 추가)

**Interfaces:**
- Consumes: Task 2의 `KisClient._get(path, tr_id, params) -> dict`, `KisError`, `KST`, 테스트 헬퍼 `FakeKis(on_get=...)`, `FakeResponse`, `ok(rows)`, `make_client`
- Produces (`kis.py`):
  - `@dataclass(frozen=True) class Bar: ts: datetime(KST), open: Decimal, high: Decimal, low: Decimal, close: Decimal, volume: int`
  - `KisClient.fetch_minute_bars(symbol: str, today: date) -> list[Bar]` — 시각 오름차순, 오늘 봉만

- [ ] **Step 1: 샘플 응답 파일 작성**

`trader/tests/fixtures/minute_page.json` (KIS 문서의 응답 형태, 최신순):

```json
{
  "rt_cd": "0",
  "msg_cd": "MCA00000",
  "msg1": "정상처리 되었습니다.",
  "output1": {
    "prdy_vrss": "100",
    "prdy_vrss_sign": "2",
    "prdy_ctrt": "0.14",
    "stck_prdy_clpr": "70000",
    "acml_vol": "1523400",
    "acml_tr_pbmn": "106800000000",
    "hts_kor_isnm": "삼성전자",
    "stck_prpr": "70100"
  },
  "output2": [
    {"stck_bsop_date": "20260914", "stck_cntg_hour": "091000", "stck_prpr": "70100", "stck_oprc": "70000", "stck_hgpr": "70200", "stck_lwpr": "69900", "cntg_vol": "15234", "acml_tr_pbmn": "1068000000"},
    {"stck_bsop_date": "20260914", "stck_cntg_hour": "090900", "stck_prpr": "70000", "stck_oprc": "69900", "stck_hgpr": "70000", "stck_lwpr": "69800", "cntg_vol": "12001", "acml_tr_pbmn": "840000000"},
    {"stck_bsop_date": "20260914", "stck_cntg_hour": "090800", "stck_prpr": "69900", "stck_oprc": "69800", "stck_hgpr": "69900", "stck_lwpr": "69700", "cntg_vol": "9870", "acml_tr_pbmn": "690000000"}
  ]
}
```

- [ ] **Step 2: 실패하는 테스트 추가**

`trader/tests/test_kis.py` 맨 위 import를 다음으로 바꾼다:

```python
import itertools
import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import requests

from kis import KST, Bar, KisClient, KisError
```

파일 끝에 추가:

```python
TODAY = date(2026, 9, 14)
FIXTURES = Path(__file__).parent / "fixtures"


def row(hhmmss, ymd="20260914", price="70000"):
    """KIS output2 한 행을 만든다."""
    return {"stck_bsop_date": ymd, "stck_cntg_hour": hhmmss, "stck_prpr": price,
            "stck_oprc": price, "stck_hgpr": price, "stck_lwpr": price,
            "cntg_vol": "100", "acml_tr_pbmn": "0"}


def day_minutes():
    """정상 거래일 1분봉 시각(09:00~15:20 매분, 15:30)을 HHMMSS 목록으로 만든다."""
    out, t = [], datetime(2026, 9, 14, 9, 0)
    while t.time() <= time(15, 20):
        out.append(t.strftime("%H%M%S"))
        t += timedelta(minutes=1)
    return out + ["153000"]


def market(minutes):
    """기준 시각 이하의 봉을 최신순으로 최대 30개 돌려주는 on_get 핸들러를 만든다."""
    def on_get(params):
        hour = params["FID_INPUT_HOUR_1"]
        picked = sorted((m for m in minutes if m <= hour), reverse=True)[:30]
        return ok([row(m) for m in picked])
    return on_get


def test_fetch_parses_kis_fields(tmp_path):
    """KIS 응답 필드를 Bar로 변환한다."""
    body = json.loads((FIXTURES / "minute_page.json").read_text(encoding="utf-8"))
    fake = FakeKis(on_get=lambda params: FakeResponse(body))
    bars = make_client(tmp_path, fake).fetch_minute_bars("005930", TODAY)
    assert [b.ts.time() for b in bars] == [time(9, 8), time(9, 9), time(9, 10)]
    assert bars[-1] == Bar(datetime(2026, 9, 14, 9, 10, tzinfo=KST), Decimal("70000"),
                           Decimal("70200"), Decimal("69900"), Decimal("70100"), 15234)


def test_fetch_sends_minute_chart_request(tmp_path):
    """당일분봉 경로·TR ID·파라미터로 15:30부터 요청한다."""
    fake = FakeKis(on_get=lambda params: ok())
    make_client(tmp_path, fake).fetch_minute_bars("005930", TODAY)
    _, url, kwargs = fake.gets()[0]
    assert url.endswith("/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice")
    assert kwargs["headers"]["tr_id"] == "FHKST03010200"
    assert kwargs["params"] == {"FID_ETC_CLS_CODE": "", "FID_COND_MRKT_DIV_CODE": "J",
                                "FID_INPUT_ISCD": "005930", "FID_INPUT_HOUR_1": "153000",
                                "FID_PW_DATA_INCU_YN": "Y"}


def test_fetch_walks_back_to_market_open(tmp_path):
    """15:30부터 거꾸로 받아 09:00에서 멈추고, 겹친 봉 없이 오름차순으로 반환한다."""
    minutes = day_minutes()
    fake = FakeKis(on_get=market(minutes))
    bars = make_client(tmp_path, fake).fetch_minute_bars("005930", TODAY)
    assert len(bars) == len(minutes)
    assert bars[0].ts == datetime(2026, 9, 14, 9, 0, tzinfo=KST)
    assert bars[-1].ts == datetime(2026, 9, 14, 15, 30, tzinfo=KST)
    assert all(a.ts < b.ts for a, b in zip(bars, bars[1:]))
    assert len(fake.gets()) <= 20


def test_fetch_stops_when_time_does_not_move_back(tmp_path):
    """다음 페이지의 가장 이른 시각이 기준 시각보다 이르지 않으면 멈춘다."""
    same_page = ok([row(m) for m in sorted(day_minutes()[360:390], reverse=True)])
    fake = FakeKis(on_get=lambda params: same_page)
    bars = make_client(tmp_path, fake).fetch_minute_bars("005930", TODAY)
    assert len(fake.gets()) == 2
    assert len(bars) == len(same_page.json()["output2"])


def test_fetch_raises_after_20_pages(tmp_path):
    """20페이지 안에 09:00에 닿지 못하면 PAGINATION 예외를 낸다."""
    def one_minute_back(params):
        t = datetime.strptime(params["FID_INPUT_HOUR_1"], "%H%M%S") - timedelta(minutes=1)
        return ok([row(t.strftime("%H%M%S"))])
    fake = FakeKis(on_get=one_minute_back)
    with pytest.raises(KisError) as e:
        make_client(tmp_path, fake).fetch_minute_bars("005930", TODAY)
    assert e.value.code == "PAGINATION"
    assert len(fake.gets()) == 20


def test_fetch_ignores_other_dates(tmp_path):
    """오늘이 아닌 날짜의 봉만 오면 빈 목록을 반환하고 더 요청하지 않는다."""
    fake = FakeKis(on_get=lambda params: ok([row("153000", ymd="20260911")]))
    assert make_client(tmp_path, fake).fetch_minute_bars("005930", TODAY) == []
    assert len(fake.gets()) == 1
```

- [ ] **Step 3: 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests\test_kis.py -v`
Expected: ERROR `ImportError: cannot import name 'Bar' from 'kis'`

- [ ] **Step 4: 구현**

`trader/kis.py` import 블록을 다음으로 바꾼다:

```python
import json
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import requests
```

`RATE_LIMIT_RETRIES = 3` 아래에 추가:

```python
MINUTE_CHART_PATH = "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice"
MINUTE_CHART_TR_ID = "FHKST03010200"
MARKET_CLOSE = "153000"
MARKET_OPEN = "090000"
MAX_PAGES = 20


@dataclass(frozen=True)
class Bar:
    """1분봉 한 개. ts는 KST 봉 시각."""
    ts: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


def _to_bar(row, today):
    """KIS output2 한 행을 Bar로 변환한다."""
    h = row["stck_cntg_hour"]
    ts = datetime.combine(today, time(int(h[:2]), int(h[2:4]), int(h[4:6])), KST)
    return Bar(ts, Decimal(row["stck_oprc"]), Decimal(row["stck_hgpr"]),
               Decimal(row["stck_lwpr"]), Decimal(row["stck_prpr"]), int(row["cntg_vol"]))
```

`KisClient` 클래스 끝(`_get` 아래)에 추가:

```python
    def fetch_minute_bars(self, symbol, today: date):
        """당일 1분봉을 15:30부터 거꾸로 30개씩 받아 시각 오름차순으로 반환한다."""
        ymd = today.strftime("%Y%m%d")
        bars = {}
        hour = MARKET_CLOSE
        for _ in range(MAX_PAGES):
            body = self._get(MINUTE_CHART_PATH, MINUTE_CHART_TR_ID, {
                "FID_ETC_CLS_CODE": "",
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": symbol,
                "FID_INPUT_HOUR_1": hour,
                "FID_PW_DATA_INCU_YN": "Y",
            })
            rows = [r for r in body.get("output2") or []
                    if r.get("stck_bsop_date") == ymd and r.get("stck_cntg_hour")]
            if not rows:
                break
            for r in rows:
                bar = _to_bar(r, today)
                bars[bar.ts] = bar
            earliest = min(r["stck_cntg_hour"] for r in rows)
            if earliest <= MARKET_OPEN or earliest >= hour:
                break
            hour = earliest
        else:
            raise KisError("PAGINATION", f"{symbol} {MAX_PAGES}페이지 초과")
        return sorted(bars.values(), key=lambda b: b.ts)
```

- [ ] **Step 5: 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests\test_kis.py -v`
Expected: `17 passed`

- [ ] **Step 6: 커밋**

```powershell
git add trader/kis.py trader/tests/test_kis.py trader/tests/fixtures/minute_page.json
git commit -m "KIS 당일 1분봉 페이지 순회 추가"
```

---

### Task 4: DB 저장소

**Files:**
- Create: `trader/store.py`
- Test: `trader/tests/test_store.py` (테스트 추가)

**Interfaces:**
- Consumes: Task 1 `conn` 픽스처와 스키마, Task 3 `kis.Bar`, `kis.KST`
- Produces (`store.py`, 모두 첫 인자는 autocommit `psycopg.Connection`):
  - `save_bars(conn, symbol: str, bars: list[Bar]) -> None`
  - `record_run(conn, symbol: str, trade_date: date, bar_count: int, status: str, error: str | None = None) -> None` — status는 `'ok' | 'empty' | 'error'`
  - `done_symbols(conn, trade_date: date) -> set[str]` — status가 ok 또는 empty
  - `failed_runs(conn, trade_date: date) -> list[tuple[str, str]]` — `(symbol, error)`, symbol 오름차순

- [ ] **Step 1: 실패하는 테스트 추가**

`trader/tests/test_store.py` 전체를 다음으로 바꾼다:

```python
from datetime import date, datetime
from decimal import Decimal

import store
from kis import KST, Bar

TODAY = date(2026, 9, 14)


def bar(hour, minute, close="70000"):
    """테스트용 1분봉을 만든다."""
    p = Decimal(close)
    return Bar(datetime(2026, 9, 14, hour, minute, tzinfo=KST), p, p, p, p, 100)


def test_schema_creates_tables(conn):
    """스키마 적용 후 두 테이블이 존재하는지 확인한다."""
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    ).fetchall()
    assert {"minute_bars", "collect_runs"} <= {r[0] for r in rows}


def test_save_bars_round_trip(conn):
    """저장한 봉을 같은 값으로 읽을 수 있다."""
    store.save_bars(conn, "005930", [bar(9, 0, "70100")])
    row = conn.execute("SELECT symbol, ts, open, close, volume FROM minute_bars").fetchone()
    assert row == ("005930", datetime(2026, 9, 14, 9, 0, tzinfo=KST),
                   Decimal("70100"), Decimal("70100"), 100)


def test_save_bars_is_idempotent(conn):
    """같은 봉을 두 번 저장해도 행 수가 늘지 않는다."""
    bars = [bar(9, 0), bar(9, 1)]
    store.save_bars(conn, "005930", bars)
    store.save_bars(conn, "005930", bars)
    assert conn.execute("SELECT count(*) FROM minute_bars").fetchone()[0] == 2


def test_record_run_overwrites_same_day(conn):
    """같은 종목·날짜 결과는 최신 기록으로 덮어쓴다."""
    store.record_run(conn, "005930", TODAY, 0, "error", "EGW00201 오류")
    store.record_run(conn, "005930", TODAY, 382, "ok")
    rows = conn.execute("SELECT bar_count, status, error FROM collect_runs").fetchall()
    assert rows == [(382, "ok", None)]


def test_done_symbols_includes_ok_and_empty_only(conn):
    """완료 종목은 ok·empty만이고 error와 다른 날짜는 제외한다."""
    store.record_run(conn, "A", TODAY, 382, "ok")
    store.record_run(conn, "B", TODAY, 0, "empty")
    store.record_run(conn, "C", TODAY, 0, "error", "x")
    store.record_run(conn, "D", date(2026, 9, 11), 382, "ok")
    assert store.done_symbols(conn, TODAY) == {"A", "B"}


def test_failed_runs_lists_errors_sorted(conn):
    """오늘 error 종목을 종목코드 순으로 오류 메시지와 함께 반환한다."""
    store.record_run(conn, "B", TODAY, 0, "error", "b오류")
    store.record_run(conn, "A", TODAY, 0, "error", "a오류")
    store.record_run(conn, "C", TODAY, 382, "ok")
    assert store.failed_runs(conn, TODAY) == [("A", "a오류"), ("B", "b오류")]
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests\test_store.py -v`
Expected: ERROR `ModuleNotFoundError: No module named 'store'`

- [ ] **Step 3: 구현**

`trader/store.py`:

```python
"""minute_bars·collect_runs 테이블 저장과 조회."""


def save_bars(conn, symbol, bars):
    """봉 목록을 한 트랜잭션으로 저장한다. 이미 있는 (symbol, ts)는 건너뛴다."""
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO minute_bars (symbol, ts, open, high, low, close, volume) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (symbol, ts) DO NOTHING",
            [(symbol, b.ts, b.open, b.high, b.low, b.close, b.volume) for b in bars],
        )


def record_run(conn, symbol, trade_date, bar_count, status, error=None):
    """종목·날짜별 수집 결과를 기록하고, 이미 있으면 최신 결과로 덮어쓴다."""
    conn.execute(
        "INSERT INTO collect_runs (symbol, trade_date, bar_count, status, error) "
        "VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (symbol, trade_date) DO UPDATE SET bar_count = EXCLUDED.bar_count, "
        "status = EXCLUDED.status, error = EXCLUDED.error, collected_at = now()",
        (symbol, trade_date, bar_count, status, error),
    )


def done_symbols(conn, trade_date):
    """해당 날짜에 ok 또는 empty로 끝난 종목 집합을 반환한다."""
    rows = conn.execute(
        "SELECT symbol FROM collect_runs WHERE trade_date = %s AND status IN ('ok', 'empty')",
        (trade_date,),
    ).fetchall()
    return {r[0] for r in rows}


def failed_runs(conn, trade_date):
    """해당 날짜 error 종목을 (symbol, error) 목록으로 종목코드 순으로 반환한다."""
    return conn.execute(
        "SELECT symbol, error FROM collect_runs "
        "WHERE trade_date = %s AND status = 'error' ORDER BY symbol",
        (trade_date,),
    ).fetchall()
```

- [ ] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests\test_store.py -v`
Expected: `6 passed`

- [ ] **Step 5: 커밋**

```powershell
git add trader/store.py trader/tests/test_store.py
git commit -m "분봉·수집 결과 저장소 추가"
```

---

### Task 5: 텔레그램 알림과 수집기 진입점

**Files:**
- Create: `trader/notify.py`
- Create: `trader/collector.py`
- Test: `trader/tests/test_collector.py`

**Interfaces:**
- Consumes: Task 2·3 `kis.KisClient`, `kis.KisError`, `kis.KST`, `kis.Bar`; Task 4 `store.save_bars`, `store.record_run`, `store.done_symbols`, `store.failed_runs`; Task 1 `conn` 픽스처
- Produces:
  - `notify.send_telegram(text: str) -> None` — 예외를 올리지 않음
  - `collector.should_run(now: datetime) -> bool`
  - `collector.read_symbols(path: Path) -> list[str]`
  - `collector.collect(conn, client, symbols: list[str], today: date) -> dict[str, int]` — 키 `ok`, `empty`, `error`, `skipped`. `client`는 `fetch_minute_bars(symbol, today)`만 있으면 된다
  - `collector.format_alert(today: date, failed: list[tuple[str, str]]) -> str`
  - `collector.alert_failures(conn, now: datetime, send=notify.send_telegram) -> bool`
  - `collector.main(now: datetime | None = None) -> int` — 0 또는 1

- [ ] **Step 1: 실패하는 테스트 작성**

`trader/tests/test_collector.py`:

```python
import logging
from datetime import date, datetime
from decimal import Decimal

import requests

import collector
import notify
import store
from kis import KST, Bar, KisError

TODAY = date(2026, 9, 14)  # 월요일


def bars(n):
    """09:00부터 n개의 1분봉을 만든다."""
    p = Decimal("70000")
    return [Bar(datetime(2026, 9, 14, 9, m, tzinfo=KST), p, p, p, p, 100) for m in range(n)]


class FakeClient:
    """KisClient 대역. results[symbol]이 예외면 던지고, 아니면 봉 목록을 반환한다."""

    def __init__(self, results):
        self.results = results
        self.fetched = []

    def fetch_minute_bars(self, symbol, today):
        """요청 종목을 기록하고 준비된 결과를 돌려준다."""
        self.fetched.append(symbol)
        result = self.results[symbol]
        if isinstance(result, Exception):
            raise result
        return result


def statuses(conn):
    """collect_runs를 {symbol: (status, bar_count)}로 읽는다."""
    rows = conn.execute("SELECT symbol, status, bar_count FROM collect_runs").fetchall()
    return {s: (st, n) for s, st, n in rows}


def test_should_run_only_weekdays_after_1535():
    """평일 15:35 이후에만 수집하고, 주말·장 마감 직후·자정 이후 새벽은 건너뛴다."""
    assert not collector.should_run(datetime(2026, 9, 12, 16, 0, tzinfo=KST))  # 토요일
    assert not collector.should_run(datetime(2026, 9, 14, 15, 34, tzinfo=KST))
    assert not collector.should_run(datetime(2026, 9, 15, 0, 10, tzinfo=KST))
    assert collector.should_run(datetime(2026, 9, 14, 15, 35, tzinfo=KST))


def test_read_symbols_skips_blank_and_comments(tmp_path):
    """빈 줄과 # 주석을 무시하고 종목코드만 읽는다."""
    path = tmp_path / "symbols.txt"
    path.write_text("# 대형주\n005930\n\n000660  # 하이닉스\n", encoding="utf-8")
    assert collector.read_symbols(path) == ["005930", "000660"]


def test_collect_records_ok_empty_error(conn):
    """성공은 저장·ok, 빈 응답은 empty, 실패는 봉 없이 error로 기록하고 다음 종목을 계속한다."""
    client = FakeClient({"A": KisError("EGW00201", "초과"), "B": bars(3), "C": []})
    counts = collector.collect(conn, client, ["A", "B", "C"], TODAY)
    assert counts == {"ok": 1, "empty": 1, "error": 1, "skipped": 0}
    assert statuses(conn) == {"A": ("error", 0), "B": ("ok", 3), "C": ("empty", 0)}
    assert conn.execute("SELECT symbol, count(*) FROM minute_bars GROUP BY symbol").fetchall() == [("B", 3)]
    assert store.failed_runs(conn, TODAY) == [("A", "EGW00201 초과")]


def test_collect_skips_done_and_retries_errors(conn):
    """ok·empty 종목은 건너뛰고 error 종목은 다시 수집한다."""
    store.record_run(conn, "A", TODAY, 3, "ok")
    store.record_run(conn, "B", TODAY, 0, "empty")
    store.record_run(conn, "C", TODAY, 0, "error", "x")
    client = FakeClient({"C": bars(2)})
    counts = collector.collect(conn, client, ["A", "B", "C"], TODAY)
    assert client.fetched == ["C"]
    assert counts == {"ok": 1, "empty": 0, "error": 0, "skipped": 2}
    assert statuses(conn)["C"] == ("ok", 2)


def test_alert_only_after_23_with_failures(conn):
    """23:00 이후 실행이고 실패 종목이 있을 때만 알림을 보낸다."""
    sent = []
    store.record_run(conn, "A", TODAY, 3, "ok")
    at_23 = datetime(2026, 9, 14, 23, 0, tzinfo=KST)
    assert not collector.alert_failures(conn, at_23, sent.append)

    store.record_run(conn, "B", TODAY, 0, "error", "NETWORK ConnectionError")
    assert not collector.alert_failures(conn, datetime(2026, 9, 14, 22, 59, tzinfo=KST), sent.append)
    assert sent == []

    assert collector.alert_failures(conn, at_23, sent.append)
    assert sent == ["[수집기] 2026-09-14 실패 1종목\nB: NETWORK ConnectionError"]


def test_format_alert_truncates_after_20_lines():
    """실패 종목이 20개를 넘으면 20줄만 쓰고 나머지는 개수로 적는다."""
    failed = [(f"{i:06d}", "오류") for i in range(25)]
    lines = collector.format_alert(TODAY, failed).splitlines()
    assert lines[0] == "[수집기] 2026-09-14 실패 25종목"
    assert len(lines) == 22
    assert lines[-1] == "외 5종목"


def test_send_telegram_swallows_errors_without_leaking_token(monkeypatch, caplog):
    """전송 실패는 예외 없이 로그만 남기고, 로그에 봇 토큰이 없다."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "BOTSECRET")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")

    def fail(url, **kwargs):
        raise requests.ConnectionError(f"failed {url}")

    monkeypatch.setattr(notify.requests, "post", fail)
    with caplog.at_level(logging.ERROR):
        notify.send_telegram("hi")
    assert "텔레그램 전송 실패" in caplog.text
    assert "BOTSECRET" not in caplog.text


def test_send_telegram_posts_message(monkeypatch):
    """설정이 있으면 봇 API로 chat_id와 본문을 보낸다."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "BOT")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    calls = []

    class Resp:
        def raise_for_status(self):
            """성공 응답이라 아무것도 하지 않는다."""

    def post(url, **kwargs):
        calls.append((url, kwargs["json"]))
        return Resp()

    monkeypatch.setattr(notify.requests, "post", post)
    notify.send_telegram("hi")
    assert calls == [("https://api.telegram.org/botBOT/sendMessage", {"chat_id": "42", "text": "hi"})]
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests\test_collector.py -v`
Expected: ERROR `ModuleNotFoundError: No module named 'collector'`

- [ ] **Step 3: 알림 구현**

`trader/notify.py`:

```python
"""텔레그램 알림 전송."""
import logging
import os

import requests

log = logging.getLogger(__name__)


def send_telegram(text):
    """TELEGRAM_BOT_TOKEN·TELEGRAM_CHAT_ID로 메시지 1통을 보낸다. 실패해도 예외를 올리지 않는다."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.warning("텔레그램 설정이 없어 알림을 보내지 않음")
        return
    try:
        resp = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                             json={"chat_id": chat_id, "text": text}, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        # 예외 문자열에 토큰이 든 URL이 포함되므로 종류만 남긴다
        log.error("텔레그램 전송 실패: %s", type(e).__name__)
```

- [ ] **Step 4: 수집기 구현**

`trader/collector.py`:

```python
"""KIS 당일 1분봉 수집기 진입점. 작업 스케줄러가 평일 16:00~23:00 매시 실행한다."""
import logging
import os
import sys
from datetime import datetime, time
from pathlib import Path

import psycopg
from dotenv import load_dotenv

import notify
import store
from kis import KST, KisClient

ROOT = Path(__file__).resolve().parent
START_AFTER = time(15, 35)
FINAL_RUN_FROM = time(23, 0)
REQUIRED_ENV = ("KIS_APP_KEY", "KIS_APP_SECRET", "DATABASE_URL")
MAX_ALERT_LINES = 20

log = logging.getLogger("collector")


def should_run(now):
    """평일 15:35 이후에만 수집한다. 자정 이후 밀린 실행이 장 전 빈 응답을 기록하는 것을 막는다."""
    return now.weekday() < 5 and now.time() >= START_AFTER


def read_symbols(path):
    """종목코드 파일을 읽는다. 빈 줄과 # 주석은 무시한다."""
    codes = []
    for line in path.read_text(encoding="utf-8").splitlines():
        code = line.split("#", 1)[0].strip()
        if code:
            codes.append(code)
    return codes


def collect(conn, client, symbols, today):
    """오늘 완료되지 않은 종목만 수집·기록하고 상태별 개수를 반환한다."""
    done = store.done_symbols(conn, today)
    counts = {"ok": 0, "empty": 0, "error": 0, "skipped": 0}
    for symbol in symbols:
        if symbol in done:
            counts["skipped"] += 1
            continue
        try:
            bars = client.fetch_minute_bars(symbol, today)
            if bars:
                store.save_bars(conn, symbol, bars)
                status = "ok"
            else:
                status = "empty"
            store.record_run(conn, symbol, today, len(bars), status)
            log.info("%s %s %d", symbol, status, len(bars))
        except Exception as e:
            store.record_run(conn, symbol, today, 0, "error", str(e))
            status = "error"
            log.error("%s error %s", symbol, e)
        counts[status] += 1
    return counts


def format_alert(today, failed):
    """실패 종목 알림 문구를 만든다. 최대 20줄, 나머지는 개수만 적는다."""
    lines = [f"[수집기] {today} 실패 {len(failed)}종목"]
    lines += [f"{symbol}: {error}" for symbol, error in failed[:MAX_ALERT_LINES]]
    if len(failed) > MAX_ALERT_LINES:
        lines.append(f"외 {len(failed) - MAX_ALERT_LINES}종목")
    return "\n".join(lines)


def alert_failures(conn, now, send=notify.send_telegram):
    """23:00 이후 실행에서 오늘 실패 종목이 남아 있으면 알림을 보내고 True를 반환한다."""
    if now.time() < FINAL_RUN_FROM:
        return False
    failed = store.failed_runs(conn, now.date())
    if not failed:
        return False
    send(format_alert(now.date(), failed))
    return True


def setup_logging(today):
    """콘솔과 logs/collector-YYYY-MM-DD.log에 로그를 남기도록 설정한다."""
    (ROOT / "logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler(ROOT / "logs" / f"collector-{today}.log", encoding="utf-8")],
    )


def main(now=None):
    """수집 1회 실행. 종목 단위 오류만 있으면 0, 설정·DB·토큰 등 실행 전체 실패면 1을 반환한다."""
    now = now or datetime.now(KST)
    load_dotenv(ROOT / ".env")
    setup_logging(now.date())
    if not should_run(now):
        log.info("수집 시간이 아님: %s", now.isoformat())
        return 0
    try:
        missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
        if missing:
            raise RuntimeError(f".env 누락: {', '.join(missing)}")
        client = KisClient(
            os.environ["KIS_APP_KEY"], os.environ["KIS_APP_SECRET"],
            os.environ.get("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443"),
            float(os.environ.get("KIS_RPS", "15")), ROOT / ".token.json",
        )
        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
            client.get_token()
            counts = collect(conn, client, read_symbols(ROOT / "symbols.txt"), now.date())
            log.info("요약 ok %(ok)d / empty %(empty)d / error %(error)d / skipped %(skipped)d", counts)
            alert_failures(conn, now)
    except Exception as e:
        log.error("실행 실패: %s %s", type(e).__name__, e)
        if now.time() >= FINAL_RUN_FROM:
            notify.send_telegram(f"[수집기] {now.date()} 실행 실패: {type(e).__name__}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests\test_collector.py -v`
Expected: `8 passed`

- [ ] **Step 6: 전체 테스트**

Run: `.\.venv\Scripts\python -m pytest tests -v`
Expected: `31 passed`

- [ ] **Step 7: 커밋**

```powershell
git add trader/notify.py trader/collector.py trader/tests/test_collector.py
git commit -m "수집기 진입점과 텔레그램 알림 추가"
```

---

### Task 6: 종목 파일, README, 실행 확인

**Files:**
- Create: `trader/symbols.txt`
- Create: `trader/README.md`

**Interfaces:**
- Consumes: Task 5 `collector.main`, Task 1 `schema.sql`·`.env.example`
- Produces: 설치·스케줄러 등록·누락 확인·수동 검증 문서

- [ ] **Step 1: 종목 파일 작성**

`trader/symbols.txt`:

```
# 수집 대상 종목코드, 한 줄에 하나 (# 뒤는 주석)
005930  # 삼성전자
000660  # SK하이닉스
```

- [ ] **Step 2: README 작성**

`trader/README.md`:

````markdown
# KIS 1분봉 수집기

평일 장 마감 후 `symbols.txt` 종목의 당일 1분봉을 한국투자증권 Open API에서 받아 PostgreSQL에 저장한다.
설계: `docs/superpowers/specs/2026-09-14-kis-minute-collector-design.md`

## 설치

1. PostgreSQL 15 서비스(`postgresql-x64-15`, 포트 5432)에 DB와 사용자 생성

   ```powershell
   D:\PIE\PostgreSQL_15\bin\psql.exe -h localhost -U postgres -c "CREATE USER trader WITH PASSWORD '비밀번호'" -c "CREATE DATABASE trader OWNER trader" -c "CREATE DATABASE trader_test OWNER trader"
   D:\PIE\PostgreSQL_15\bin\psql.exe -h localhost -U trader -d trader -f schema.sql
   ```

2. 가상환경과 의존성

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python -m pip install -r requirements.txt
   ```

3. `.env.example`을 `.env`로 복사해 값 입력
   - `KIS_APP_KEY`, `KIS_APP_SECRET`: KIS Developers에서 발급한 **실전** 앱키 (시세 조회만 사용)
   - `TELEGRAM_BOT_TOKEN`: BotFather로 만든 봇 토큰
   - `TELEGRAM_CHAT_ID`: 봇에게 메시지를 보낸 뒤 `https://api.telegram.org/bot<토큰>/getUpdates`의 `chat.id`

4. 테스트: `.\.venv\Scripts\python -m pytest tests -v`

## 작업 스케줄러 등록

평일 16:00부터 1시간마다 23:00까지 실행한다. 관리자 PowerShell에서 `trader` 폴더 기준으로 실행:

```powershell
$dir = (Get-Location).Path
$action = New-ScheduledTaskAction -Execute "$dir\.venv\Scripts\python.exe" -Argument "collector.py" -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 16:00
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At 16:00 -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Hours 7)).Repetition
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 50)
Register-ScheduledTask -TaskName "KIS 분봉 수집기" -Action $action -Trigger $trigger -Settings $settings
```

- 평일 장 마감 후 23:00 전까지 PC가 켜져 있어야 한다. 당일분봉은 다음 날 다시 받을 수 없다.
- 로그: `logs/collector-YYYY-MM-DD.log`

## 누락 확인

```sql
SELECT trade_date, symbol, status, bar_count, error
FROM collect_runs
WHERE status <> 'ok' OR bar_count < 300
ORDER BY trade_date DESC, symbol;
```

## 키 발급 후 수동 검증 (1회)

1. `symbols.txt`를 005930, 000660 두 종목으로 두고 평일 15:35 이후 `.\.venv\Scripts\python collector.py` 실행
2. 종목당 약 381개 저장 확인: `SELECT symbol, count(*) FROM minute_bars GROUP BY symbol;`
3. 임의 봉 3개를 HTS/MTS 1분 차트와 시가·고가·저가·종가·거래량 대조
4. 응답 필드명, 오류 코드(`EGW00123`, `EGW00133`, `EGW00201`), 호출 한도, 주식일별분봉조회 API 사용 가능 여부가 설계와 다르면 스펙과 코드 수정
5. 작업 스케줄러 등록 후 하루 동안 두 번째 실행부터 `skipped`만 나오는지 로그 확인
6. 5영업일 연속 `collect_runs`에 전 종목 `ok`(공휴일은 `empty`) 확인
````

- [ ] **Step 3: 키 없이 실행 경로 확인**

KIS 키가 비어 있는 현재 `.env`로 실행 전체 실패 경로를 확인한다.

Run: `.\.venv\Scripts\python -c "from datetime import datetime; from kis import KST; import collector; print(collector.main(datetime(2026, 9, 14, 16, 0, tzinfo=KST)))"`
Expected: 로그 `실행 실패: RuntimeError .env 누락: KIS_APP_KEY, KIS_APP_SECRET`, 마지막 줄 `1`

Run: `.\.venv\Scripts\python -c "from datetime import datetime; from kis import KST; import collector; print(collector.main(datetime(2026, 9, 14, 10, 0, tzinfo=KST)))"`
Expected: 로그 `수집 시간이 아님: 2026-09-14T10:00:00+09:00`, 마지막 줄 `0`

두 실행이 만든 `logs/`는 git에서 제외되는지 `git status --short trader`로 확인한다 (logs가 목록에 없어야 함).

- [ ] **Step 4: 커밋**

```powershell
git add trader/symbols.txt trader/README.md
git commit -m "수집기 종목 파일과 README 추가"
```

- [ ] **Step 5: 남은 작업 보고**

KIS 계좌·키 발급 전까지 README "키 발급 후 수동 검증"과 작업 스케줄러 등록은 사용자 작업으로 남는다고 보고한다.
