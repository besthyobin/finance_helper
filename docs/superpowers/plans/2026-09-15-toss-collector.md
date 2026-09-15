# 토스증권 1분봉 수집기 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 수집기 데이터 소스를 KIS에서 토스증권 Open API로 바꿔, 최근 약 4년치 1분봉을 날짜 단위로 백필·매일 보충하고(`minute_bars.source='toss'`), 백테스트에 정규장/전체 세션 선택을 더한다.

**Architecture:** `kis.py`의 `KST`·`Bar`를 `bars.py`로 옮기고, `toss.py`(`TossClient`: 토큰 캐시·호출 간격·재시도·하루치 페이지 조회)를 새로 만든다. `collector.py`는 종목마다 `collect_runs`에 `ok`/`empty`가 없는 평일을 최신순으로 받아 기록한다. KIS 코드는 삭제하고, `backtest.py`에 `--session regular|all`을 더한다.

**Tech Stack:** Python 3.11, requests, psycopg 3, python-dotenv, pytest, PostgreSQL 15 (신규 의존성 없음)

**Spec:** `docs/superpowers/specs/2026-09-15-toss-collector-design.md`

### 스펙과 다르게 구체화한 점

- `volume`은 `int(Decimal(volume))`로 변환한다. 공식 스키마에서 `volume`이 decimal 형식 문자열이라 `"100.0"`이 와도 깨지지 않게 한다.
- `TossError(code, detail="")`: `str(e)`는 `"{code} {detail}"`. `detail`은 `requests` 예외 종류 이름(`ConnectionError` 등)에만 쓴다. `e.code`는 스펙대로다.
- 조회 중 401은 코드가 `expired-token`·`token-revoked`·`invalid-token`이면 1회 재발급한다. 그 밖의 401 코드(`edge-blocked`, `login-user-not-found` 등)는 곧바로 `TossAuthError`로 올린다. 인증 오류를 날짜 오류로 수천 건 기록하지 않기 위해서다.
- 토큰 발급 중 네트워크 예외는 재시도 없이 `TossError("NETWORK", 종류)`로 올린다. 예외 메시지에 URL이 섞이지 않게 하려는 것이다.
- 200 응답에 `result.candles`가 없으면 `TossError("BAD_RESPONSE")`를 낸다. 그대로 두면 `empty`로 기록되어 다시 받지 않기 때문이다.
- `main`은 설정이 빠지면 빠진 **변수 이름**을 따로 로그에 남긴다(값은 남기지 않음). 실행 실패 로그·알림은 스펙대로 예외 종류만 남긴다.
- `collect`에서 `record_run`으로 기록하는 예외는 `fetch_day`에서 난 것뿐이다. `save_bars`·`record_run` 자체의 DB 오류는 실행 실패로 올린다(오류 처리표의 "DB 접속 실패 → 실행 실패"와 같은 취급).
- `backtest.regular_session(bars)` 함수로 세션 필터를 분리해 단위 테스트한다.

## Global Constraints

- Python 3.11, 가상환경 `trader/.venv` (이미 있음). 의존성은 `requests`, `psycopg[binary]`, `python-dotenv`, `pytest` 4개만. ORM·비동기·설정 클래스·재시도/모킹 라이브러리 금지
- DB: `trader/.env`의 `DATABASE_URL`(운영), `TEST_DATABASE_URL`(테스트). 둘 다 이미 설정되어 있음. 테스트는 `TEST_DATABASE_URL`만 사용
- 시간대: `bars.KST` (UTC+9 고정). `zoneinfo` 사용 금지
- 테스트는 네트워크 호출 금지. 토스 서버는 `send` 대역, 텔레그램은 `monkeypatch`
- 예외 메시지·로그·알림에 client_id, client_secret, 토큰, 요청 URL 원문, DB 접속 문자열을 넣지 않는다
- 함수/메서드마다 무엇을 하는지 한국어 한 줄 docstring
- 커밋 메시지는 한국어로 간결하게. 커밋에는 해당 태스크 파일만 `git add <경로>`/`git rm <경로>`로 추가. 작업 트리에 무관한 FMP 수정 파일(`app/api/stocks/...`, `lib/fmp-finance.ts`)이 있으니 `git add -A`·`git add .` 금지
- 작업 브랜치 `feat/toss-collector`에서 작업 (`main`에서 바로 커밋 금지)
- 모든 명령은 PowerShell, 작업 디렉터리 `D:\dev\antigravity_workspace\finance_helper\trader`
- `notify.py`, `engine.py`, `strategies.py`, `schema.sql`은 수정하지 않는다. `load_yahoo.py`는 import 한 줄만 바꾼다
- 상수: `HISTORY_DAYS = 1461`, `TODAY_FROM = time(20, 10)`, `MAX_CONSECUTIVE_ERRORS = 5`, `MAX_ALERT_LINES = 20`, 페이지 `count=200`, 최대 10페이지, `TOSS_BASE_URL` 기본 `https://openapi.tossinvest.com`, `TOSS_RPS` 기본 `15`
- 시작 시점 테스트: `72 passed`

---

### Task 1: `bars.py`로 `KST`·`Bar` 이동

**Files:**
- Create: `trader/bars.py`
- Modify: `trader/kis.py:1-30` (정의 제거, `bars`에서 import)
- Modify: `trader/store.py:6`, `trader/load_yahoo.py:14`
- Modify: `trader/tests/test_store.py:10`, `trader/tests/test_backtest.py:9`, `trader/tests/test_engine.py:7`, `trader/tests/test_strategies.py:6`, `trader/tests/test_load_yahoo.py:8`

**Interfaces:**
- Consumes: 없음
- Produces: `bars.KST` (`timezone(timedelta(hours=9))`), `bars.Bar(ts: datetime, open: Decimal, high: Decimal, low: Decimal, close: Decimal, volume: int)` (frozen dataclass, `ts`는 KST 봉 시작 시각)

`collector.py`, `tests/test_collector.py`, `tests/test_kis.py`는 이 태스크에서 건드리지 않는다(`kis.py`가 `KST`·`Bar`를 다시 내보내므로 계속 동작하고, Task 4에서 재작성·삭제한다).

- [ ] **Step 1: 브랜치 만들고 기준 테스트 확인**

```powershell
git switch -c feat/toss-collector
.\.venv\Scripts\python -m pytest tests -q
```

Expected: `72 passed`

- [ ] **Step 2: `trader/bars.py` 생성**

```python
"""1분봉 공용 타입: KST 시간대와 Bar."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

KST = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class Bar:
    """1분봉 한 개. ts는 KST 봉 시작 시각."""
    ts: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
```

- [ ] **Step 3: `trader/kis.py` 상단 교체**

`kis.py` 1~30행(모듈 docstring부터 `class Bar` 끝까지)을 아래로 바꾼다. `_to_bar` 이하는 그대로 둔다.

```python
"""한국투자증권 Open API 클라이언트: 토큰 캐시, 호출 간격 제한, 재시도."""
import json
import time as _time
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

import requests

from bars import KST, Bar

NETWORK_DELAYS = (1, 2, 4)
RATE_LIMIT_RETRIES = 3
MINUTE_CHART_PATH = "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice"
MINUTE_CHART_TR_ID = "FHKST03010200"
MARKET_CLOSE = "153000"
MARKET_OPEN = "090000"
MAX_PAGES = 20

```

- [ ] **Step 4: import 경로 변경**

아래 7개 파일의 해당 줄을 바꾼다.

| 파일 | 변경 전 | 변경 후 |
|---|---|---|
| `store.py` | `from kis import KST, Bar` | `from bars import KST, Bar` |
| `load_yahoo.py` | `from kis import KST, Bar` | `from bars import KST, Bar` |
| `tests/test_store.py` | `from kis import KST, Bar` | `from bars import KST, Bar` |
| `tests/test_backtest.py` | `from kis import KST, Bar` | `from bars import KST, Bar` |
| `tests/test_engine.py` | `from kis import KST, Bar` | `from bars import KST, Bar` |
| `tests/test_strategies.py` | `from kis import KST, Bar` | `from bars import KST, Bar` |
| `tests/test_load_yahoo.py` | `from kis import KST, Bar` | `from bars import KST, Bar` |

- [ ] **Step 5: 테스트와 남은 import 확인**

```powershell
.\.venv\Scripts\python -m pytest tests -q
Select-String -Path *.py,tests\*.py -Pattern '^from kis import'
```

Expected: `72 passed`. `Select-String` 결과는 `collector.py`(`from kis import KST, KisClient`), `tests\test_collector.py`, `tests\test_kis.py` 3줄만

- [ ] **Step 6: 커밋**

```powershell
git add bars.py kis.py store.py load_yahoo.py tests/test_store.py tests/test_backtest.py tests/test_engine.py tests/test_strategies.py tests/test_load_yahoo.py
git commit -m "KST·Bar를 bars 모듈로 분리"
```

---

### Task 2: `TossClient` 토큰·GET 호출

**Files:**
- Create: `trader/toss.py`
- Test: `trader/tests/test_toss.py`

