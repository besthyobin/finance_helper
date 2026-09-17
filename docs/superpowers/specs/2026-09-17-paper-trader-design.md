# 실시간 모의투자 설계

- 작성일: 2026-09-17
- 관련: `docs/superpowers/specs/2026-09-15-toss-collector-design.md` (토스 수집기), `docs/superpowers/specs/2026-09-15-backtester-design.md` (백테스터)
- 후속: 종목 자동 선정(아래 8절)은 별도 스펙으로 설계한다.

## 1. 배경

백테스터와 토스 1분봉 수집기가 운영 중이다(005930·000660, 2022-09-16~). 다음 단계로 장중 실시간 봉으로 전략을 돌려 가상 체결하고, 실시간 동작이 백테스트와 같은지 확인한다. 이후 토스 소액 실계좌 주문으로 확장할 수 있게 체결 지점을 한 곳에 둔다.

### 실측 결과 (2026-09-17 장중)

- `GET /api/v1/candles?symbol=005930&interval=1m&count=3` (`before` 없음)은 최신순이며 **맨 앞 봉은 진행 중인 봉**이다. 11:48:50 호출 시 끝나는 시각 11:49:00 봉이 포함됨
- **막 끝난 봉도 수 초간 갱신된다.** 11:49:00에 끝난 봉의 거래량이 11:48:50 호출 4,077 → 11:49:10 호출 4,491
- 9/16 비용 0 백테스트(2022-09~2026-09, 두 종목, 정규장): ma_cross 26,080건 평균 0.000%, orb 937건 평균 +0.168%. 기본 비용(건당 약 0.33%)을 넣으면 둘 다 평균 마이너스로 추정된다 → 이번 목표는 수익이 아니라 파이프라인 검증

### 공식 문서 확인 (`openapi.json`)

- 모의투자·샌드박스 환경 없음. 주문 API는 실계좌 전용
- 국내 주식 주문은 정수 수량만 가능(소수점은 미국 주식 시장가 매도만)
- `GET /api/v1/rankings`: 거래대금·거래량·등락률 순위만, 최대 100위. 시가총액 순위 없음
- `GET /api/v1/market-calendar/KR`: 전일·당일·익일 영업일의 `integrated.regularMarket.startTime/endTime` (KST). 당일이 휴장이면 `today.integrated`(또는 `regularMarket`)가 null이다
- 웹소켓 실시간 시세 없음

### 확정된 결정

| 항목 | 결정 | 이유 |
|---|---|---|
| 체결 | 내 PC에서 가상 체결, 이후 토스 소액 실계좌 주문으로 확장 | 토스에 모의계좌 없음 |
| 전략 | `orb` 기본 파라미터(09:30, -1%, +2%) | 하루 거래가 적어 검증이 쉬움 |
| 종목 | `paper_symbols.txt`, 처음엔 005930·000660 | 종목 선정(8절)은 후속 작업 |
| 금액 | `PAPER_CAPITAL`(모의 2,000,000원) ÷ 종목 수 = 종목당 고정 금액 | 잔고 관리 없이 백테스트와 1:1 비교. 실투자 시 100,000원 예정 |
| 수량 | 종목당 금액 ÷ 슬리피지 반영 매수가 **내림, 정수 주**. 0주면 거래 안 함 | 국내 주문은 정수만 가능 |
| 체결 규칙 | 백테스트와 동일: 신호 다음 봉 시가, 15:15 강제 청산, 슬리피지·수수료·세금 기본값 | 같은 `DayRunner` 코드 사용 |
| 세션 | 정규장 09:00~15:30 봉만 | 백테스트 기본값과 같음 |
| 실행 방식 | 장중 상주 프로세스, 매분 끝난 봉 조회 | 실시간 수신·지연 문제까지 검증 |
| 봉 조회 시각 | 매분 :15초 | 끝난 봉 갱신 대기. 운영 검증 1단계에서 조정 |
| 화면 | `trader` 안 로컬 웹(표준 라이브러리 HTTP 서버, `127.0.0.1:8765`) | 기존 Next.js 대시보드는 Vercel 배포라 로컬 DB에 접근 불가 |
| 알림 | 체결마다 텔레그램, 마감 후 일일 요약 | 기존 `notify.send_telegram` 재사용 |

