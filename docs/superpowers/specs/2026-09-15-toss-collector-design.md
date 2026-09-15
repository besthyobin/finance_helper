# 토스증권 1분봉 수집기 설계

- 작성일: 2026-09-15
- 대체: `docs/superpowers/specs/2026-09-14-kis-minute-collector-design.md` (KIS 수집기). KIS 코드는 이 작업에서 제거한다.
- 관련: `docs/superpowers/specs/2026-09-15-backtester-design.md` (백테스터 2a)

## 1. 배경

KIS Open API는 당일 1분봉만 제공해 매일 23시 전에 수집하지 못하면 복구할 수 없었고, KIS 계좌·키도 아직 없다. 토스증권 Open API(2026-08-13 정식 서비스)는 과거 1분봉을 제공하고 사용자 키가 이미 발급되어 있어, 수집기의 데이터 소스를 토스증권으로 바꾼다.

### 실측 결과 (2026-09-15, 사용자 키로 시세 API만 호출)

- 토큰: `POST https://openapi.tossinvest.com/oauth2/token` (form, client_credentials) → `expires_in` 86399
- `GET /api/v1/candles?symbol=005930&interval=1m&count=200&before=...` 응답 헤더 `X-RateLimit-Limit: 20`
- 과거 범위: 005930 기준 2022-01-03 봉 있음, 2021-09-15 빈 응답 → **약 4년**
- 하루 봉 수: NXT 거래 종목(005930) 720봉, 첫 봉 `08:01`, 마지막 `20:00`. NXT 미거래 종목(035720, 2024-09-13) 390봉, 09:01~15:30
- NXT 출범 전 날짜(005930, 2024-09-13)도 720봉이며 08시·16~20시 봉은 거래량 0인 채움 봉(339개)
- 봉 시각은 **끝나는 시각 표기**로 판단(첫 봉 08:01, 마지막 20:00)
- 응답은 최신순, `nextBefore`로 다음(과거) 페이지 요청. 실측에서 `nextBefore`는 마지막 봉보다 1분 이른 시각이었으나 공식 예시는 마지막 봉과 같은 시각이다 → 시각 기준 중복 제거로 양쪽 모두 처리

### 공식 문서

- `https://developers.tossinvest.com/llms.txt`
- `https://openapi.tossinvest.com/openapi-docs/overview.md` (Rate Limits: `MARKET_DATA_CHART` 초당 20회, `AUTH` 초당 5회)
- `https://openapi.tossinvest.com/openapi-docs/latest/openapi.json` (v1.2.17)
- 토큰은 클라이언트당 1개만 유효, 재발급 시 이전 토큰 즉시 무효화(`token-revoked`). refresh token 없음
- 허용 IP 목록에 없는 IP의 호출은 403 `access_denied`

### 확정된 결정

| 항목 | 결정 | 이유 |
|---|---|---|
| 소스 | 토스증권 Open API, `minute_bars.source='toss'` | 과거 분봉 제공, 키 보유 |
| KIS | `kis.py`·KIS 테스트·KIS 환경변수 제거, `collector.py`는 토스 기반으로 재작성 | 사용자 선택: 토스가 KIS 자리를 대체 |
| 저장 시간대 | 받은 봉 전부(08:00~19:59, 채움 봉 포함) | NXT 전략 여지, 재수집 불필요. 백테스트에서 세션 필터 |
| 가격 | 원래 체결가(`adjusted=false`) | 저장값 불변. 엔진이 날짜별 독립이라 분할 영향 없음 |
| 시각 | 받은 시각 − 1분 = 봉 시작 시각으로 저장 | Yahoo·엔진·전략 파라미터(`range_end`, `exit_at`)와 같은 기준 |
| 백필 범위 | 제공되는 만큼 전부(실행일 − 1461일부터) | 사용자 선택 |
| 진행 기록 | 날짜 단위, 기존 `collect_runs(symbol, trade_date)` 재사용 | 중단·재개·중간 구멍 자동 처리, 휴장일 달력 불필요 |
| 실행 | 작업 스케줄러 평일 20:30 1회, 첫 백필은 수동 실행 | NXT 마감 후 당일 완성본, 놓친 날은 다음 실행이 채움 |