**Interfaces:**
- Consumes: `bars.KST`
- Produces:
  - `toss.TossError(code: str, detail: str = "")` — 속성 `code`, `str(e) == f"{code} {detail}".strip()`
  - `toss.TossAuthError(TossError)` — 실행 중단용 인증·권한 오류
  - `toss.TossClient(client_id, client_secret, base_url, rps: float, token_path: Path, send=requests.request, sleep=time.sleep, clock=time.monotonic, now=lambda: datetime.now(KST))`
  - `TossClient.get_token() -> str`
  - `TossClient._get(path: str, params: dict) -> dict` (Task 3의 `fetch_day`가 사용)

- [ ] **Step 1: 실패하는 테스트 작성 — `trader/tests/test_toss.py`**

```python
import itertools
import json
from datetime import datetime, timedelta

import pytest
import requests

from bars import KST
from toss import TossAuthError, TossClient, TossError

NOW = datetime(2026, 9, 15, 20, 30, tzinfo=KST)
SECRETS = ("CID-X", "SECRET-X", "TOK-1", "TOK-2")


class FakeResponse:
    """requests.Response 대역: status_code, headers, json()만 흉내 낸다."""

    def __init__(self, body, status=200, headers=None):
        self.status_code = status
        self.headers = headers or {}
        self._body = body

    def json(self):
        """본문이 None이면 JSON이 아닌 응답처럼 ValueError를 낸다."""
        if self._body is None:
            raise ValueError("not json")
        return self._body


class FakeToss:
    """토스 서버 대역. 토큰 요청과 GET 요청에 준비된 응답(또는 예외)을 순서대로 돌려준다."""

    def __init__(self, get_responses=(), token_responses=None, on_get=None):
        self.get_responses = list(get_responses)
        self.token_responses = list(
            token_responses if token_responses is not None else [token_ok("TOK-1")]
        )
        self.on_get = on_get
        self.calls = []

    def __call__(self, method, url, **kwargs):
        """요청을 기록하고 준비된 응답을 반환하거나 예외를 던진다."""
        self.calls.append((method, url, kwargs))
        if url.endswith("/oauth2/token"):
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

    def token_calls(self):
        """토큰 발급 요청 기록만 반환한다."""
        return [c for c in self.calls if c[0] == "POST"]


def token_ok(token):
    """정상 토큰 발급 응답을 만든다."""
    return FakeResponse({"access_token": token, "token_type": "Bearer", "expires_in": 86399})


def ok(candles=(), next_before=None):
    """정상 캔들 응답을 만든다."""
    return FakeResponse({"result": {"candles": list(candles), "nextBefore": next_before}})


def err(status, code, headers=None):
    """토스 공통 오류 응답을 만든다."""
    return FakeResponse({"error": {"requestId": "r1", "code": code, "message": "오류"}}, status, headers)


def make_client(tmp_path, fake, sleeps=None, clock=None):
    """테스트용 TossClient를 만든다. sleep 인자는 sleeps에 기록되고 기본 clock은 매번 10초씩 흐른다."""
    return TossClient(
        "CID-X", "SECRET-X", "https://toss.test", 1000, tmp_path / ".token.json",
        send=fake,
        sleep=(sleeps if sleeps is not None else []).append,
        clock=clock or itertools.count(0.0, 10.0).__next__,
        now=lambda: NOW,
    )


def get(client):
    """임의 경로로 _get을 호출한다."""
    return client._get("/api/v1/test", {"A": "1"})


def assert_no_secrets(exc):
    """예외 메시지에 client_id·secret·토큰이 없는지 확인한다."""
    assert not any(s in str(exc) for s in SECRETS)


def test_token_is_issued_with_form_and_cached_to_file(tmp_path):
    """form 본문으로 토큰을 발급해 만료 시각과 함께 파일에 저장하고, 다른 클라이언트가 재사용한다."""
    fake = FakeToss()
    assert make_client(tmp_path, fake).get_token() == "TOK-1"
    method, url, kwargs = fake.calls[0]
    assert (method, url) == ("POST", "https://toss.test/oauth2/token")
    assert kwargs["data"] == {"grant_type": "client_credentials",
                              "client_id": "CID-X", "client_secret": "SECRET-X"}
    assert kwargs["timeout"] == 10
    cached = json.loads((tmp_path / ".token.json").read_text(encoding="utf-8"))
    assert cached == {"access_token": "TOK-1",
                      "expires_at": (NOW + timedelta(seconds=86399)).isoformat()}

    reuse = FakeToss(token_responses=[])
    assert make_client(tmp_path, reuse).get_token() == "TOK-1"
    assert reuse.calls == []


def test_token_expiring_within_5_minutes_is_reissued(tmp_path):
    """만료가 5분 이내로 남은 캐시 토큰은 쓰지 않고 새로 발급한다."""
    (tmp_path / ".token.json").write_text(json.dumps(
        {"access_token": "OLD", "expires_at": (NOW + timedelta(minutes=4)).isoformat()}
    ), encoding="utf-8")
    fake = FakeToss(token_responses=[token_ok("TOK-2")])
    assert make_client(tmp_path, fake).get_token() == "TOK-2"


@pytest.mark.parametrize("status, code", [(401, "invalid_client"), (403, "access_denied")])
def test_token_auth_failure_raises_auth_error(tmp_path, status, code):
    """토큰 발급 401·403은 OAuth2 error 값을 code로 TossAuthError를 낸다."""
    fake = FakeToss(token_responses=[FakeResponse({"error": code, "error_description": "x"}, status)])
    with pytest.raises(TossAuthError) as e:
        make_client(tmp_path, fake).get_token()
    assert e.value.code == code
    assert_no_secrets(e.value)


def test_token_rate_limit_waits_retry_after(tmp_path):
    """토큰 발급 429면 Retry-After초 기다렸다가 다시 발급한다."""
    sleeps = []
    fake = FakeToss(token_responses=[err(429, "rate-limit-exceeded", {"Retry-After": "2"}),
                                     token_ok("TOK-1")])
    assert make_client(tmp_path, fake, sleeps).get_token() == "TOK-1"
    assert sleeps == [2]


def test_get_returns_body_with_bearer_header(tmp_path):
    """정상 응답 본문을 반환하고 Bearer 헤더·파라미터·timeout을 보낸다."""
    fake = FakeToss(get_responses=[ok()])
    assert get(make_client(tmp_path, fake)) == {"result": {"candles": [], "nextBefore": None}}
    _, url, kwargs = fake.gets()[0]
    assert url == "https://toss.test/api/v1/test"
    assert kwargs["params"] == {"A": "1"}
    assert kwargs["headers"] == {"Authorization": "Bearer TOK-1"}
    assert kwargs["timeout"] == 10


def test_get_reissues_token_once_on_expired_token(tmp_path):
    """401 expired-token이면 캐시를 지우고 한 번 재발급해 같은 호출을 다시 한다."""
    fake = FakeToss(get_responses=[err(401, "expired-token"), ok()],
                    token_responses=[token_ok("TOK-1"), token_ok("TOK-2")])
    get(make_client(tmp_path, fake))
    assert fake.gets()[1][2]["headers"] == {"Authorization": "Bearer TOK-2"}
    cached = json.loads((tmp_path / ".token.json").read_text(encoding="utf-8"))
    assert cached["access_token"] == "TOK-2"


def test_get_raises_auth_error_when_401_repeats_after_reissue(tmp_path):
    """재발급 후에도 401이면 더 재발급하지 않고 TossAuthError를 낸다."""
    fake = FakeToss(get_responses=[err(401, "expired-token"), err(401, "token-revoked")],
                    token_responses=[token_ok("TOK-1"), token_ok("TOK-2")])
    with pytest.raises(TossAuthError) as e:
        get(make_client(tmp_path, fake))
    assert e.value.code == "token-revoked"
    assert len(fake.token_calls()) == 2


def test_get_forbidden_raises_auth_error(tmp_path):
    """403은 재시도 없이 TossAuthError를 낸다."""
    with pytest.raises(TossAuthError) as e:
        get(make_client(tmp_path, FakeToss(get_responses=[err(403, "forbidden")])))
    assert e.value.code == "forbidden"


def test_get_waits_retry_after_on_rate_limit(tmp_path):
    """429면 Retry-After초(없으면 1초) 기다렸다가 재시도한다."""
    sleeps = []
    fake = FakeToss(get_responses=[err(429, "rate-limit-exceeded"),
                                   err(429, "rate-limit-exceeded", {"Retry-After": "3"}), ok()])
    get(make_client(tmp_path, fake, sleeps))
    assert sleeps == [1, 3]


def test_get_raises_after_three_rate_limit_retries(tmp_path):
    """429가 재시도 3회 후에도 계속되면 TossError를 낸다."""
    sleeps = []
    fake = FakeToss(get_responses=[err(429, "rate-limit-exceeded")] * 4)
    with pytest.raises(TossError) as e:
        get(make_client(tmp_path, fake, sleeps))
    assert e.value.code == "rate-limit-exceeded"
    assert sleeps == [1, 1, 1]


def test_get_retries_network_errors_then_raises(tmp_path):
    """네트워크 오류는 1·2·4초 간격으로 3회 재시도한 뒤 예외 종류만 담은 NETWORK 예외를 낸다."""
    sleeps = []
    fake = FakeToss(get_responses=[requests.ConnectionError("https://toss.test/api/v1/test CID-X")] * 4)
    with pytest.raises(TossError) as e:
        get(make_client(tmp_path, fake, sleeps))
    assert e.value.code == "NETWORK"
    assert str(e.value) == "NETWORK ConnectionError"
    assert sleeps == [1, 2, 4]


def test_get_retries_5xx_without_error_code(tmp_path):
    """error.code 없는 5xx는 네트워크 오류처럼 재시도한다."""
    sleeps = []
    get(make_client(tmp_path, FakeToss(get_responses=[FakeResponse(None, 502), ok()]), sleeps))
    assert sleeps == [1]


def test_get_raises_other_errors_with_code_and_no_secrets(tmp_path):
    """그 밖 4xx는 재시도 없이 응답 error.code로 TossError를 내고, 메시지에 비밀값이 없다."""
    fake = FakeToss(get_responses=[err(404, "not-found")])
    with pytest.raises(TossError) as e:
        get(make_client(tmp_path, fake))
    assert type(e.value) is TossError
    assert e.value.code == "not-found"
    assert len(fake.gets()) == 1
    assert_no_secrets(e.value)


def test_throttle_waits_between_calls(tmp_path):
    """직전 호출로부터 1/rps초가 지나지 않았으면 남은 시간만큼 기다린다."""
    sleeps = []
    client = make_client(tmp_path, FakeToss(get_responses=[ok()]), sleeps, clock=lambda: 0.0)
    client.rps = 10
    get(client)
    assert sleeps == [pytest.approx(0.1)]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests\test_toss.py -v`