## 2. 범위

**포함**
- `engine.py`: `run_day`의 봉 처리를 `DayRunner`로 분리(동작 불변)
- `toss.py`: `fetch_recent`, `market_hours` 추가
- `store.py`: 모의투자 상태·거래 저장/조회
- `paper.py`: 장중 모의투자 프로세스
- `web.py`, `web/index.html`, `web/app.js`, `web/style.css`: 로컬 화면
- `schema.sql`: `paper_status`, `paper_trades`
- `paper_symbols.txt`, `.env.example`·README 갱신

**제외**
- 실계좌 주문, 계좌·잔고 API
- 종목 자동 선정(8절), 여러 전략 동시 실행, 파라미터 조정
- 화면의 차트·설정 변경·외부 접속
- 실시간 봉 DB 저장(20:30 수집기가 확정값을 저장)
- 새 의존성 (`requests`, `psycopg[binary]`, `python-dotenv`, `pytest` 유지)

## 3. 구성 요소

```
trader/
├── engine.py          # 수정: DayRunner 분리
├── toss.py            # 추가: fetch_recent, market_hours
├── store.py           # 추가: save_paper_status, save_paper_trade, load_paper_status, load_paper_trades, load_paper_daily
├── paper.py           # 신규
├── web.py             # 신규
├── web/
│   ├── index.html     # 신규
│   ├── app.js         # 신규
│   └── style.css      # 신규
├── paper_symbols.txt  # 신규
├── schema.sql         # 추가
└── tests/
    ├── test_engine.py     # DayRunner 분할 입력 테스트 추가 (기존 테스트 수정 없음)
    ├── test_toss.py       # fetch_recent, market_hours 추가
    ├── test_store.py      # paper 함수 추가
    ├── test_paper.py      # 신규
    ├── test_web.py        # 신규
    └── fixtures/
        ├── toss_candles_live.json    # 신규: 진행 중인 봉 포함 샘플
        └── toss_calendar_*.json      # 신규: 영업일·휴장일·시간 변경일
```

### 3.1 `engine.py`

```python
class DayRunner:
    """한 종목 하루의 체결 규칙을 봉 하나씩 적용한다. run_day와 paper.py가 함께 쓴다."""
    def __init__(self, symbol, strategy, costs): ...
    def step(self, bar):
        """봉 하나를 처리하고 이 봉에서 일어난 이벤트 목록을 반환한다.
        이벤트: ("buy", bar) 진입 체결, ("sell", Trade) 청산 완결."""
    def finish(self):
        """데이터가 끝났을 때 보유 중이면 마지막 봉 종가로 day_end 청산 Trade를, 아니면 None을 반환한다."""
    holding  # 보유 중이면 진입 봉, 아니면 None
    done     # exit_at 이후 봉을 받아 하루가 끝났으면 True (이후 step은 빈 목록)
```

- `run_day`는 `DayRunner`로 봉을 넣고 `"sell"` 이벤트의 `Trade`와 `finish()` 결과를 모아 반환한다. 기존 `test_engine.py`가 수정 없이 통과해야 한다
- `_close`는 그대로 쓴다

### 3.2 `toss.py` 추가

**`fetch_recent(symbol, count)`**
1. `_get(CANDLES_PATH, {"symbol", "interval": "1m", "count", "adjusted": "false"})`
2. 각 캔들의 끝나는 시각이 클라이언트 시계(`self._now()`)보다 늦으면 버린다(진행 중인 봉)
3. `fetch_day`와 같은 변환(시각 −1분, `Decimal`, `int`)으로 `Bar` 목록을 시각 오름차순 반환
4. 변환 코드는 `fetch_day`와 공용 함수 `_to_bar(candle)`로 뽑는다

