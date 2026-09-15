# 백테스터 설계 (하위 프로젝트 2a)

- 작성일: 2026-09-15
- 선행: `docs/superpowers/specs/2026-09-14-kis-minute-collector-design.md` (수집기, `trader/`)

## 1. 배경

한국 주식 분봉 단타 자동매매 시스템의 두 번째 하위 프로젝트. 수집기가 쌓는 1분봉으로 여러 전략을 골라 백테스트하고, 결과를 DB에 저장해 이후 UI(2b)에서 일별·시간대별로 조회한다.

KIS 키 발급 전이라 수집 데이터가 없으므로, 개발·검증용으로 Yahoo Finance 1분봉을 임시 적재한다.

### 확정된 결정

| 항목 | 결정 | 이유 |
|---|---|---|
| 종목 구조 | 종목별 독립 (현금 공유·동시 보유 제한 없음) | 단타 신호 검증에 충분, 구현 단순 |
| 초기 데이터 | Yahoo 1분봉을 `minute_bars`에 `source='yahoo'`로 적재 | 키 발급 전 실데이터로 개발. source로 KIS 데이터와 구분 |
| 체결 가정 | t봉 신호 → t+1봉 시가 체결, 비용 차감 | 미래 데이터 참조 방지 |
| 전략 | 여러 전략을 등록해두고 실행 시 선택 | 사용자 요구: 여러 기준을 비교·선택 |
| 엔진 | 순수 Python 봉 단위 루프 | 의존성 없음, 전략이 미래 봉을 볼 수 없는 구조, 상태 있는 청산 규칙 표현 쉬움 |
| 결과 | PostgreSQL `backtest_runs`·`backtest_trades` + 콘솔 요약 | UI(2b)에서 일별·시간대 조건으로 조회 |
| 범위 분할 | 2a 엔진+DB(이 문서) → 2b Next.js 조회 화면 | 스택이 다르고 독립 검증 가능 |

### Yahoo 1분봉 확인 결과 (2026-09-15 조회, 005930.KS)

- `https://query1.finance.yahoo.com/v8/finance/chart/005930.KS?interval=1m&range=8d` → 200, 최근 7거래일 + 오늘 장중
- 거래일당 360개, 09:00~14:59. **15:00~15:30 봉과 종가 단일가 봉이 없음**
- OHLC가 null인 봉 1개, 거래량 0인 봉 2개
- 비공식 API라 차단·형식 변경 가능

## 2. 범위

**포함**
- `minute_bars`에 `source` 컬럼 추가, 기존 테이블 이전
- `load_yahoo.py`: Yahoo 1분봉 일회성 적재
- `strategies.py`: 전략 인터페이스와 초기 전략 2개(`ma_cross`, `orb`)
- `engine.py`: 종목·날짜별 봉 루프, 체결·청산·비용 규칙
- `backtest.py`: CLI, 봉 조회, 결과 저장, 콘솔 요약
- `backtest_runs`·`backtest_trades` 테이블

**제외**
- 조회 UI (2b에서 설계)
- 공매도(매도 진입), 포트폴리오 자금 배분, 동시 보유 제한
- 파라미터 최적화(그리드 탐색), 봉 내부 지정가·손절가 체결
- 호가 단위 슬리피지, 수정주가, Yahoo 주기 실행
- pandas 등 신규 의존성 (수집기의 4개 의존성만 사용)

## 3. 구성 요소

```
trader/
├── schema.sql        # minute_bars source 이전 + 결과 테이블 추가
├── store.py          # save_bars(source 인자), load_bars, save_run 추가
├── load_yahoo.py     # Yahoo 1분봉 적재 CLI
├── strategies.py     # 전략 클래스와 STRATEGIES 등록 dict
├── engine.py         # 백테스트 규칙 (DB 모름)
├── backtest.py       # 실행 CLI
└── tests/
    ├── test_store.py        # source 구분·스키마 재적용·결과 저장 테스트 추가
    ├── test_load_yahoo.py
    ├── test_strategies.py
    ├── test_engine.py
    ├── test_backtest.py
    └── fixtures/yahoo_chart.json
```

모든 스크립트는 `trader/.env`의 `DATABASE_URL`을 쓴다. 시간대는 수집기와 같이 `kis.KST`(UTC+9 고정)를 쓴다.

### 3.1 스키마 변경 (`schema.sql`)