Expected: ERROR `ModuleNotFoundError: No module named 'toss'`

- [ ] **Step 3: `trader/toss.py` 구현**

```python
"""토스증권 Open API 클라이언트: 토큰 캐시, 호출 간격 제한, 재시도."""
import json
import time as _time
from datetime import datetime, timedelta
from pathlib import Path

import requests

from bars import KST

NETWORK_DELAYS = (1, 2, 4)
RATE_LIMIT_RETRIES = 3
REISSUE_CODES = {"expired-token", "token-revoked", "invalid-token"}


class TossError(Exception):
    """토스 호출 실패. code에 토스 error.code 또는 NETWORK·HTTP<status>·PAGINATION·BAD_RESPONSE를 담는다."""

    def __init__(self, code, detail=""):
        super().__init__(f"{code} {detail}".strip())
        self.code = code


class TossAuthError(TossError):
    """인증·권한 오류. 날짜 단위로 기록하지 않고 실행 전체를 중단한다."""


def _json(resp):
    """응답 본문을 dict로 파싱하고, JSON 객체가 아니면 빈 dict를 반환한다."""
    try:
        body = resp.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _retry_after(resp):
    """429 응답의 Retry-After 초를 읽는다. 없거나 숫자가 아니면 1초."""
    try:
        return float(resp.headers.get("Retry-After", 1))
    except ValueError:
        return 1


class TossClient:
    """토스 REST 호출 담당. send/sleep/clock/now는 테스트에서 대역으로 바꾼다."""

    def __init__(self, client_id, client_secret, base_url, rps, token_path: Path,
                 send=requests.request, sleep=_time.sleep, clock=_time.monotonic,
                 now=lambda: datetime.now(KST)):
        """접속 정보와 대체 가능한 부수효과 함수를 받는다."""
        self.client_id = client_id
        self.client_secret = client_secret
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
        except (OSError, ValueError, KeyError, TypeError):
            pass

        rate_tries = 0
        while True:
            self._throttle()
            try:
                resp = self._send(
                    "POST", self.base_url + "/oauth2/token",
                    data={"grant_type": "client_credentials",
                          "client_id": self.client_id, "client_secret": self.client_secret},
                    timeout=10,
                )
            except requests.RequestException as e:
                raise TossError("NETWORK", type(e).__name__) from None
            body = _json(resp)
            if resp.status_code == 200 and body.get("access_token"):
                expires_at = self._now() + timedelta(seconds=int(body.get("expires_in", 86399)))
                self.token_path.write_text(json.dumps(
                    {"access_token": body["access_token"], "expires_at": expires_at.isoformat()}
                ), encoding="utf-8")
                self._token = body["access_token"]
                return self._token
            if resp.status_code == 429 and rate_tries < RATE_LIMIT_RETRIES:
                rate_tries += 1
                self._sleep(_retry_after(resp))
                continue
            error = body.get("error")
            code = error if isinstance(error, str) else f"HTTP{resp.status_code}"
            if resp.status_code in (401, 403):
                raise TossAuthError(code)
            raise TossError(code)

    def _drop_token(self):
        """메모리와 파일의 토큰 캐시를 지운다."""
        self._token = None
        self.token_path.unlink(missing_ok=True)

    def _get(self, path, params):
        """GET 호출. 토큰 만료·호출 한도·네트워크 오류를 재시도하고 정상 응답 본문을 반환한다."""
        network_tries = rate_tries = 0
        token_reissued = False
        while True:
            headers = {"Authorization": f"Bearer {self.get_token()}"}
            self._throttle()
            try:
                resp = self._send("GET", self.base_url + path, headers=headers,
                                  params=params, timeout=10)
            except requests.RequestException as e:
                if network_tries < len(NETWORK_DELAYS):
                    self._sleep(NETWORK_DELAYS[network_tries])
                    network_tries += 1
                    continue
                raise TossError("NETWORK", type(e).__name__) from None

            status = resp.status_code
            if status == 200:
                return _json(resp)
            error = _json(resp).get("error")
            code = error.get("code") if isinstance(error, dict) else None
            if status == 401:
                if code in REISSUE_CODES and not token_reissued:
                    token_reissued = True
                    self._drop_token()
                    continue
                raise TossAuthError(code or "HTTP401")
            if status == 403:
                raise TossAuthError(code or "HTTP403")
            if status == 429:
                if rate_tries < RATE_LIMIT_RETRIES:
                    rate_tries += 1
                    self._sleep(_retry_after(resp))
                    continue
                raise TossError(code or "HTTP429")
            if code is None and status >= 500 and network_tries < len(NETWORK_DELAYS):
                self._sleep(NETWORK_DELAYS[network_tries])
                network_tries += 1
                continue
            raise TossError(code or f"HTTP{status}")
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests\test_toss.py -v`
Expected: `15 passed`

Run: `.\.venv\Scripts\python -m pytest tests -q`
Expected: `87 passed`

- [ ] **Step 5: 커밋**

```powershell
git add toss.py tests/test_toss.py
git commit -m "토스 API 토큰 발급과 호출 재시도 추가"
```

---

### Task 3: `TossClient.fetch_day` 하루치 1분봉 조회

**Files:**
- Modify: `trader/toss.py` (import 블록, 상수, `fetch_day` 메서드 추가)
- Create: `trader/tests/fixtures/toss_candles.json`
- Test: `trader/tests/test_toss.py` (import 블록 교체, 테스트 7개 추가)

**Interfaces:**
- Consumes: `TossClient._get(path, params) -> dict`, `TossError`, `bars.Bar`, `bars.KST`
- Produces: `TossClient.fetch_day(symbol: str, day: date) -> list[Bar]` — `ts`는 받은 `timestamp − 1분`(KST, 봉 시작 시각), 시각 오름차순, 봉이 없으면 `[]`. 실패 시 `TossError`(`PAGINATION`, `BAD_RESPONSE` 포함) 또는 `TossAuthError`

응답 형식(공식 OpenAPI v1.2.17): `{"result": {"candles": [{"timestamp": "2026-03-25T09:32:00+09:00", "openPrice": "72000", "highPrice": "72100", "lowPrice": "71950", "closePrice": "72050", "volume": "15200", "currency": "KRW"}, ...], "nextBefore": "2026-03-25T09:31:00+09:00" | null}}`. 캔들은 최신순이고, `1m`의 `timestamp`는 봉이 **끝나는** 시각이다. 가격·거래량은 문자열로 온다.

- [ ] **Step 1: 픽스처 생성 — `trader/tests/fixtures/toss_candles.json`**

```json
{
  "result": {
    "candles": [
      {"timestamp": "2026-09-14T09:03:00+09:00", "openPrice": "70100", "highPrice": "70300", "lowPrice": "70000", "closePrice": "70200", "volume": "15234", "currency": "KRW"},
      {"timestamp": "2026-09-14T09:02:00+09:00", "openPrice": "70000", "highPrice": "70100", "lowPrice": "69900", "closePrice": "70100", "volume": "12001", "currency": "KRW"},
      {"timestamp": "2026-09-14T09:01:00+09:00", "openPrice": "69900", "highPrice": "70000", "lowPrice": "69800", "closePrice": "70000", "volume": "9870", "currency": "KRW"}
    ],
    "nextBefore": null
  }
}
```