**`market_hours(day)`**
- `GET /api/v1/market-calendar/KR?date=YYYY-MM-DD`
- `result.today.date != day`이거나 `integrated` 또는 `integrated.regularMarket`이 null이면 `None`(휴장)
- 아니면 `(regularMarket.startTime, regularMarket.endTime)`을 KST `datetime` 튜플로 반환
- 형식이 다르면 `TossError("BAD_RESPONSE")`

### 3.3 `schema.sql` 추가

```sql
-- 종목별 현재 상태. paper.py가 매분 덮어쓴다
CREATE TABLE IF NOT EXISTS paper_status (
    symbol       text        PRIMARY KEY,
    trade_date   date        NOT NULL,
    last_bar_ts  timestamptz,           -- 마지막으로 처리한 봉 시작 시각
    last_close   numeric,
    qty          int         NOT NULL,  -- 0이면 미보유
    entry_ts     timestamptz,           -- 진입 체결 봉 시각
    entry_price  numeric,               -- 슬리피지 반영 매수가
    updated_at   timestamptz NOT NULL DEFAULT now()
);

-- 완결된 모의 거래. 재시작 시 같은 거래는 덮어쓴다
CREATE TABLE IF NOT EXISTS paper_trades (
    symbol       text        NOT NULL,
    strategy     text        NOT NULL,
    entry_ts     timestamptz NOT NULL,
    qty          int         NOT NULL,
    entry_price  numeric     NOT NULL,  -- 슬리피지 반영 매수가
    exit_ts      timestamptz NOT NULL,
    exit_price   numeric     NOT NULL,  -- 슬리피지 반영 매도가
    pnl_krw      numeric     NOT NULL,  -- qty × (exit_price × (1 − fee − tax) − entry_price × (1 + fee))
    return_pct   numeric     NOT NULL,  -- Trade.return_pct와 동일
    exit_reason  text        NOT NULL CHECK (exit_reason IN ('signal', 'close_time', 'day_end')),
    PRIMARY KEY (symbol, entry_ts)
);
```

`tests/conftest.py`의 `TRUNCATE`에 두 테이블을 추가한다.

### 3.4 `store.py` 추가

- `save_paper_status(conn, row)`: `symbol` 기준 upsert
- `save_paper_trade(conn, strategy, qty, trade, pnl_krw)` (종목은 `trade.symbol`): `(symbol, entry_ts)` 기준 upsert
- `load_paper_status(conn)`: 전체 행
- `load_paper_trades(conn, day)`: `exit_ts`의 KST 날짜가 `day`인 거래, `exit_ts` 오름차순
- `load_paper_daily(conn)`: KST 날짜별 거래 수·승 수·`pnl_krw` 합계, 최신순

### 3.5 `paper.py`

**설정:** `.env`의 `TOSS_*`, `DATABASE_URL`, `TELEGRAM_*`, `PAPER_CAPITAL`(필수). `paper_symbols.txt`는 `symbols.txt`와 같은 형식이다(`#` 주석). 비용은 `backtest.py` 기본값(fee 0.00015, tax 0.002, slippage 0.0005, exit_at 15:15). 종목당 금액 = `PAPER_CAPITAL ÷ 종목 수`. 로그는 `logs/paper-YYYY-MM-DD.log`.

**하루 흐름**
1. **시작 검사:** `market_hours(오늘)`이 `None`이면 "휴장" 로그 후 종료 코드 0. 정규장이 09:00~15:30이 아니면 텔레그램 "정규장 시간 변경일, 모의투자 안 함" 후 종료 코드 0
2. **준비:** 종목마다 `DayRunner(symbol, Orb({}), costs)`를 만든다
3. **따라잡기:** 종목마다 `fetch_day(오늘)` 중 `now` 이전에 끝난 정규장 봉을 `process(symbol, bars)`에 넣는다
4. **매분 루프:** 매분 :15초까지 대기 → 종목마다 `fetch_recent(symbol, 5)` → 정규장 봉 중 `last_bar_ts` 이후만 `process`. 새 봉 중 첫 봉이 `last_bar_ts + 1분`이 아니면(처리한 봉이 없으면 09:00이 아니면) 빈 분이 있는 것이므로 그 종목은 `fetch_day`로 따라잡는다. 봉 시각은 연속이라고 가정한다(토스는 거래 없는 분도 채움 봉을 준다). 다음 조회 시각이 15:31 이후면 루프를 끝내고 15:31까지 기다린다(마감 비교용 봉이 확정되도록). `done`인 종목은 봉을 받아도 체결 없이 상태만 갱신한다
5. **마감 비교:** 종목마다 `fetch_day(오늘)` 정규장 봉을 `engine.run_day(symbol, Orb({}), bars, costs)`로 돌려 실시간 `Trade` 목록과 `(entry_ts, entry_price, exit_ts, exit_price, exit_reason)`을 비교한다. 루프가 끝났는데 보유 중인 종목은 `runner.finish()`로 청산한다
6. **일일 요약:** 텔레그램으로 종목별·전체 거래 수, 승/패, `pnl_krw` 합계, 비교 결과("일치" 또는 종목별 차이)를 보낸다. 종료 코드 0