`minute_bars`:
- 신규 생성 시 `source text NOT NULL DEFAULT 'kis'` 포함, 기본키 `(source, symbol, ts)`
- 기존 테이블(수집기 버전)은 `schema.sql` 재적용 시 `DO` 블록으로 이전: `source` 컬럼이 없으면 `ADD COLUMN source text NOT NULL DEFAULT 'kis'` 후 기본키를 `(source, symbol, ts)`로 교체. 데이터 보존. 재적용을 여러 번 해도 결과가 같다.

결과 테이블:

```sql
CREATE TABLE IF NOT EXISTS backtest_runs (
    id          bigserial   PRIMARY KEY,
    created_at  timestamptz NOT NULL DEFAULT now(),
    strategy    text        NOT NULL,
    params      jsonb       NOT NULL,   -- 기본값에 --param을 덮어쓴 최종값
    source      text        NOT NULL,
    symbols     text[]      NOT NULL,   -- 실제로 봉이 있어 백테스트한 종목
    date_from   date        NOT NULL,
    date_to     date        NOT NULL,
    costs       jsonb       NOT NULL    -- {"fee":..., "tax":..., "slippage":..., "exit_at":"15:15"}
);

CREATE TABLE IF NOT EXISTS backtest_trades (
    run_id       bigint      NOT NULL REFERENCES backtest_runs(id) ON DELETE CASCADE,
    symbol       text        NOT NULL,
    entry_ts     timestamptz NOT NULL,  -- 체결 봉 시각
    entry_price  numeric     NOT NULL,  -- 슬리피지 반영 매수가
    exit_ts      timestamptz NOT NULL,
    exit_price   numeric     NOT NULL,  -- 슬리피지 반영 매도가
    return_pct   numeric     NOT NULL,  -- 비용 반영 수익률(%)
    exit_reason  text        NOT NULL CHECK (exit_reason IN ('signal', 'close_time', 'day_end')),
    PRIMARY KEY (run_id, symbol, entry_ts)
);
```

요약 지표는 저장하지 않는다(거래 내역에서 계산 가능, 중복 방지).

### 3.2 `store.py` 추가·변경

- `save_bars(conn, symbol, bars, source="kis")` — 기존 호출(수집기)은 그대로 동작
- `load_bars(conn, source, date_from, date_to, symbols=None) -> dict[str, list[Bar]]` — 종목별 시각 오름차순. `symbols`가 없으면 해당 source·기간에 봉이 있는 전 종목. 날짜 경계는 KST 기준
- `save_run(conn, run: dict, trades: list[Trade]) -> int` — 한 트랜잭션으로 run·trades 저장, run id 반환

### 3.3 `load_yahoo.py`

- 실행: `python load_yahoo.py [종목코드 ...]` — 인자 없으면 `symbols.txt` (`collector.read_symbols` 재사용)
- 종목마다 `{code}.KS` 요청, 응답 `chart.error`가 있거나 HTTP 오류면 `{code}.KQ`로 1회 재시도
- 요청: `GET https://query1.finance.yahoo.com/v8/finance/chart/{ticker}`, `params={"interval": "1m", "range": "8d"}`, `User-Agent` 헤더, timeout 10초
- 변환: `timestamp`(UTC epoch, 봉 시작 시각) → KST `datetime`, `indicators.quote[0]`의 open/high/low/close/volume → `Bar`(가격은 `Decimal(str(값))`, 거래량 null은 0)
- 제외: OHLC 중 하나라도 null인 봉, KST 날짜가 실행일(오늘)인 봉
- 저장: `store.save_bars(conn, code, bars, source="yahoo")` (이미 있는 봉은 건너뜀)
- 출력: 종목별 `005930 yahoo(.KS) 2520봉` 형태 한 줄. 실패 종목은 `000660 실패: <예외 종류>`
- 종료 코드: 전 종목 성공 0, 하나라도 실패 1
- HTTP 함수는 `send` 인자로 주입(기본 `requests.get`)해 테스트에서 대역 사용

### 3.4 `strategies.py`

```python
class Strategy:
    name: str
    defaults: dict          # 파라미터 기본값
    def __init__(self, params: dict): ...
    def on_bar(self, bar: Bar, entry_price: Decimal | None) -> str | None:
        """봉 하나를 받아 "buy" / "sell" / None을 반환한다. entry_price는 보유 중일 때 체결 시가(슬리피지 전), 미보유면 None."""

STRATEGIES = {"ma_cross": MaCross, "orb": Orb}
```

- 엔진이 종목·날짜마다 새 인스턴스를 만들고 봉을 시각순으로 하나씩 넘긴다. 전략은 받은 봉만 알고, 지표는 인스턴스 안에 누적한다.
- 새 전략 추가 = 클래스 1개 + `STRATEGIES`에 한 줄