## 2. 범위

**포함**
- `bars.py`: `KST`, `Bar` 공용 모듈 (기존 `kis.py`에서 이동)
- `toss.py`: 토큰 캐시, 호출 간격, 재시도, 하루치 1분봉 조회
- `collector.py` 재작성: 대상 날짜 계산, 종목·날짜별 수집·기록, 실패 알림
- `store.done_days` 추가, `done_symbols`·`failed_runs` 제거
- `backtest.py`: `--session regular|all`
- import 경로 변경(`kis` → `bars`), `.env.example`·README 갱신

**제외**
- 주문·계좌 API, 웹소켓 실시간 시세
- 일봉 수집, 수정주가 저장, 종목 자동 선정
- KIS·Yahoo 데이터 이관 또는 삭제 (`load_yahoo.py`는 유지)
- 휴장일 달력 API 사용
- ORM, 비동기, 설정 클래스, 재시도 라이브러리 (의존성은 `requests`, `psycopg[binary]`, `python-dotenv`, `pytest` 4개 유지)

## 3. 구성 요소

```
trader/
├── .env                 # TOSS_CLIENT_ID, TOSS_CLIENT_SECRET, TOSS_BASE_URL, TOSS_RPS, DATABASE_URL, TEST_DATABASE_URL, TELEGRAM_* (git 제외)
├── .env.example
├── .token.json          # 토스 액세스 토큰 캐시 (git 제외, 기존 .gitignore 항목)
├── bars.py              # 신규: KST, Bar
├── toss.py              # 신규: TossClient
├── collector.py         # 재작성
├── store.py             # done_days 추가, done_symbols·failed_runs 제거
├── backtest.py          # --session 추가
├── load_yahoo.py        # import 변경
├── notify.py            # 변경 없음
├── engine.py, strategies.py, schema.sql   # 변경 없음
└── tests/
    ├── test_toss.py            # 신규 (test_kis.py 대체)
    ├── fixtures/toss_candles.json   # 신규 (minute_page.json 대체)
    ├── test_collector.py       # 재작성
    ├── test_store.py           # done_days 테스트로 교체
    ├── test_backtest.py        # 세션 필터 테스트 추가
    └── test_engine.py, test_strategies.py, test_load_yahoo.py   # import만 변경
```

삭제: `kis.py`, `tests/test_kis.py`, `tests/fixtures/minute_page.json`.

### 3.1 `bars.py`

```python
KST = timezone(timedelta(hours=9))

@dataclass(frozen=True)
class Bar:
    ts: datetime      # KST, 봉 시작 시각
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
```

`store.py`, `load_yahoo.py`, 모든 테스트의 `from kis import ...`를 `from bars import ...`로 바꾼다.

### 3.2 `toss.py`

```python
class TossError(Exception):        # code 속성: 토스 error.code, "NETWORK", "HTTP<status>", "PAGINATION"
class TossAuthError(TossError):    # 실행 전체를 중단해야 하는 인증·권한 오류

class TossClient:
    def __init__(self, client_id, client_secret, base_url, rps, token_path,
                 send=requests.request, sleep=time.sleep, clock=time.monotonic,
                 now=lambda: datetime.now(KST)): ...
    def get_token(self) -> str: ...
    def fetch_day(self, symbol: str, day: date) -> list[Bar]: ...
```

**토큰 (`get_token`)**
- 메모리에 있으면 반환. `token_path` 파일의 `{"access_token", "expires_at"}`가 현재 + 5분 이후 만료면 재사용
- 아니면 `POST {base_url}/oauth2/token`, `data={"grant_type": "client_credentials", "client_id", "client_secret"}`, timeout 10초
- 200이면 `expires_at = now + expires_in초`로 파일에 저장하고 반환
- 401·403 → `TossAuthError(응답의 error 값 또는 "HTTP<status>")`
- 429 → `Retry-After`(없으면 1)초 대기 후 재시도, 최대 3회. 그 외 → `TossError`

