# 일봉 스윙 전략 백테스트 설계

- 작성일: 2026-09-18
- 관련: `docs/superpowers/specs/2026-09-17-daily-selector-design.md` (매일 종목 선정), `docs/superpowers/specs/2026-09-15-backtester-design.md` (1분봉 백테스터)
- 이번 범위: 백테스트로 효과 확인까지. 효과가 확인되면 모의투자·종목 선정기 연동을 별도 스펙으로 설계한다.

## 1. 배경

매일 종목 선정기 첫 실행(2026-09-17)에서 선정 종목이 0개였다. 원인을 확인하려고 orb 파라미터 80개 조합(범위 종료 09:10~10:00, 손절 -0.5~-3%, 목표 1~5%)을 12종목·1년으로 돌렸고, 두 구간 모두 플러스인 조합은 없었다. 가장 좋은 조합(10:00, -3%, +5%)도 선정 구간 682건 평균 -0.065%, 확인 구간 263건 -0.707%였다.

같은 조합을 비용 0으로 돌리면 선정 구간 +0.266%, 확인 구간 -0.379%다. 거래 1건 비용 약 0.33%가 수익을 넘고, 당일 청산 전략은 거래 수가 많아 비용이 누적된다.

### 비용 확인 (2026년)

- 매도 세금 0.20%: 코스피 증권거래세 0.05% + 농어촌특별세 0.15%, 코스닥 증권거래세 0.20%
- 토스증권 수수료: KRX 0.015%, NXT 0.014% (2025-12-15 ~ 2026-06-30 이벤트 무료, 이후 정상)
- 결론: 기존 백테스트 기본값(수수료 0.00015, 세금 0.002, 슬리피지 0.0005)을 그대로 쓴다

### 실측 (2026-09-18)

- `GET /api/v1/candles?interval=1d&count=200&adjusted=true&before=...`는 `nextBefore`로 과거 페이지를 준다. 005930은 40페이지(8,000봉)에서 1995-01-07까지 이어졌다. 10년치는 약 13페이지다

### 확정된 결정

| 항목 | 결정 | 이유 |
|---|---|---|
| 전략 | 신고가 돌파 + 추세 필터 + 트레일링 청산(일봉) | 사용자 선택. 종목별 독립 판단, 규칙 단순, 거래 수 적음 |
| 범위 | 백테스트로 효과 확인까지 | 사용자 선택. 효과가 없으면 여기서 멈춤 |
| 구조 | 일봉 전용 새 모듈. 기존 1분봉 엔진·orb·모의투자는 그대로 | 운영 중인 자동 실행에 영향 없음 |
| 대상 | 오늘 기준 거래대금 1년 상위 100 중 보통주·상장 중(가격 제한 없음) | 효과 검증 단계. 생존 편향이 있음을 결과에 명시 |
| 기간 | 최근 10년. 분할일(기본 2023-09-18) 전은 개발 구간, 이후는 검증 구간 | 검증 구간은 파라미터 선택에 쓰지 않음 |
| 체결 | 그날 종가로 신호, 다음 날 시가 체결(수정주가) | 미래 정보 사용 방지 |
| 비용 | 수수료 0.00015(양쪽), 세금 0.002(매도), 슬리피지 0.0005(양쪽) | 모의투자와 같음 |
| 저장 | 일봉은 `daily_bars`, 백테스트 결과는 화면 출력만 | 결과 저장은 효과 확인 후 필요 시 |

## 2. 범위

**포함**
- `schema.sql`: `daily_bars`
- `store.py`: `save_daily_bars`, `load_daily_bars`
- `toss.py`: `fetch_daily_history(symbol, since)`
- `engine.py`: 비용 계산을 `make_trade`로 분리(동작 불변)
- `swing.py`: 일봉 엔진 `run_symbol`, 전략 `BreakoutTrend`
- `swing_backtest.py`: 일봉 수집(`--collect`), 개발 구간 조합표, 검증 구간 실행(`--validate`)
- README 절

**제외**
- 모의투자·종목 선정기 연동, 작업 스케줄러 등록
- 백테스트 결과 DB 저장, 화면 표시
- 포트폴리오 단위 자금 배분·동시 보유 제한
- 다른 스윙 전략(이동평균 교차, 모멘텀 순환매)
- 새 의존성