**`ma_cross`** — `defaults = {"short": 5, "long": 20}`
- 종가 단순이동평균. 봉이 `long`개 쌓이기 전에는 신호 없음
- 직전 봉에서 `short평균 <= long평균`이고 이번 봉에서 `short평균 > long평균`이면 `"buy"`
- 직전 봉에서 `short평균 >= long평균`이고 이번 봉에서 `short평균 < long평균`이면 `"sell"`

**`orb`** — `defaults = {"range_end": "09:30", "stop_pct": -1.0, "target_pct": 2.0}`
- 시각이 `range_end` 이전인 봉들의 고가 최댓값을 범위 고가로 누적
- 시각이 `range_end` 이상인 봉에서, 미보유이고 오늘 아직 매수 신호를 낸 적 없고 종가 > 범위 고가이면 `"buy"` (하루 1회)
- 보유 중이면 `(종가 / entry_price - 1) * 100`이 `stop_pct` 이하이거나 `target_pct` 이상일 때 `"sell"`
- `range_end` 이전 봉이 없는 날은 신호 없음

파라미터 값의 형태는 기본값과 같게 변환한다(int, float, "HH:MM" 문자열).

### 3.5 `engine.py`

- `run_day(strategy, bars: list[Bar], costs: Costs) -> list[Trade]` — 한 종목 하루 봉(시각 오름차순)
- `run(strategy_cls, params, bars_by_symbol, costs) -> list[Trade]` — 종목별·KST 날짜별로 나눠 `run_day` 호출
- `Costs(fee: Decimal, tax: Decimal, slippage: Decimal, exit_at: time)`
- `Trade(symbol, entry_ts, entry_price, exit_ts, exit_price, return_pct, exit_reason)`

체결 규칙 (봉 인덱스 t):
1. 매수만. 종목당 동시에 1포지션.
2. t봉 `on_bar` 결과가 `"buy"`(미보유)/`"sell"`(보유)이면 t+1봉 시가에 체결. 보유 중 `"buy"`, 미보유 중 `"sell"`은 무시. 마지막 봉의 신호는 버린다.
3. 체결 봉 시각이 `exit_at` 이상이면 매수하지 않는다.
4. 보유 중에 시각이 `exit_at` 이상인 첫 봉이 오면, 그 봉 시가에 청산(`close_time`). 이 청산은 같은 봉의 신호 체결보다 우선한다.
5. 날의 마지막 봉까지 보유 중이면 마지막 봉 종가에 청산(`day_end`). Yahoo 데이터(14:59 종료)는 이 경로로 청산된다.
6. 가격·수익률:
   - 매수가 = 시가 × (1 + slippage)
   - 매도가 = 청산 기준가(시가 또는 종가) × (1 − slippage)
   - `return_pct = (매도가 × (1 − fee − tax) / (매수가 × (1 + fee)) − 1) × 100`
7. 전략에 넘기는 `entry_price`는 체결 시가(슬리피지 전). 체결은 t+1봉에서 일어나므로, 전략은 t+1봉의 `on_bar` 호출부터 `entry_price`를 받는다.
8. 금액·수량은 다루지 않는다.

기본 비용: `fee=0.00015`(0.015%), `tax=0.002`(0.20%, **실제 매도 거래세율 확인 필요**), `slippage=0.0005`(0.05%), `exit_at=15:15`.

### 3.6 `backtest.py`

```
python backtest.py --strategy ma_cross,orb --source yahoo --from 2026-09-04 --to 2026-09-14
                   [--symbols 005930,000660] [--param short=3 --param long=10]
                   [--fee 0.00015] [--tax 0.002] [--slippage 0.0005] [--exit-at 15:15]
```

1. 인자 검증 (DB 접속 전): 등록되지 않은 전략, `--from > --to`, `key=value` 형식 오류, 선택한 **모든** 전략에 없는 `--param` 키 → 오류 메시지 후 종료 코드 2. `--param` 키는 그 키를 가진 전략에만 적용한다.
2. `store.load_bars`로 봉 조회. 봉이 하나도 없으면 `봉 데이터 없음` 출력, 종료 코드 1, 저장 없음.
3. 전략마다 `engine.run` → `store.save_run` (전략당 run 1건).
4. 실행별 콘솔 요약 한 블록:
   - run id, 전략, 파라미터, 종목 수, 기간
   - 거래 수, 승률(`return_pct > 0` 비율), 평균 수익률, 수익률 합
   - 최대 낙폭: 청산 시각순 `return_pct` 누적합의 (고점 − 이후 저점), %p
   - 평균 보유 분
   - 거래 0건이어도 run은 저장하고 요약에 `거래 0건`