**호출 (`_get(path, params) -> dict`)**
- 매 호출 전 직전 호출로부터 `1/rps`초 경과하도록 대기
- 헤더 `Authorization: Bearer {token}`, timeout 10초
- 200 → JSON 본문 반환
- 401이고 `error.code`가 `expired-token`·`token-revoked`·`invalid-token`이면 메모리·파일 토큰을 지우고 1회만 재발급 후 같은 호출 재시도. 재발급 후에도 401이면 `TossAuthError`
- 403 → `TossAuthError`
- 429 → `Retry-After`(없으면 1)초 대기 후 재시도, 최대 3회 초과 시 `TossError(code)`
- 네트워크 예외(`requests.RequestException`) 또는 `error.code` 없는 5xx → 1·2·4초 간격 3회 재시도 후 `TossError("NETWORK")` 또는 `TossError("HTTP<status>")`
- 그 밖 → `TossError(error.code 또는 "HTTP<status>")`
- 예외 메시지·로그에 client_id, client_secret, 토큰, 요청 URL 원문을 넣지 않는다(`requests` 예외는 종류 이름만)

**하루치 1분봉 (`fetch_day(symbol, day)`)**
1. `before = f"{day}T23:59:59+09:00"`
2. `_get("/api/v1/candles", {"symbol": symbol, "interval": "1m", "count": 200, "before": before, "adjusted": "false"})`
3. `result.candles` 각 봉의 `timestamp`를 `datetime.fromisoformat`으로 파싱해 KST 날짜가 `day`인 봉만 모은다(시각 기준 dict로 중복 제거)
4. 페이지에 `day`보다 이른 날짜의 봉이 있거나, `candles`가 비었거나, `nextBefore`가 null이면 멈춘다. 아니면 `before = nextBefore`로 2부터 반복
5. 최대 10페이지. 10페이지를 받고도 멈춤 조건에 닿지 않으면 `TossError("PAGINATION")`
6. 변환: `ts = timestamp − 1분`(KST), `open/high/low/close = Decimal(openPrice/highPrice/lowPrice/closePrice)`, `volume = int(volume)`
7. 시각 오름차순 `list[Bar]` 반환. 봉이 없으면 `[]`

### 3.3 `store.py` 변경

- 추가: `done_days(conn, symbol) -> set[date]` — `collect_runs`에서 해당 종목의 `status IN ('ok','empty')`인 `trade_date` 집합
- 제거: `done_symbols`, `failed_runs` (호출처 없음)
- 유지: `save_bars`, `load_bars`, `save_run`, `record_run`

### 3.4 `collector.py`

```python
HISTORY_DAYS = 1461
TODAY_FROM = time(20, 10)
MAX_CONSECUTIVE_ERRORS = 5
MAX_ALERT_LINES = 20
REQUIRED_ENV = ("TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET", "DATABASE_URL")

def read_symbols(path) -> list[str]            # 기존 유지 (load_yahoo도 사용)
def target_days(now) -> list[date]              # 최신순
def collect(conn, client, symbols, days) -> tuple[dict[str, dict[str, int]], list[tuple[str, date, str]]]
def format_alert(run_date, failures) -> str
def setup_logging(today)                        # 기존 유지
def main(now=None) -> int
```

**`target_days(now)`**: `now.date() − 1461일`부터 어제까지의 평일(월~금), 그리고 `now.time() >= 20:10`이고 오늘이 평일이면 오늘. 최신 날짜가 먼저 오도록 정렬.

**`collect(conn, client, symbols, days)`**: 종목마다
1. `todo = [d for d in days if d not in store.done_days(conn, symbol)]` (최신순 유지)
2. 날짜마다 `client.fetch_day(symbol, d)`
   - 봉 있음 → `store.save_bars(conn, symbol, bars, source="toss")`, `record_run(..., len(bars), "ok")`
   - 봉 없음 → `record_run(..., 0, "empty")`
   - `TossAuthError` → 기록하지 않고 그대로 올린다(실행 중단)
   - 그 밖 예외 → `record_run(..., 0, "error", str(e))`, 실패 목록에 `(symbol, d, str(e))` 추가
3. 같은 종목에서 `error`가 연속 5번이면 그 종목의 남은 날짜를 건너뛰고(`skipped`에 개수) 로그를 남긴다. 성공(`ok`/`empty`)이 나오면 연속 횟수는 0으로
4. 종목별 `{"ok", "empty", "error", "skipped"}` 개수를 로그에 남기고 반환값에 담는다