## 3. 구성 요소

```
trader/
├── schema.sql          # daily_bars 추가
├── store.py            # save_daily_bars, load_daily_bars
├── toss.py             # fetch_daily_history
├── engine.py           # make_trade 분리
├── swing.py            # 신규: run_symbol, BreakoutTrend
├── swing_backtest.py   # 신규: CLI
├── README.md
└── tests/
    ├── test_engine.py         # 기존 테스트 그대로 통과
    ├── test_toss.py           # fetch_daily_history
    ├── test_store.py          # daily_bars 저장·조회
    ├── test_swing.py          # 신규
    └── test_swing_backtest.py # 신규
```

### 3.1 `schema.sql`

```sql
-- 수정주가 일봉. 수집할 때마다 전체 기간을 다시 받아 덮어쓴다(분할·배당으로 과거 값이 바뀜)
CREATE TABLE IF NOT EXISTS daily_bars (
    symbol  text    NOT NULL,
    day     date    NOT NULL,
    open    numeric NOT NULL,
    high    numeric NOT NULL,
    low     numeric NOT NULL,
    close   numeric NOT NULL,
    volume  bigint  NOT NULL,
    PRIMARY KEY (symbol, day)
);
```

### 3.2 `store.py`

- `save_daily_bars(conn, symbol, bars)`: 한 트랜잭션으로 `(symbol, day)` 기준 upsert(값 갱신)
- `load_daily_bars(conn, symbols, date_from, date_to)`: `{symbol: [DailyBar 날짜 오름차순]}`, 양끝 포함

### 3.3 `toss.fetch_daily_history(symbol, since)`

1. `_get(CANDLES_PATH, {"symbol", "interval": "1d", "count": 200, "adjusted": "true"})` (첫 페이지는 `before` 없음)
2. 캔들을 `DailyBar`(KST 날짜)로 바꿔 날짜 기준 dict에 모은다(중복 제거)
3. 페이지에 `since`보다 이른 날짜가 있거나, 캔들이 비었거나, `nextBefore`가 없으면 멈춘다. 아니면 `before=nextBefore`로 반복
4. 최대 40페이지. 넘으면 `TossError("PAGINATION")`
5. `since` 이후 날짜만 날짜 오름차순으로 반환. 형식 오류는 `TossError("BAD_RESPONSE")`

### 3.4 `engine.make_trade`

```python
def make_trade(symbol, entry_ts, entry_open, exit_ts, exit_base, reason, costs):
    """진입 시가·청산 기준가에 슬리피지를, 수익률에 수수료·세금을 반영한 Trade를 만든다."""
```

기존 `_close(symbol, entry_bar, exit_ts, exit_base, reason, costs)`는 `make_trade(symbol, entry_bar.ts, entry_bar.open, ...)`를 부른다. 계산식은 그대로다.

### 3.5 `swing.py`

```python
class BreakoutTrend(Strategy):
    """종가가 직전 entry_days일 최고 종가를 넘고 trend_days일 이동평균 위면 매수, 직전 exit_days일 최저 종가 아래면 매도."""
    name = "breakout_trend"
    defaults = {"entry_days": 20, "trend_days": 50, "exit_days": 10}
```

- 최근 `max(entry_days, trend_days, exit_days) + 1`개 종가만 보관한다
- 미보유: 직전 `entry_days`개 종가(오늘 제외) 최고보다 오늘 종가가 크고, 오늘 포함 `trend_days`개 종가 평균보다 오늘 종가가 크면 `"buy"`
- 보유: 직전 `exit_days`개 종가(오늘 제외) 최저보다 오늘 종가가 작으면 `"sell"`
- 필요한 종가 수가 모자라면 `None`
- `0 < exit_days`, `0 < entry_days`, `0 < trend_days`가 아니면 `ValueError`

```python
def run_symbol(symbol, bars, strategy, costs):
    """일봉(날짜 오름차순)을 하루씩 전략에 넣어 신호 다음 날 시가에 체결한 거래 목록을 반환한다."""
```