**`process(symbol, bars)`**
- 봉마다 `runner.step(bar)` 호출
- `("buy", bar)`: 매수가 = `bar.open × (1 + slippage)`, 수량 = `floor(종목당 금액 ÷ 매수가)`. 0이면 "금액 부족" 로그를 남기고 이 진입의 청산 이벤트도 저장하지 않는다(runner 상태는 백테스트와 같게 유지). 0보다 크면 수량을 기억하고 텔레그램 "매수"
- `("sell", trade)`: 기억한 수량이 0보다 크면 `pnl_krw` 계산 → `save_paper_trade` → 텔레그램 "매도"
- 봉 처리 후 `save_paper_status` 1회
- 실주문 확장 시 이 함수의 매수·매도 지점에서 주문을 호출한다

재시작하면 3단계가 같은 봉을 같은 순서로 넣으므로 상태가 복구되고, 거래는 upsert라 중복되지 않는다. 대신 텔레그램 매수·매도 알림은 따라잡기 중 다시 나갈 수 있다 → 따라잡기 중에는 체결 알림을 보내지 않고 "따라잡기 완료: 보유 N종목, 거래 M건" 1통만 보낸다.

**시계:** `paper.run(client, conn, symbols, capital, now=datetime.now, sleep=time.sleep)`로 주입해 테스트한다.

### 3.6 `web.py`와 `web/`

- `ThreadingHTTPServer(("127.0.0.1", 8765), Handler)`, `.env`의 `DATABASE_URL`, 요청마다 새 연결
- `GET /` → `web/index.html`, `GET /app.js`, `GET /style.css` → `web/`의 파일. 그 밖의 경로는 404
- `GET /api/status` → `paper_status` 행 + `eval_krw = (last_close × (1 − slippage) − entry_price) × qty`(보유 중일 때)
- `GET /api/trades?date=YYYY-MM-DD` → 그날 거래. 날짜가 없으면 오늘, 형식이 틀리면 400
- `GET /api/daily` → 일별 합계
- 숫자는 문자열로 직렬화(`Decimal` 정밀도 유지), 시각은 ISO 8601 KST
- `app.js`: 5초마다 세 API를 조회해 표 3개(현재 상태, 오늘 체결, 일별 손익)를 갱신. 조회 실패 시 상단에 "연결 끊김" 표시. `status.updated_at`이 2분 넘게 지나면 "모의투자 프로세스 멈춤" 표시(장중에만)

### 3.7 `.env.example`

```
PAPER_CAPITAL=2000000
```

## 4. 오류 처리

| 상황 | 처리 |
|---|---|
| 필수 환경변수·`paper_symbols.txt` 없음 | 오류 로그, 종료 코드 1 |
| 시작 시 DB 접속 실패 | 오류 로그(접속 문자열 출력 안 함), 종료 코드 1 |
| 한 분 조회 실패 (`TossError`, 네트워크) | 로그만 남기고 다음 분에 5개를 다시 받아 채움. 같은 종목 연속 5분 실패 시 텔레그램 1회 |
| 빈 분이 5분 넘게 생김 | `fetch_day`로 따라잡기 |
| `TossAuthError` | 텔레그램 후 종료 코드 1 |
| 루프 중 DB 오류 | 로그 후 다음 분 재시도. 상태는 runner 메모리에 있으므로 다음 저장에서 반영 |
| 텔레그램 실패 | 기존대로 로그만 |
| 프로세스 재시작 | 따라잡기로 복구, 거래 upsert |
| 동시 실행 | 작업 스케줄러 `MultipleInstances IgnoreNew` |