반환: (종목별 개수 dict, 실패 목록)

**`format_alert(run_date, failures)`**: 첫 줄 `[수집기] {run_date} 실패 {N}건`, 이후 `{symbol} {date}: {error}` 최대 20줄, 초과분은 `외 {N−20}건`.

**`main(now=None)`**
1. `now = now or datetime.now(KST)`, `.env` 로드, 로그 설정
2. `REQUIRED_ENV` 누락 → 실행 실패
3. `TossClient(TOSS_CLIENT_ID, TOSS_CLIENT_SECRET, TOSS_BASE_URL(기본 https://openapi.tossinvest.com), float(TOSS_RPS(기본 15)), ROOT/".token.json")`
4. DB 연결(autocommit) → `client.get_token()` → `collect(conn, client, read_symbols(symbols.txt), target_days(now))`
5. 전체 요약 로그. 실패 목록이 있으면 `notify.send_telegram(format_alert(now.date(), failures))`
6. 반환 0
7. 2~5 중 예외(`TossAuthError`, DB 오류, 설정 누락 등) → 로그 `실행 실패: {예외 종류} {TossError면 code}`, 텔레그램 `[수집기] {now.date()} 실행 실패: {예외 종류}`, 반환 1. 예외 메시지 원문(`str(e)`)은 `TossError`의 code 외에는 출력하지 않는다(접속 문자열 노출 방지)

기존 `should_run`(15:35 이후 평일만)은 제거한다. 과거를 다시 받을 수 있어 실행 시각 제한이 필요 없고, 오늘 날짜 포함 여부는 `target_days`가 정한다.

### 3.5 `backtest.py` 변경

- 옵션 `--session`, 선택지 `regular`(기본), `all`
- `regular`: `load_bars` 직후 각 종목 봉을 `time(9, 0) <= b.ts.time() < time(15, 30)`로 거르고, 남은 봉이 없는 종목은 제외. 전부 제외되면 `봉 데이터 없음` 경로(종료 코드 1)
- `backtest_runs.costs` JSON에 `"session": "regular"|"all"` 추가 (스키마 변경 없음)
- README에 `all` 사용 시 `--exit-at`을 확장 시간에 맞게 지정하라고 적는다

### 3.6 `.env.example`

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

사용자의 `trader/.env`에서 `KIS_*` 4줄을 지우고 `TOSS_BASE_URL`, `TOSS_RPS`를 추가한다(`TOSS_CLIENT_ID`·`TOSS_CLIENT_SECRET`은 이미 있음).

## 4. 오류 처리 요약

| 상황 | 처리 |
|---|---|
| 설정 누락, DB 접속 실패 | 실행 실패: 로그·텔레그램(예외 종류만), 종료 코드 1 |
| 토큰 발급 401 `invalid_client` / 403 `access_denied` | `TossAuthError` → 실행 실패 |
| 조회 중 토큰 만료·무효화 | 1회 재발급 후 재시도, 또 401이면 실행 실패 |
| 429 | `Retry-After` 대기 후 최대 3회 재시도 |
| 네트워크·5xx | 1·2·4초 재시도 후 해당 날짜 `error` |
| 그 밖 4xx(없는 종목 404 등) | 해당 날짜 `error`, 연속 5회면 종목 건너뜀 |
| 날짜 단위 오류 | 다음 실행에서 자동 재시도 (`error`는 `done_days`에 포함 안 됨) |
| 동시 실행 | 금지. 스케줄러 `MultipleInstances IgnoreNew`. 수동 실행은 스케줄 시각(20:30)을 피한다 |

## 5. 작업 스케줄러 (README에 기록)

- 트리거: 매주 월~금 20:30, 반복 없음
- 설정: `StartWhenAvailable`(놓친 작업 실행), `MultipleInstances IgnoreNew`, `ExecutionTimeLimit` 6시간
- 동작: `.venv\Scripts\python.exe collector.py`, 작업 폴더 `trader`
- 첫 백필은 스케줄러 등록 전에 수동 실행 권장 (종목당 약 4분, 50종목 3~4시간)
- 토스 WTS 설정 > Open API > 허용 IP 관리에 이 PC의 공인 IP 등록