- [ ] **Step 2: 실패하는 테스트 작성 — `trader/tests/test_toss.py`**

파일 맨 위 import 블록(`import itertools`부터 `from toss import ...`까지)을 아래로 바꾼다.

```python
import itertools
import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import requests

from bars import KST, Bar
from toss import TossAuthError, TossClient, TossError
```

파일 끝에 추가:

```python
DAY = date(2026, 9, 14)       # 월요일
PREV_DAY = date(2026, 9, 11)  # 금요일
FIXTURES = Path(__file__).parent / "fixtures"


def candle(end, price="70000"):
    """끝나는 시각 end의 캔들 한 개를 응답 형식으로 만든다."""
    return {"timestamp": end.isoformat(), "openPrice": price, "highPrice": price,
            "lowPrice": price, "closePrice": price, "volume": "100", "currency": "KRW"}


def session_ends(day):
    """08:01~20:00 매분, 봉이 끝나는 시각 720개를 만든다."""
    first = datetime.combine(day, time(8, 1), KST)
    return [first + timedelta(minutes=i) for i in range(720)]


def market(ends, next_offset):
    """before 이하 봉을 최신순 200개씩 주고 nextBefore를 마지막 봉 시각 + next_offset으로 주는 on_get을 만든다."""
    def on_get(params):
        before = datetime.fromisoformat(params["before"])
        page = sorted((e for e in ends if e <= before), reverse=True)[:200]
        return ok([candle(e) for e in page], (page[-1] + next_offset).isoformat() if page else None)
    return on_get


def test_fetch_day_parses_candle_fields(tmp_path):
    """캔들을 봉 시작 시각(−1분)·Decimal 가격·int 거래량의 Bar로 바꿔 오름차순으로 반환한다."""
    body = json.loads((FIXTURES / "toss_candles.json").read_text(encoding="utf-8"))
    fake = FakeToss(on_get=lambda params: FakeResponse(body))
    bars = make_client(tmp_path, fake).fetch_day("005930", DAY)
    assert [b.ts for b in bars] == [datetime(2026, 9, 14, 9, m, tzinfo=KST) for m in (0, 1, 2)]
    assert bars[-1] == Bar(datetime(2026, 9, 14, 9, 2, tzinfo=KST), Decimal("70100"),
                           Decimal("70300"), Decimal("70000"), Decimal("70200"), 15234)
    assert len(fake.gets()) == 1


@pytest.mark.parametrize("next_offset", [timedelta(minutes=-1), timedelta(0)])
def test_fetch_day_walks_pages_back_to_previous_day(tmp_path, next_offset):
    """200개씩 거꾸로 받아 전날 봉에서 멈추고, nextBefore 겹침과 무관하게 중복 없는 720봉을 반환한다."""
    fake = FakeToss(on_get=market(session_ends(PREV_DAY) + session_ends(DAY), next_offset))
    bars = make_client(tmp_path, fake).fetch_day("005930", DAY)
    assert len(bars) == 720
    assert bars[0].ts == datetime(2026, 9, 14, 8, 0, tzinfo=KST)
    assert bars[-1].ts == datetime(2026, 9, 14, 19, 59, tzinfo=KST)
    assert all(a.ts < b.ts for a, b in zip(bars, bars[1:]))
    assert len(fake.gets()) == 4
    _, url, kwargs = fake.gets()[0]
    assert url == "https://toss.test/api/v1/candles"
    assert kwargs["params"] == {"symbol": "005930", "interval": "1m", "count": 200,
                                "before": "2026-09-14T23:59:59+09:00", "adjusted": "false"}


@pytest.mark.parametrize("page", [
    ok([candle(e) for e in reversed(session_ends(PREV_DAY)[-200:])], "2026-09-11T16:40:00+09:00"),
    ok([], None),
])
def test_fetch_day_returns_empty_for_day_without_bars(tmp_path, page):
    """첫 페이지가 전날 봉뿐이거나 비어 있으면 요청 1번으로 빈 목록을 반환한다."""
    fake = FakeToss(on_get=lambda params: page)
    assert make_client(tmp_path, fake).fetch_day("005930", DAY) == []
    assert len(fake.gets()) == 1


def test_fetch_day_raises_after_10_pages(tmp_path):
    """10페이지를 받아도 멈춤 조건에 닿지 않으면 PAGINATION 예외를 낸다."""
    end = datetime(2026, 9, 14, 12, 0, tzinfo=KST)
    fake = FakeToss(on_get=lambda params: ok([candle(end)], end.isoformat()))
    with pytest.raises(TossError) as e:
        make_client(tmp_path, fake).fetch_day("005930", DAY)
    assert e.value.code == "PAGINATION"
    assert len(fake.gets()) == 10


def test_fetch_day_rejects_body_without_candles(tmp_path):
    """200 응답에 result.candles가 없으면 빈 날로 기록되지 않도록 BAD_RESPONSE 예외를 낸다."""
    fake = FakeToss(on_get=lambda params: FakeResponse(None))
    with pytest.raises(TossError) as e:
        make_client(tmp_path, fake).fetch_day("005930", DAY)
    assert e.value.code == "BAD_RESPONSE"
```

- [ ] **Step 3: 테스트 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests\test_toss.py -v`
Expected: 새 7개 FAIL `AttributeError: 'TossClient' object has no attribute 'fetch_day'`, 기존 15개 PASS

- [ ] **Step 4: `trader/toss.py`에 구현 추가**

모듈 docstring과 import·상수 블록(`"""토스증권 ...`부터 `REISSUE_CODES = ...`까지)을 아래로 바꾼다.

```python
"""토스증권 Open API 클라이언트: 토큰 캐시, 호출 간격 제한, 재시도, 하루치 1분봉 조회."""
import json
import time as _time
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import requests

from bars import KST, Bar

NETWORK_DELAYS = (1, 2, 4)
RATE_LIMIT_RETRIES = 3
REISSUE_CODES = {"expired-token", "token-revoked", "invalid-token"}
CANDLES_PATH = "/api/v1/candles"
PAGE_SIZE = 200
MAX_PAGES = 10
```

`TossClient` 클래스 끝(`_get` 다음)에 추가:

```python
    def fetch_day(self, symbol, day: date):
        """day 하루치 1분봉을 최신순 페이지로 거꾸로 받아 봉 시작 시각 오름차순으로 반환한다."""
        bars = {}
        before = f"{day.isoformat()}T23:59:59+09:00"
        for _ in range(MAX_PAGES):
            body = self._get(CANDLES_PATH, {"symbol": symbol, "interval": "1m", "count": PAGE_SIZE,
                                            "before": before, "adjusted": "false"})
            try:
                candles = body["result"]["candles"]
                next_before = body["result"].get("nextBefore")
            except (KeyError, TypeError, AttributeError):
                raise TossError("BAD_RESPONSE") from None
            reached_prev_day = False
            for c in candles:
                end = datetime.fromisoformat(c["timestamp"]).astimezone(KST)
                if end.date() < day:
                    reached_prev_day = True
                elif end.date() == day:
                    ts = end - timedelta(minutes=1)
                    bars[ts] = Bar(ts, Decimal(c["openPrice"]), Decimal(c["highPrice"]),
                                   Decimal(c["lowPrice"]), Decimal(c["closePrice"]),
                                   int(Decimal(c["volume"])))
            if reached_prev_day or not candles or not next_before:
                break
            before = next_before
        else:
            raise TossError("PAGINATION")
        return [bars[ts] for ts in sorted(bars)]
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests\test_toss.py -v`
Expected: `22 passed`

Run: `.\.venv\Scripts\python -m pytest tests -q`
Expected: `94 passed`

- [ ] **Step 6: 커밋**

```powershell
git add toss.py tests/test_toss.py tests/fixtures/toss_candles.json
git commit -m "토스 하루치 1분봉 페이지 조회 추가"
```

---

### Task 4: 수집기를 토스 기반으로 재작성하고 KIS 제거

**Files:**
- Modify: `trader/store.py:64-79` (`done_symbols`·`failed_runs` → `done_days`)
- Rewrite: `trader/collector.py`
- Rewrite: `trader/tests/test_collector.py`
- Modify: `trader/tests/test_store.py:54-68` (테스트 2개 → 1개)
- Rewrite: `trader/.env.example`, `trader/README.md`
- Delete: `trader/kis.py`, `trader/tests/test_kis.py`, `trader/tests/fixtures/minute_page.json`

**Interfaces:**
- Consumes: `TossClient(client_id, client_secret, base_url, rps, token_path)`, `TossClient.get_token()`, `TossClient.fetch_day(symbol, day) -> list[Bar]`, `TossError`, `TossAuthError`, `store.save_bars(conn, symbol, bars, source)`, `store.record_run(conn, symbol, trade_date, bar_count, status, error=None)`, `notify.send_telegram(text)`
- Produces:
  - `store.done_days(conn, symbol) -> set[date]`
  - `collector.read_symbols(path) -> list[str]` (기존 그대로, `load_yahoo.py`가 사용)
  - `collector.target_days(now: datetime) -> list[date]` (최신순)
  - `collector.collect(conn, client, symbols, days) -> tuple[dict[str, dict[str, int]], list[tuple[str, date, str]]]`
  - `collector.format_alert(run_date: date, failures) -> str`
  - `collector.main(now=None) -> int`

- [ ] **Step 1: `tests/test_store.py` 테스트 교체**

`test_done_symbols_includes_ok_and_empty_only`와 `test_failed_runs_lists_errors_sorted` 두 함수(54~68행)를 지우고 그 자리에 아래를 넣는다.

```python
def test_done_days_includes_ok_and_empty_only(conn):
    """완료 날짜는 해당 종목의 ok·empty만이고 error와 다른 종목은 제외한다."""
    store.record_run(conn, "A", TODAY, 720, "ok")
    store.record_run(conn, "A", date(2026, 9, 11), 0, "empty")
    store.record_run(conn, "A", date(2026, 9, 10), 0, "error", "x")
    store.record_run(conn, "B", date(2026, 9, 9), 720, "ok")
    assert store.done_days(conn, "A") == {TODAY, date(2026, 9, 11)}
```

- [ ] **Step 2: `tests/test_collector.py` 전체 교체**

```python
import logging
import os
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
import requests

import collector
import notify
import store
from bars import KST, Bar
from toss import TossAuthError, TossError

NOW = datetime(2026, 9, 15, 21, 0, tzinfo=KST)  # 화요일
DAY = date(2026, 9, 14)                          # 월요일
PREV_DAY = date(2026, 9, 11)                     # 금요일


def bars(day, n):
    """day 09:00부터 n개의 1분봉을 만든다."""
    p = Decimal("70000")
    return [Bar(datetime(day.year, day.month, day.day, 9, m, tzinfo=KST), p, p, p, p, 100)
            for m in range(n)]


class FakeClient:
    """TossClient 대역. results[(symbol, day)]가 예외면 던지고, 없으면 빈 목록을 반환한다."""

    def __init__(self, results=None):
        self.results = results or {}
        self.fetched = []

    def get_token(self):
        """토큰 발급을 흉내 낸다."""
        return "TOK"

    def fetch_day(self, symbol, day):
        """요청을 기록하고 준비된 결과를 돌려준다."""
        self.fetched.append((symbol, day))
        result = self.results.get((symbol, day), [])
        if isinstance(result, Exception):
            raise result
        return result


def statuses(conn):
    """collect_runs를 {(symbol, trade_date): (status, bar_count)}로 읽는다."""
    rows = conn.execute("SELECT symbol, trade_date, status, bar_count FROM collect_runs").fetchall()
    return {(s, d): (st, n) for s, d, st, n in rows}


def test_read_symbols_skips_blank_and_comments(tmp_path):
    """빈 줄과 # 주석을 무시하고 종목코드만 읽는다."""
    path = tmp_path / "symbols.txt"
    path.write_text("# 대형주\n005930\n\n000660  # 하이닉스\n", encoding="utf-8")
    assert collector.read_symbols(path) == ["005930", "000660"]