- 전날 신호가 `"buy"`이고 미보유면 오늘 시가에 진입, `"sell"`이고 보유면 오늘 시가에 청산(`"signal"`)
- 체결 처리 후 오늘 봉을 `strategy.on_bar(bar, entry_open or None)`에 넣어 다음 날 신호를 받는다
- 마지막 날 보유 중이면 마지막 종가로 청산(`"day_end"`)
- `costs.exit_at`은 쓰지 않는다(일봉에는 장중 청산 시각이 없음)
- 거래 시각은 `datetime.combine(day, time(9, 0), KST)`(진입·청산 모두 시가 기준, `day_end`는 `time(15, 30)`)

### 3.6 `swing_backtest.py`

```
swing_backtest.py [--collect] [--split YYYY-MM-DD] [--years 10] [--validate E:T:X]
```

- **대상 종목:** `client.rankings()` + `client.stocks()`에서 `security_type == "STOCK"`, `common`, `active`. `--collect`일 때만 토스를 부르고, 그 외에는 `daily_bars`에 있는 종목을 쓴다
- **`--collect`:** 대상 종목마다 `fetch_daily_history(symbol, 오늘 − years년)` → `save_daily_bars`. 종목별 실패는 로그만 남기고 계속, 인증 오류는 종료 코드 1
- **기본 실행(개발 구간):** `load_daily_bars(전 종목, 오늘 − years년, split − 1일)`로 조합 8개(`entry_days` 20/55 × `trend_days` 50/100 × `exit_days` 10/20)를 돌려 표를 출력
- **`--validate E:T:X`:** 조합 하나만 검증 구간(`split` ~ 어제)에서 돌려 같은 형식으로 출력
- **지표(`summarize_swing`)**
  - 조합별: 거래 수, 승률, 평균 수익률, 평균 보유일(청산일 − 진입일), 종목당 누적 수익(종목별 거래 수익률 합의 평균), 종목당 단순 보유 수익(종목별 구간 첫 시가 대비 마지막 종가 수익률의 평균)
- 첫 줄에 대상 종목 수, 기간, 생존 편향 안내를 출력

## 4. 오류 처리

| 상황 | 처리 |
|---|---|
| 필수 환경변수·DB 접속 실패 | 오류 로그(접속 문자열 없음), 종료 코드 1 |
| `--collect` 종목 하나 실패(`TossError`) | 로그 후 다음 종목 |
| `TossAuthError` | 종료 코드 1 |
| 잘못된 인자(`--validate` 형식, 날짜) | 사용법 오류, 종료 코드 2 |
| 일봉이 없거나 모자란 종목 | 거래 없이 지나감(단순 보유 계산에서도 제외) |

## 5. 테스트

- `test_engine.py`: 기존 테스트 수정 없이 통과. `make_trade`가 `_close`와 같은 값을 내는지 1개
- `test_toss.py`: 여러 페이지 넘기기, `since`에서 멈춤, 중복 날짜 제거, 40페이지 초과, 잘못된 응답
- `test_store.py`: 저장 후 조회, 같은 날 재저장 시 값 갱신, 기간·종목 필터
- `test_swing.py`: 다음 날 시가 체결, 여러 날 보유, 보유 중 매수 무시, 마지막 날 청산, 비용 반영, 전략 매수(돌파+추세)·추세 미달 무시·청산·데이터 부족, 파라미터 검증
- `test_swing_backtest.py`: 인자 해석(`--validate` 형식 오류 2), 개발·검증 구간 분리, 조합표 값, 단순 보유 값, `--collect`(가짜 클라이언트, 종목 하나 실패 후 계속)

### 수동 검증

1. 운영 DB에 `schema.sql` 적용
2. `.\.venv\Scripts\python swing_backtest.py --collect` (장중 모의투자와 동시에 돌려도 토큰은 같은 캐시를 씀. 수집기 20:30은 피함)
3. `.\.venv\Scripts\python swing_backtest.py` → 개발 구간 조합표
4. 개발 구간에서 조합 하나를 골라 `--validate`로 검증 구간 1회 실행
5. 결과를 사용자에게 보고하고 모의투자 연동 여부를 정한다

## 6. 완료 기준

- 자동 테스트 전부 통과
- 실제 일봉 수집 후 개발 구간 조합표와 검증 구간 결과를 사용자에게 보고