## 5. 작업 스케줄러 (README에 기록)

평일 08:55 시작, 실행 제한 8시간, `MultipleInstances IgnoreNew`, `StartWhenAvailable`. 등록 명령은 기존 수집기 명령과 같은 형식으로 README에 쓴다. `web.py`는 필요할 때 수동 실행한다.

## 6. 테스트

- `test_engine.py`: 기존 테스트 수정 없이 통과. 같은 봉을 한 번에 넣은 `run_day` 결과와 `DayRunner`에 하나씩 넣은 결과가 같음. `done` 이후 `step`은 빈 목록
- `test_toss.py`: `fetch_recent`가 진행 중인 봉 제외·시각 −1분·오름차순. `market_hours` 영업일·휴장일·시간 변경일·잘못된 형식
- `test_store.py`: `paper_status` upsert, `paper_trades` 같은 키 덮어쓰기, KST 날짜별 조회·합계
- `test_paper.py` (가짜 클라이언트·시계, 테스트 DB):
  - 휴장일·시간 변경일은 거래 없이 종료
  - 수량 내림, 0주면 거래 저장 안 함, `pnl_krw` 계산
  - 따라잡기 후 루프 진행 결과 = 같은 봉을 한 번에 넣은 `run_day` 결과
  - 이미 처리한 봉 재수신 무시, 한 분 실패 후 다음 분에 채움, 빈 분 5분 초과 시 `fetch_day` 호출
  - 재시작 시 거래 중복 없음, 따라잡기 중 체결 알림 없음
  - 마감 비교 일치·불일치 보고
  - `TossAuthError` 시 종료 코드 1
- `test_web.py`: 임시 포트 서버로 세 API의 JSON 형태, 잘못된 날짜 400, 정적 파일 제공, `web/` 밖 경로(`/../.env` 등) 404

### 수동 검증 (장중, 사용자 PC)

1. **봉 확정 대기 측정:** 장중 10분간 매분 :05, :15, :30초에 직전 봉을 조회해 값이 더 이상 바뀌지 않는 시점을 확인하고, 필요하면 조회 초를 조정한다
2. 운영 DB에 `schema.sql` 적용, `.env`에 `PAPER_CAPITAL=2000000` 추가
3. 장중 `python paper.py` 실행 → 따라잡기·매분 로그 확인, `python web.py` → `http://localhost:8765`가 매분 갱신되는지 확인
4. 한 번 종료 후 재시작 → 상태·거래 유지, 중복 없음
5. 15:31 마감 비교·텔레그램 요약 수신
6. 사용자 승인 후 작업 스케줄러 등록, 다음 영업일 자동 실행 확인

## 7. 완료 기준

- 자동 테스트 전부 통과
- 장중 하루 실행에서 마감 비교가 "일치"
- 화면에서 현재 상태·오늘 체결·일별 손익 확인
- 작업 스케줄러 자동 실행 1일 성공

## 8. 후속: 종목 자동 선정 (별도 스펙)

이번 범위가 아니다. 합의된 방향만 기록한다.

1. `rankings?type=MARKET_TRADING_AMOUNT&marketCountry=KR&duration=1y&excludeInvestmentCaution=true&count=100`
2. 주가 100,000원 이하(실투자 10만 원·정수 주 기준)
3. 후보 최근 1년 1분봉 백필
4. 앞 9개월 비용 반영 orb 백테스트: 거래 20건 이상·평균 수익률 플러스
5. 뒤 3개월 재확인에서 플러스인 종목 중 최대 5개 → `paper_symbols.txt`
6. 통과 종목이 없으면 기존 두 종목으로 파이프라인 검증만 계속