def test_target_days_weekdays_newest_first_within_1461_days():
    """1461일 전부터 어제까지의 평일만 최신순으로 반환하고, 20:10 전이면 오늘은 뺀다."""
    days = collector.target_days(datetime(2026, 9, 15, 12, 0, tzinfo=KST))
    assert days[0] == DAY
    assert days[-1] == date(2022, 9, 15)
    assert date(2022, 9, 14) not in days
    assert all(d.weekday() < 5 for d in days)
    assert days == sorted(days, reverse=True)
    assert len(days) == 1043


def test_target_days_includes_today_from_2010():
    """평일 20:10부터 오늘을 맨 앞에 포함한다."""
    assert collector.target_days(datetime(2026, 9, 15, 20, 9, tzinfo=KST))[0] == DAY
    assert collector.target_days(datetime(2026, 9, 15, 20, 10, tzinfo=KST))[0] == date(2026, 9, 15)


def test_target_days_excludes_weekend_today():
    """주말에 실행하면 20:10 이후라도 오늘은 없고 직전 금요일부터 시작한다."""
    assert collector.target_days(datetime(2026, 9, 12, 21, 0, tzinfo=KST))[0] == PREV_DAY


def test_collect_records_ok_empty_error(conn):
    """봉 있음은 source='toss'로 저장·ok, 없음은 empty, 오류는 error로 기록하고 실패 목록을 돌려준다."""
    client = FakeClient({("A", DAY): bars(DAY, 3),
                         ("B", DAY): TossError("NETWORK", "ConnectionError")})
    counts, failures = collector.collect(conn, client, ["A", "B"], [DAY, PREV_DAY])
    assert counts == {"A": {"ok": 1, "empty": 1, "error": 0, "skipped": 0},
                      "B": {"ok": 0, "empty": 1, "error": 1, "skipped": 0}}
    assert failures == [("B", DAY, "NETWORK ConnectionError")]
    assert statuses(conn) == {("A", DAY): ("ok", 3), ("A", PREV_DAY): ("empty", 0),
                              ("B", DAY): ("error", 0), ("B", PREV_DAY): ("empty", 0)}
    assert conn.execute("SELECT source, symbol, count(*) FROM minute_bars GROUP BY 1, 2").fetchall() \
        == [("toss", "A", 3)]


def test_collect_skips_done_days_and_retries_errors(conn):
    """해당 종목의 ok·empty 날짜는 건너뛰고 error 날짜는 다시 받으며, 남은 날짜를 최신순으로 요청한다."""
    older = date(2026, 9, 10)
    store.record_run(conn, "A", DAY, 720, "ok")
    store.record_run(conn, "A", PREV_DAY, 0, "error", "x")
    store.record_run(conn, "B", older, 0, "empty")
    client = FakeClient({("A", PREV_DAY): bars(PREV_DAY, 2)})
    collector.collect(conn, client, ["A"], [DAY, PREV_DAY, older])
    assert client.fetched == [("A", PREV_DAY), ("A", older)]
    assert statuses(conn)[("A", PREV_DAY)] == ("ok", 2)


def test_collect_skips_symbol_after_5_consecutive_errors(conn):
    """한 종목에서 오류가 5번 연속이면 남은 날짜를 건너뛰고, 중간에 성공이 있으면 횟수를 다시 센다."""
    days = [DAY - timedelta(days=i) for i in range(8)]
    fail = TossError("HTTP500")
    results = {(s, d): fail for s in ("A", "B") for d in days}
    results[("B", days[4])] = bars(days[4], 1)
    client = FakeClient(results)
    counts, failures = collector.collect(conn, client, ["A", "B"], days)
    assert counts["A"] == {"ok": 0, "empty": 0, "error": 5, "skipped": 3}
    assert counts["B"] == {"ok": 1, "empty": 0, "error": 7, "skipped": 0}
    assert [d for s, d in client.fetched if s == "A"] == days[:5]
    assert len(failures) == 12


def test_collect_propagates_auth_error_without_record(conn):
    """TossAuthError는 기록하지 않고 그대로 올려 실행을 멈춘다."""
    client = FakeClient({("A", DAY): TossAuthError("invalid_client")})
    with pytest.raises(TossAuthError):
        collector.collect(conn, client, ["A", "B"], [DAY])
    assert statuses(conn) == {}
    assert client.fetched == [("A", DAY)]


def test_format_alert_lists_up_to_20_failures():
    """실패가 20건을 넘으면 20줄만 쓰고 나머지는 개수로 적는다."""
    failures = [(f"{i:06d}", DAY, "HTTP500") for i in range(25)]
    lines = collector.format_alert(date(2026, 9, 15), failures).splitlines()
    assert lines[0] == "[수집기] 2026-09-15 실패 25건"
    assert lines[1] == "000000 2026-09-14: HTTP500"
    assert len(lines) == 22
    assert lines[-1] == "외 5건"


@pytest.fixture
def main_env(monkeypatch):
    """main이 실제 .env·텔레그램을 쓰지 않도록 막고, 보낸 알림을 담을 목록을 돌려준다."""
    monkeypatch.setattr(collector, "load_dotenv", lambda *args, **kwargs: None)
    for key in ("TOSS_BASE_URL", "TOSS_RPS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TOSS_CLIENT_ID", "CID-X")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "SECRET-X")
    sent = []
    monkeypatch.setattr(collector.notify, "send_telegram", sent.append)
    return sent


def test_main_missing_env_fails_without_secrets(main_env, monkeypatch, caplog):
    """설정이 빠지면 1을 반환하고, 로그·알림에는 빠진 변수 이름과 예외 종류만 남긴다."""
    monkeypatch.delenv("TOSS_CLIENT_ID")
    monkeypatch.setenv("DATABASE_URL", "SECRETPW")
    with caplog.at_level(logging.INFO):
        assert collector.main(NOW) == 1
    assert ".env 누락: TOSS_CLIENT_ID" in caplog.text
    assert "실행 실패: RuntimeError" in caplog.text
    assert "SECRET-X" not in caplog.text and "SECRETPW" not in caplog.text
    assert main_env == ["[수집기] 2026-09-15 실행 실패: RuntimeError"]