5. 종료 코드 0.

## 4. 오류 처리

| 상황 | 처리 |
|---|---|
| 잘못된 CLI 인자 | DB 작업 전 오류 메시지, 종료 코드 2 |
| `DATABASE_URL` 없음, DB 접속 실패 | 예외 종류만 출력 (메시지에 접속 문자열·비밀번호가 섞일 수 있어 출력하지 않음), 종료 코드 1 (URL·비밀번호는 출력하지 않음) |
| 조건에 봉 없음 | 종료 코드 1, 저장 없음 |
| `save_run` 중 오류 | 트랜잭션 롤백(반쪽 run 없음), 종료 코드 1 |
| Yahoo 종목 실패 (.KS·.KQ 모두, 네트워크, 형식) | 해당 종목만 실패 출력, 다음 종목 계속, 끝에 종료 코드 1 |

## 5. 2b(조회 UI)를 위한 조회 예시

일별·시간대별 집계는 `backtest_trades`에서 바로 계산한다.

```sql
SELECT (entry_ts AT TIME ZONE 'Asia/Seoul')::date AS day,
       CASE WHEN (entry_ts AT TIME ZONE 'Asia/Seoul')::time < '12:00' THEN '09-12' ELSE '12-15' END AS slot,
       count(*) AS trades, avg(return_pct) AS avg_ret, sum(return_pct) AS sum_ret
FROM backtest_trades
WHERE run_id = $1
GROUP BY 1, 2
ORDER BY 1, 2;
```

## 6. 테스트

pytest, 기존 `conn` 픽스처 재사용. 네트워크 호출 없음.

- `test_engine.py` (DB 없음, 코드로 만든 봉과 고정 신호를 내는 테스트용 전략)
  - 신호 다음 봉 시가 체결, 슬리피지 반영가
  - 마지막 봉 신호 무시
  - 보유 중 buy·미보유 중 sell 무시
  - `exit_at` 이상 봉에서 매수 금지, 보유 시 그 봉 시가 `close_time` 청산 (같은 봉 신호보다 우선)
  - 14:59에 끝나는 날의 `day_end` 종가 청산
  - `return_pct` 공식 값
  - 여러 날짜가 날짜별로 분리되어 전날 포지션이 이어지지 않음
  - `entry_price`가 체결 다음 `on_bar`부터 전달됨
- `test_strategies.py` (DB 없음)
  - `ma_cross`: `long`개 전 신호 없음, 상향·하향 교차 봉에서만 신호
  - `orb`: `range_end` 전 신호 없음, 돌파 시 buy 1회만, stop·target 도달 시 sell
- `test_store.py` 추가
  - 수집기 버전 `minute_bars`(source 없음)에 데이터가 있을 때 `schema.sql` 재적용 → `source='kis'`로 보존, 두 번 적용해도 오류 없음
  - 같은 symbol·ts라도 source가 다르면 별도 저장
  - `load_bars` source·기간·종목 필터와 KST 날짜 경계
  - `save_run` 저장·반환 id, trades 중 오류 시 run도 남지 않음
- `test_load_yahoo.py`
  - 샘플 JSON(`fixtures/yahoo_chart.json`, 실제 응답 형태 축약) 변환: KST 시각, Decimal, null OHLC 제외, 오늘 봉 제외
  - `.KS` 오류 응답 → `.KQ` 재시도
  - `source='yahoo'` 저장, 한 종목 실패 시 다음 종목 계속·종료 코드 1
- `test_backtest.py` (DB 사용)
  - 봉 적재 후 `main([...])` → run·trades 저장, 전략 2개면 run 2건
  - `--source` 필터(다른 source 봉 무시)
  - 봉 없음 → 1, 잘못된 전략·파라미터·기간 → 2 (DB에 run 없음)

### 수동 검증

1. `python load_yahoo.py 005930 000660` 실행, `SELECT source, symbol, count(*) FROM minute_bars GROUP BY 1, 2`로 적재 확인
2. `python backtest.py --strategy ma_cross,orb --source yahoo --from <7일 전> --to <어제>`
3. 거래 3건을 골라 `minute_bars`의 해당 봉과 대조해 진입·청산 시각, 가격, 수익률 계산 확인
4. 5절 조회 쿼리로 일별·시간대 집계가 나오는지 확인

## 7. 완료 기준

- 6절 자동 테스트 전부 통과 (기존 수집기 테스트 포함)
- 수동 검증 1~4 완료
- 수집기 `collector.py` 동작 변화 없음 (기존 테스트 통과로 확인)