## 6. 테스트

pytest, 네트워크 호출 없음. 토스 서버는 `send` 대역.

- `test_toss.py`
  - 토큰: form 본문 필드, 파일 캐시 재사용, 만료 5분 이내면 재발급, 401 `invalid_client`·403 → `TossAuthError`
  - `_get`: 인증 헤더, 401 `expired-token`이면 1회 재발급 후 재시도(두 번째 401이면 `TossAuthError`), 429 `Retry-After` 대기, 네트워크 오류 1·2·4초 재시도 후 `NETWORK`, 404 → `TossError`(code는 응답 `error.code` 값 그대로), 403 → `TossAuthError`, 예외 메시지에 client_id·secret·토큰 없음, 호출 간격 대기
  - `fetch_day`: 실측 형태 샘플 JSON(`fixtures/toss_candles.json`) 필드 변환(시각 −1분, Decimal, int)
  - `fetch_day`: 코드로 만든 가짜 서버가 끝나는 시각 표기 720봉(08:01~20:00)을 최신순 200개씩, `nextBefore`=마지막 봉 −1분으로 제공 → 720봉, 첫 봉 08:00·마지막 19:59, 오름차순, 요청 파라미터(`interval=1m`, `count=200`, `adjusted=false`, 첫 `before=YYYY-MM-DDT23:59:59+09:00`)
  - `fetch_day`: `nextBefore`가 마지막 봉과 같은 시각이어도 중복 없음
  - `fetch_day`: 전날 봉이 섞인 페이지에서 멈춤, 빈 날 `[]`(요청 1회), 10페이지 초과 `PAGINATION`
- `test_collector.py`
  - `target_days`: 평일만, 1461일 범위 경계, 20:09면 오늘 제외·20:10이면 포함, 주말 실행 시 오늘 제외, 최신순
  - `collect`: `ok`/`empty`/`error` 기록과 `source='toss'` 저장, 완료 날짜 건너뜀·`error` 날짜 재시도, 최신순 요청, 연속 5회 오류 시 종목 건너뜀(성공이 끼면 초기화), `TossAuthError`는 기록 없이 전파
  - `format_alert`: 첫 줄 형식, 20줄 제한과 `외 N건`
  - `main`: 설정 누락 → 1과 예외 종류만 로그, 실패 목록이 있을 때만 알림(대역 `send`)
  - `send_telegram` 기존 테스트 2개 유지
- `test_store.py`: `done_days`(ok·empty만, 다른 종목 제외), 기존 `done_symbols`·`failed_runs` 테스트 제거
- `test_backtest.py`: `--session regular`가 08:30·15:30·16:00 봉을 제외하고 09:00·15:29 봉은 남김, `costs->>'session'` 저장, `all`이면 전부 사용
- `test_engine.py`, `test_strategies.py`, `test_load_yahoo.py`: import 변경 후 기존 테스트 통과

### 수동 검증

1. 운영 DB에 `schema.sql` 적용(백테스터 2a에서 남긴 작업): `psql -h localhost -U trader -d trader -v ON_ERROR_STOP=1 -f schema.sql`
2. 토스 허용 IP 등록 확인, `.env` 갱신
3. `symbols.txt`를 005930, 000660으로 두고 `python collector.py` (약 8분)
4. `SELECT trade_date, status, bar_count FROM collect_runs WHERE symbol='005930' ORDER BY trade_date DESC LIMIT 10` — 최근 거래일 720, 주말 행 없음, 휴장일 `empty`
5. 임의 봉 3개를 토스 앱 1분 차트와 시가·고가·저가·종가·거래량 대조 (시각 1분 보정 확인)
6. 즉시 재실행 → 받을 날짜 0개(20:10 이후면 오늘 1개 이하)
7. `python backtest.py --strategy ma_cross,orb --source toss --from <1개월 전> --to <어제>` 결과 확인
8. 작업 스케줄러 등록, 다음 영업일 로그 확인

## 7. 완료 기준

- 6절 자동 테스트 전부 통과
- 수동 검증 1~8 완료
- 5영업일 연속 스케줄 실행에서 실패 알림 없음