def test_main_hides_connection_string_on_db_failure(main_env, monkeypatch, caplog):
    """DB 접속 실패 시 예외 종류만 로그·알림에 남기고 접속 문자열은 남기지 않는다."""
    monkeypatch.setenv("DATABASE_URL", "SECRETPW")
    with caplog.at_level(logging.INFO):
        assert collector.main(NOW) == 1
    assert "실행 실패: ProgrammingError" in caplog.text
    assert "SECRETPW" not in caplog.text
    assert main_env == ["[수집기] 2026-09-15 실행 실패: ProgrammingError"]


def test_main_alerts_only_when_dates_fail(conn, main_env, monkeypatch):
    """토스 기본 설정으로 클라이언트를 만들고, 실패 날짜가 있을 때만 실패 알림을 보낸다."""
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setattr(collector, "target_days", lambda now: [DAY])
    fake = FakeClient({("A", DAY): bars(DAY, 2), ("B", DAY): TossError("HTTP500")})
    created = []
    monkeypatch.setattr(collector, "TossClient", lambda *args: created.append(args[:4]) or fake)

    monkeypatch.setattr(collector, "read_symbols", lambda path: ["A"])
    assert collector.main(NOW) == 0
    assert main_env == []

    monkeypatch.setattr(collector, "read_symbols", lambda path: ["B"])
    assert collector.main(NOW) == 0
    assert main_env == ["[수집기] 2026-09-15 실패 1건\nB 2026-09-14: HTTP500"]
    assert created[0] == ("CID-X", "SECRET-X", "https://openapi.tossinvest.com", 15.0)


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

- [ ] **Step 3: 테스트 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests\test_collector.py tests\test_store.py -v`
Expected: `test_done_days_includes_ok_and_empty_only` FAIL (`no attribute 'done_days'`). `test_target_days_*`·`test_collect_*`·`test_format_alert_*`·`test_main_*` FAIL (`no attribute 'target_days'`, `format_alert` 문구 불일치 등). `test_read_symbols_*`, `test_send_telegram_*`, 나머지 store 테스트는 PASS

- [ ] **Step 4: `store.py` 수정**

`done_symbols`와 `failed_runs` 두 함수(64~79행)를 지우고 아래로 바꾼다.

```python
def done_days(conn, symbol):
    """해당 종목에서 ok 또는 empty로 끝난 날짜 집합을 반환한다."""
    rows = conn.execute(
        "SELECT trade_date FROM collect_runs WHERE symbol = %s AND status IN ('ok', 'empty')",
        (symbol,),
    ).fetchall()
    return {r[0] for r in rows}
```

- [ ] **Step 5: `collector.py` 전체 교체**

```python
"""토스증권 1분봉 수집기 진입점. 작업 스케줄러가 평일 20:30에 실행하고, 첫 백필은 수동 실행한다."""
import logging
import os
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

import psycopg
from dotenv import load_dotenv

import notify
import store
from bars import KST
from toss import TossAuthError, TossClient, TossError

ROOT = Path(__file__).resolve().parent
HISTORY_DAYS = 1461
TODAY_FROM = time(20, 10)
MAX_CONSECUTIVE_ERRORS = 5
MAX_ALERT_LINES = 20
REQUIRED_ENV = ("TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET", "DATABASE_URL")

log = logging.getLogger("collector")


def read_symbols(path):
    """종목코드 파일을 읽는다. 빈 줄과 # 주석은 무시한다."""
    codes = []
    for line in path.read_text(encoding="utf-8").splitlines():
        code = line.split("#", 1)[0].strip()
        if code:
            codes.append(code)
    return codes


def target_days(now):
    """실행일 1461일 전부터 어제까지의 평일과, 20:10 이후면 오늘(평일일 때)을 최신순으로 반환한다."""
    today = now.date()
    first = today - timedelta(days=HISTORY_DAYS)
    last = today if now.time() >= TODAY_FROM else today - timedelta(days=1)
    days = (last - timedelta(days=i) for i in range((last - first).days + 1))
    return [d for d in days if d.weekday() < 5]


def collect(conn, client, symbols, days):
    """종목마다 완료되지 않은 날짜를 최신순으로 수집·기록하고 (종목별 상태 개수, 실패 목록)을 반환한다."""
    counts, failures = {}, []
    for symbol in symbols:
        done = store.done_days(conn, symbol)
        todo = [d for d in days if d not in done]
        c = counts[symbol] = {"ok": 0, "empty": 0, "error": 0, "skipped": 0}
        streak = 0
        for i, day in enumerate(todo):
            if streak >= MAX_CONSECUTIVE_ERRORS:
                c["skipped"] = len(todo) - i
                log.error("%s 연속 오류 %d회, 남은 %d일 건너뜀", symbol, streak, c["skipped"])
                break
            try:
                bars = client.fetch_day(symbol, day)
            except TossAuthError:
                raise
            except Exception as e:
                store.record_run(conn, symbol, day, 0, "error", str(e))
                failures.append((symbol, day, str(e)))
                c["error"] += 1
                streak += 1
                log.error("%s %s error %s", symbol, day, e)
                continue
            if bars:
                store.save_bars(conn, symbol, bars, source="toss")
            status = "ok" if bars else "empty"
            store.record_run(conn, symbol, day, len(bars), status)
            c[status] += 1
            streak = 0
            log.info("%s %s %s %d", symbol, day, status, len(bars))
        log.info("%s ok %d / empty %d / error %d / skipped %d",
                 symbol, c["ok"], c["empty"], c["error"], c["skipped"])
    return counts, failures


def format_alert(run_date, failures):
    """실패 날짜 알림 문구를 만든다. 최대 20줄, 나머지는 개수만 적는다."""
    lines = [f"[수집기] {run_date} 실패 {len(failures)}건"]
    lines += [f"{symbol} {day}: {error}" for symbol, day, error in failures[:MAX_ALERT_LINES]]
    if len(failures) > MAX_ALERT_LINES:
        lines.append(f"외 {len(failures) - MAX_ALERT_LINES}건")
    return "\n".join(lines)


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
    """수집 1회 실행. 날짜 단위 오류만 있으면 0, 설정·DB·인증 등 실행 전체 실패면 1을 반환한다."""
    now = now or datetime.now(KST)
    load_dotenv(ROOT / ".env")
    setup_logging(now.date())
    try:
        missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
        if missing:
            log.error(".env 누락: %s", ", ".join(missing))
            raise RuntimeError("missing env")
        client = TossClient(
            os.environ["TOSS_CLIENT_ID"], os.environ["TOSS_CLIENT_SECRET"],
            os.environ.get("TOSS_BASE_URL") or "https://openapi.tossinvest.com",
            float(os.environ.get("TOSS_RPS") or "15"), ROOT / ".token.json",
        )
        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
            client.get_token()
            counts, failures = collect(conn, client, read_symbols(ROOT / "symbols.txt"), target_days(now))
    except Exception as e:
        # 예외 문자열에 접속 문자열 등이 섞일 수 있어 종류와 토스 오류 코드만 남긴다
        log.error("실행 실패: %s %s", type(e).__name__, e.code if isinstance(e, TossError) else "")
        notify.send_telegram(f"[수집기] {now.date()} 실행 실패: {type(e).__name__}")
        return 1
    total = {k: sum(c[k] for c in counts.values()) for k in ("ok", "empty", "error", "skipped")}
    log.info("요약 ok %(ok)d / empty %(empty)d / error %(error)d / skipped %(skipped)d", total)
    if failures:
        notify.send_telegram(format_alert(now.date(), failures))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: KIS 파일 삭제와 `.env.example` 교체**

```powershell
git rm kis.py tests/test_kis.py tests/fixtures/minute_page.json
```

`trader/.env.example` 전체 교체:

```
TOSS_CLIENT_ID=
TOSS_CLIENT_SECRET=
TOSS_BASE_URL=https://openapi.tossinvest.com
TOSS_RPS=15
DATABASE_URL=postgresql://trader:비밀번호@localhost:5432/trader
TEST_DATABASE_URL=postgresql://trader:비밀번호@localhost:5432/trader_test
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

- [ ] **Step 7: 테스트 통과와 KIS 잔재 확인**

Run: `.\.venv\Scripts\python -m pytest tests -q`
Expected: `82 passed` (94 − KIS 17 − 기존 수집기 8 + 새 수집기 14 − store 2 + 1)

Run: `Select-String -Path *.py,tests\*.py -Pattern 'kis|KIS'`
Expected: `store.py`의 `source="kis"` 기본값, `backtest.py`의 `--source` 도움말과 ponytail 주석(Task 5에서 변경), `tests\test_store.py`·`tests\test_backtest.py`의 `source="kis"`/`'kis'` 줄만. `import kis`나 `KisClient`는 없어야 한다

- [ ] **Step 8: `trader/README.md` 전체 교체**

````markdown
# 토스증권 1분봉 수집기와 백테스터

`symbols.txt` 종목의 1분봉을 토스증권 Open API에서 받아 PostgreSQL에 저장하고, 저장한 봉으로 전략을 백테스트한다.
설계: `docs/superpowers/specs/2026-09-15-toss-collector-design.md`, `docs/superpowers/specs/2026-09-15-backtester-design.md`

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
   - `TOSS_CLIENT_ID`, `TOSS_CLIENT_SECRET`: 토스증권 WTS 설정 > Open API에서 발급 (시세 조회만 사용)
   - 같은 메뉴 하단 **허용 IP 관리**에 이 PC의 공인 IP를 등록한다. 없으면 403 `access_denied`로 실행 실패
   - `TOSS_RPS`: 초당 호출 수. 차트 API 한도(초당 20회)보다 낮게 둔다. 기본 15
   - `TELEGRAM_BOT_TOKEN`: BotFather로 만든 봇 토큰
   - `TELEGRAM_CHAT_ID`: 봇에게 메시지를 보낸 뒤 `https://api.telegram.org/bot<토큰>/getUpdates`의 `chat.id`

4. 테스트: `.\.venv\Scripts\python -m pytest tests -v`

## 운영 DB 스키마 갱신

`minute_bars.source` 컬럼과 백테스트 결과 테이블이 필요하다. 수집기를 처음 실행하기 전에 적용한다(여러 번 적용해도 안전, 기존 데이터 보존).

```powershell
D:\PIE\PostgreSQL_15\bin\psql.exe -h localhost -U trader -d trader -v ON_ERROR_STOP=1 -f schema.sql
```

확인: `SELECT source, count(*) FROM minute_bars GROUP BY 1;`

## 수집기

```powershell
.\.venv\Scripts\python collector.py
```

한 번 실행하면 종목마다 최근 1461일(약 4년)의 평일 중 `collect_runs`에 `ok`·`empty`가 없는 날짜를 최신 날짜부터 받는다. 20:10 이후 실행이면 오늘도 포함한다.

- 저장: `minute_bars.source='toss'`, 받은 봉 전부(08:00~19:59). NXT 미거래 종목은 09:00~15:29만 오고, NXT 출범 전 날짜는 확장 시간이 거래량 0인 채움 봉이다. `ts`는 봉 시작 시각, 가격은 수정주가 미적용 원래 체결가
- 기록: 종목·날짜마다 `collect_runs`에 `ok`(봉 수) / `empty`(휴장일, 상장 전 등) / `error`. `error` 날짜는 다음 실행에서 다시 받는다
- 한 종목에서 오류가 5번 연속 나면 이번 실행에서는 그 종목의 남은 날짜를 건너뛴다
- 날짜 오류가 있으면 실행 끝에 텔레그램 알림 1통을 보낸다. 인증·DB·설정 오류로 실행 전체가 실패하면 실행 실패 알림을 보내고 종료 코드 1
- 첫 백필은 종목당 약 4분(50종목 3~4시간)이다. 작업 스케줄러 등록 전에 수동으로 한 번 실행한다
- 동시에 두 개를 실행하지 않는다. 수동 실행은 스케줄 시각(20:30)을 피한다
- 로그: `logs/collector-YYYY-MM-DD.log`

## 작업 스케줄러 등록

평일 20:30에 1회 실행한다(NXT 마감 20:00 이후 당일 완성본). PC가 꺼져 있어 놓친 날은 다음 실행이 채운다. 관리자 PowerShell에서 `trader` 폴더 기준으로 실행:

```powershell
$dir = (Get-Location).Path
$action = New-ScheduledTaskAction -Execute "$dir\.venv\Scripts\python.exe" -Argument "collector.py" -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 20:30
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 6)
Register-ScheduledTask -TaskName "토스 분봉 수집기" -Action $action -Trigger $trigger -Settings $settings
```

예전에 `KIS 분봉 수집기` 작업을 등록했다면 지운다: `Unregister-ScheduledTask -TaskName "KIS 분봉 수집기" -Confirm:$false`

## 누락 확인

```sql
SELECT trade_date, symbol, status, bar_count, error
FROM collect_runs
WHERE status = 'error' OR (status = 'ok' AND bar_count < 390)
ORDER BY trade_date DESC, symbol;
```

정상 거래일 봉 수는 NXT 거래 종목 720, 정규장만 거래하는 종목 390이다. 수능일·연초 개장일 같은 단축 거래일은 더 적을 수 있다.

## Yahoo 임시 데이터 적재

토스 데이터가 쌓이기 전 개발·검증용. 최근 약 7거래일, 하루 360봉(09:00~14:59, 15시 이후 봉 없음). 비공식 API라 언제든 막힐 수 있다.

```powershell
.\.venv\Scripts\python load_yahoo.py              # symbols.txt 전 종목
.\.venv\Scripts\python load_yahoo.py 005930 000660
```

## 백테스트

```powershell
.\.venv\Scripts\python backtest.py --strategy ma_cross,orb --source toss --from 2026-08-17 --to 2026-09-14
```

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--strategy` | (필수) | 쉼표 구분. 전략마다 실행 1건 저장 |
| `--source` | (필수) | `toss` 또는 `yahoo` |
| `--from`, `--to` | (필수) | KST 날짜, 양끝 포함 |
| `--symbols` | 전 종목 | 쉼표 구분 종목코드 |
| `--param key=value` | 전략 기본값 | 여러 번 지정. 그 키를 가진 전략에만 적용 |
| `--fee` | 0.00015 | 매수·매도 각각 |
| `--tax` | 0.002 | 매도 거래세 (실제 세율 확인 필요) |
| `--slippage` | 0.0005 | 매수가↑·매도가↓ |
| `--exit-at` | 15:15 | 이후 진입 금지, 보유분 그 봉 시가에 청산 |

전략 (`strategies.py`):
- `ma_cross` (`short=5`, `long=20`): 종가 단기 이동평균이 장기를 상향 돌파하면 매수, 하향 돌파하면 매도
- `orb` (`range_end=09:30`, `stop_pct=-1.0`, `target_pct=2.0`): 범위 시간 고가를 종가로 돌파하면 하루 1회 매수, 손절·목표 도달 시 매도

체결 규칙: 신호 다음 봉 시가 체결, 매수만, 종목당 1포지션, 장 마감 전 강제 청산(데이터가 먼저 끝나면 마지막 봉 종가).

새 전략 추가: `Strategy`를 상속해 `name`, `defaults`, `on_bar(bar, entry_price)`를 구현하고 `STRATEGIES`에 등록.

결과 조회 예 (일별·시간대별):

```sql
SELECT (entry_ts AT TIME ZONE 'Asia/Seoul')::date AS day,
       CASE WHEN (entry_ts AT TIME ZONE 'Asia/Seoul')::time < '12:00' THEN '09-12' ELSE '12-15' END AS slot,
       count(*) AS trades, avg(return_pct) AS avg_ret, sum(return_pct) AS sum_ret
FROM backtest_trades
WHERE run_id = 1
GROUP BY 1, 2
ORDER BY 1, 2;
```

## 첫 실행 수동 검증 (1회)

1. 위 "운영 DB 스키마 갱신" 적용
2. 토스 허용 IP 등록 확인, `.env`에 `TOSS_*` 값 확인. 예전 KIS 토큰 캐시 `.token.json`이 있으면 지운다
3. `symbols.txt`를 005930, 000660 두 종목으로 두고 `.\.venv\Scripts\python collector.py` (약 8분)
4. `SELECT trade_date, status, bar_count FROM collect_runs WHERE symbol='005930' ORDER BY trade_date DESC LIMIT 10;` — 최근 거래일 720, 주말 행 없음, 휴장일 `empty`
5. 임의 봉 3개를 토스 앱 1분 차트와 시가·고가·저가·종가·거래량 대조. 앱에서 `09:01`로 보이는 봉이 DB의 `09:00` 봉이어야 한다
6. 곧바로 다시 실행 → 받을 날짜 0개(20:10 이후면 오늘 1개 이하)
7. `.\.venv\Scripts\python backtest.py --strategy ma_cross,orb --source toss --from <1개월 전> --to <어제>` 결과 확인
8. 작업 스케줄러 등록 후 다음 영업일 로그 확인, 5영업일 연속 실패 알림 없음
````

- [ ] **Step 9: 커밋**

```powershell
git add store.py collector.py tests/test_collector.py tests/test_store.py .env.example README.md
git commit -m "수집기를 토스증권 날짜 단위 백필로 재작성하고 KIS 코드 제거"
```

(`git rm`으로 지운 세 파일은 이미 스테이징되어 함께 커밋된다.)

---

### Task 5: 백테스트 `--session regular|all`

**Files:**
- Modify: `trader/backtest.py` (상수, `--session`·`--source` 도움말, `regular_session`, `main`의 필터·costs, ponytail 주석)
- Modify: `trader/README.md` (옵션 표 한 줄, 안내 한 줄)
- Test: `trader/tests/test_backtest.py` (테스트 2개 추가)

**Interfaces:**
- Consumes: `store.load_bars(...) -> dict[str, list[Bar]]`, `bars.Bar`
- Produces: `backtest.regular_session(bars: dict[str, list[Bar]]) -> dict[str, list[Bar]]`, CLI 옵션 `--session`(`regular` 기본, `all`), `backtest_runs.costs["session"]`

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/test_backtest.py` 끝에 추가**

```python
def bar_at(hour, minute):
    """2026-09-11 hour:minute 시작 봉을 만든다."""
    p = Decimal(100)
    return Bar(datetime(2026, 9, 11, hour, minute, tzinfo=KST), p, p, p, p, 100)


def test_regular_session_keeps_0900_to_1529():
    """정규장 필터는 09:00·15:29 봉을 남기고 08:30·15:30·16:00 봉과 남은 봉이 없는 종목은 뺀다."""
    got = backtest.regular_session({
        "A": [bar_at(8, 30), bar_at(9, 0), bar_at(15, 29), bar_at(15, 30), bar_at(16, 0)],
        "B": [bar_at(8, 0)],
    })
    assert got == {"A": [bar_at(9, 0), bar_at(15, 29)]}


def test_main_session_option(db_env, capsys):
    """regular는 정규장 봉이 없는 종목을 빼고(전부 없으면 1), all은 전 종목을 쓰며 costs에 session을 남긴다."""
    store.save_bars(db_env, "B", [bar_at(16, 0)], source="yahoo")
    assert backtest.main(["--strategy", "orb", *ARGS]) == 1
    assert "봉 데이터 없음" in capsys.readouterr().out

    store.save_bars(db_env, "A", breakout_day(), source="yahoo")
    assert backtest.main(["--strategy", "orb", *ARGS]) == 0
    assert backtest.main(["--strategy", "orb", "--session", "all", *ARGS]) == 0
    rows = db_env.execute("SELECT symbols, costs->>'session' FROM backtest_runs ORDER BY id").fetchall()
    assert rows == [(["A"], "regular"), (["A", "B"], "all")]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests\test_backtest.py -v`
Expected: `test_regular_session_keeps_0900_to_1529` FAIL (`no attribute 'regular_session'`), `test_main_session_option` FAIL (B만 있을 때 0 반환 또는 `unrecognized arguments: --session`). 기존 14개 PASS

- [ ] **Step 3: `backtest.py` 구현**

`ROOT = Path(__file__).resolve().parent` 아래에 추가:

```python
REGULAR_OPEN = time(9, 0)
REGULAR_CLOSE = time(15, 30)
```

`parse_args`에서 `--source` 줄을 바꾸고, `--exit-at` 줄 다음에 `--session`을 추가:

```python
    p.add_argument("--source", required=True, help="minute_bars.source (toss, yahoo)")
```

```python
    p.add_argument("--session", choices=("regular", "all"), default="regular",
                   help="regular: 09:00~15:29 시작 봉만, all: 저장된 봉 전부")
```

`parse_args` 함수 다음(`summarize` 앞)에 추가:

```python
def regular_session(bars):
    """종목별 봉에서 09:00~15:29 시작 봉만 남기고, 남은 봉이 없는 종목은 뺀다."""
    out = {}
    for symbol, symbol_bars in bars.items():
        kept = [b for b in symbol_bars if REGULAR_OPEN <= b.ts.time() < REGULAR_CLOSE]
        if kept:
            out[symbol] = kept
    return out
```

`main`의 봉 조회부터 costs까지를 아래로 바꾼다(ponytail 주석의 `KIS`는 `토스`로):

```python
            # ponytail: 기간 전체 봉을 한 번에 메모리에 올림(1년×50종목≈470만 봉, 수 GB). 수개월 이상 토스 데이터로 돌리기 전에 종목별로 조회·실행하도록 바꿀 것
            bars = store.load_bars(conn, args.source, args.date_from, args.date_to, symbols)
            if args.session == "regular":
                bars = regular_session(bars)
            if not bars:
                print("봉 데이터 없음")
                return 1
            for name, p in params.items():
                trades = engine.run(STRATEGIES[name], p, bars, costs)
                run_id = store.save_run(conn, {
                    "strategy": name, "params": p, "source": args.source,
                    "symbols": sorted(bars), "date_from": args.date_from, "date_to": args.date_to,
                    "costs": {"fee": str(args.fee), "tax": str(args.tax),
                              "slippage": str(args.slippage),
                              "exit_at": args.exit_at.strftime("%H:%M"),
                              "session": args.session},
                }, trades)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests\test_backtest.py -v`
Expected: `16 passed`

Run: `.\.venv\Scripts\python -m pytest tests -q`
Expected: `84 passed`

- [ ] **Step 5: README 갱신**

`README.md` 백테스트 옵션 표의 `--exit-at` 줄 다음에 추가:

```markdown
| `--session` | regular | `regular`: 09:00~15:29 시작 봉만, `all`: 저장된 봉 전부(08:00~19:59) |
```

표 바로 아래에 한 줄 추가:

```markdown
`--session all`이면 08시대 봉부터 전략에 들어가고, `--exit-at` 기본값(15:15) 이후에는 진입하지 않고 청산한다. 확장 시간까지 거래하려면 `--exit-at 19:45`처럼 확장 시간에 맞게 지정한다.
```

- [ ] **Step 6: 커밋**

```powershell
git add backtest.py tests/test_backtest.py README.md
git commit -m "백테스트 정규장·전체 세션 선택 추가"
```

---

### Task 6: 운영 환경 수동 검증

코드 변경 없음. 사용자 PC에서 실제 토스 키·운영 DB로 확인한다. `.env` 값과 토큰은 화면·로그·커밋에 출력하지 않는다.

- [ ] **Step 1: 운영 DB 스키마 적용**

```powershell
D:\PIE\PostgreSQL_15\bin\psql.exe -h localhost -U trader -d trader -v ON_ERROR_STOP=1 -f schema.sql
```

Expected: 오류 없이 종료. `SELECT source, count(*) FROM minute_bars GROUP BY 1;` 실행 가능

- [ ] **Step 2: `.env`와 토큰 캐시 정리**

- `trader/.env`에서 `KIS_` 로 시작하는 4줄 삭제, `TOSS_BASE_URL=https://openapi.tossinvest.com`, `TOSS_RPS=15` 추가 (`TOSS_CLIENT_ID`·`TOSS_CLIENT_SECRET`은 이미 있음)
- 키 이름만 확인: `Select-String -Path .env -Pattern '^(\w+)=' | ForEach-Object { $_.Matches[0].Groups[1].Value }`
- `trader/.token.json`이 있으면 삭제: `Remove-Item .token.json -ErrorAction SilentlyContinue`
- 토스 WTS 설정 > Open API > 허용 IP 관리에 현재 공인 IP가 있는지 사용자에게 확인

- [ ] **Step 3: 두 종목 백필**

`symbols.txt`가 005930, 000660 두 종목인지 확인하고 실행:

```powershell
.\.venv\Scripts\python collector.py
```

Expected: 약 8분, 종료 코드 0, 마지막 줄 `요약 ok N / empty M / error 0 / skipped 0`. `TossAuthError`로 실패하면 로그의 코드(`invalid_client`, `access_denied` 등)를 사용자에게 보고하고 멈춘다

- [ ] **Step 4: 기록 확인**

```sql
SELECT trade_date, status, bar_count FROM collect_runs WHERE symbol='005930' ORDER BY trade_date DESC LIMIT 10;
SELECT min(ts), max(ts), count(*) FROM minute_bars WHERE source='toss' AND symbol='005930' AND ts >= '2026-09-14' AND ts < '2026-09-15';
```

Expected: 최근 거래일 `ok 720`, 주말 행 없음, 휴장일 `empty`. 2026-09-14 봉은 `08:00`~`19:59` KST, 720개

- [ ] **Step 5: 봉 값 대조 (사용자와 함께)**

임의 봉 3개를 골라 토스 앱 1분 차트와 시가·고가·저가·종가·거래량을 대조한다. 앱의 `09:01` 봉이 DB의 `09:00` 봉과 같아야 한다(1분 보정). 다르면 스펙 1절의 "봉 시각" 판단을 사용자와 다시 확인한다

- [ ] **Step 6: 재실행과 백테스트**

```powershell
.\.venv\Scripts\python collector.py
.\.venv\Scripts\python backtest.py --strategy ma_cross,orb --source toss --from <1개월 전 날짜> --to <어제 날짜>
```

Expected: 수집기는 `ok 0 / empty 0 / error 0`(20:10 이후 실행이면 오늘 1일 이하), 백테스트는 `[run N] ma_cross ...`, `[run N+1] orb ...` 두 블록과 종료 코드 0

- [ ] **Step 7: 작업 스케줄러 등록과 남은 작업 보고**

README "작업 스케줄러 등록" 명령을 관리자 PowerShell에서 실행(사용자 승인 후). 사용자에게 남은 작업을 보고한다:
- `symbols.txt`를 전체 종목으로 늘린 뒤 첫 백필 수동 실행(50종목 3~4시간)
- 다음 영업일 20:30 실행 로그 확인, 5영업일 연속 실패 알림 없음 확인
- 매도 거래세 기본값 0.20%는 실제 세율 확인 필요 (백테스터 2a에서 남긴 항목)
