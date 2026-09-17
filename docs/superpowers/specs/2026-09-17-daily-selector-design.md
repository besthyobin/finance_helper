# 매일 종목 선정 설계

- 작성일: 2026-09-17
- 관련: `docs/superpowers/specs/2026-09-17-paper-trader-design.md` (모의투자, 8절 후속 방향), `docs/superpowers/specs/2026-09-15-toss-collector-design.md` (수집기)
- 로드맵: 이 문서는 분석 확장 4단계 중 1단계다. 2 재무·밸류에이션(DART), 3 뉴스, 4 해외 시장 브리핑(미국·일본)은 각각 별도 스펙으로 설계한다.

## 1. 배경

모의투자(`paper.py`)는 `paper_symbols.txt`의 고정 종목(005930, 000660)으로 돈다. SK하이닉스는 종목당 금액(100만 원)으로 1주도 살 수 없고, 실투자 예정 금액(10만 원)과 정수 주 주문 조건에 맞는 종목이 필요하다. 매일 장 시작 전에 토스 데이터만으로 종목을 스스로 고르고, 결과를 메일로 받는다.

### 실측 결과 (2026-09-17, 사용자 키로 조회 API만 호출)

- `GET /api/v1/rankings?type=MARKET_TRADING_AMOUNT&marketCountry=KR&duration=1y&excludeInvestmentCaution=true&count=100`: 100건, 항목은 `rank`, `symbol`, `price.lastPrice`, `tradingAmount` 등. 종목명 없음
- 순위 100건 중 ETF 21건(KODEX 레버리지 122630, 인버스2X 252670 등), 우선주 포함(005935). 주가 중간값 약 120,900원
- `GET /api/v1/stocks?symbols=...`: `name`, `securityType`(STOCK/ETF/...), `isCommonShare`, `status`, `koreanMarketDetail.krxTradingSuspended`
- `GET /api/v1/candles?interval=1d&count=...&adjusted=true`: 일봉 `timestamp`는 그날 00:00 KST(`2026-09-17T00:00:00.000+09:00`). 최대 200개
- `GET /api/v1/stocks/{symbol}/warnings`: 활성 항목 배열. `warningType`(LIQUIDATION_TRADING, OVERHEATED, INVESTMENT_WARNING, INVESTMENT_RISK, VI_*, STOCK_WARRANTS), `startDate`, `endDate`
- `GET /api/v1/stocks/{symbol}/short-selling?count=`: `records[]` 최신순, `shortSellingAmountRate`(비율, 삼성전자 9/17 `0.0512`). 당일 저녁 확정
- `GET /api/v1/stocks/{symbol}/credit-trades?count=`: `records[].marginLoan.balanceRate`(삼성전자 9/16 `0.0038`). T+1 새벽 반영
- `GET /api/v1/stocks/{symbol}/investor-trading?count=`: `records[].foreigner.netBuyVolume`, `institution.netBuyVolume`(주 수)
- 재무제표·밸류에이션·뉴스·일본 시장 API는 없음 (2~4단계에서 외부 출처 사용)

### 확정된 결정

| 항목 | 결정 | 이유 |
|---|---|---|
| 실행 | 별도 `selector.py`, 작업 스케줄러 평일 07:30 | 사용자 선택(아침 선정). 모의투자·수집기와 책임 분리, 실패해도 전날 종목으로 모의투자 진행 |
| 마감 | 08:45까지 끝내지 못하면 중단하고 전날 종목 유지 | 08:55 모의투자 시작 전 종료, 토스 토큰 충돌 방지 |
| 후보 | 거래대금 1년 상위 100(투자 유의 제외) 중 보통주(`securityType=STOCK`, `isCommonShare`), `status=ACTIVE`, 전일 종가 100,000원 이하 | 실투자 10만 원·정수 주 주문 가능 |
| 선정 방식 | 위험 필터 → 두 구간 백테스트 → 확인 구간 평균 수익률 순 최대 5개 | 사용자 선택. 전략(orb)에 맞는 종목을 설명 가능한 규칙으로 고름 |
| 선정 없음 | `paper_symbols.txt`를 바꾸지 않음(전날 종목 유지) | 모의투자 중단 방지 |
| 알림 | 매 실행 메일 1통(`notify.send_mail`) | 사용자 요구 |
| 백테스트 조건 | orb 기본 파라미터, 정규장, 수수료 0.00015·세금 0.002·슬리피지 0.0005·청산 15:15 | 모의투자와 같은 조건 |

## 2. 범위

**포함**
- `toss.py`: 순위·종목 정보·경고·공매도·신용·투자자별 매매·일봉 조회
- `selection.py`: 후보 거르기, 위험 지표·필터, 기술지표, 두 구간 백테스트 평가, 순위, 메일 문구 (순수 계산)
- `selector.py`: 실행 흐름, 마감 처리, 파일·DB 저장, 메일
- `collector.collect`의 중단 콜백 `stop`
- `store.save_candidates`, `schema.sql`의 `selection_candidates`
- README 절, 작업 스케줄러 안내

**제외**
- 재무제표·밸류에이션·뉴스·해외 시장 (2~4단계)
- 전략 파라미터 최적화, 여러 전략 비교
- 화면(`web.py`)에 선정 결과 표시 (필요 시 후속)
- 기준값 설정 파일 (코드 상수로 둔다)
- 새 의존성

## 3. 구성 요소

```
trader/
├── toss.py          # 추가 메서드
├── selection.py     # 신규 (순수 계산)
├── selector.py      # 신규 (실행)
├── collector.py     # collect(..., stop=None)
├── store.py         # save_candidates
├── schema.sql       # selection_candidates
├── README.md
└── tests/
    ├── test_selection.py   # 신규
    ├── test_selector.py    # 신규
    ├── test_toss.py
    ├── test_store.py
    └── test_collector.py
```

### 3.1 `toss.py` 추가

모두 `_get`을 쓰고, 응답 형식이 다르면 `TossError("BAD_RESPONSE")`.

| 메서드 | 호출 | 반환 |
|---|---|---|
| `rankings()` | `GET /api/v1/rankings` `type=MARKET_TRADING_AMOUNT, marketCountry=KR, duration=1y, excludeInvestmentCaution=true, count=100` | `[{"rank": int, "symbol": str, "last_price": Decimal}]` 순위순 |
| `stocks(symbols)` | `GET /api/v1/stocks?symbols=a,b,...` (100개 이하) | `{symbol: {"name", "security_type", "common", "active", "suspended"}}` |
| `warnings(symbol)` | `GET /api/v1/stocks/{symbol}/warnings` | `[{"type": str, "start": date or None, "end": date or None}]` |
| `short_selling(symbol, count)` | `GET .../short-selling?count=` | `[{"date": date, "amount_rate": Decimal or None}]` 최신순 |
| `credit_trades(symbol, count)` | `GET .../credit-trades?count=` | `[{"date": date, "margin_balance_rate": Decimal or None}]` 최신순 |
| `investor_trading(symbol, count)` | `GET .../investor-trading?count=` | `[{"date": date, "foreigner": int, "institution": int}]` 순매수 주 수, 최신순 |
| `fetch_daily(symbol, count)` | `GET /api/v1/candles` `interval=1d, count, adjusted=true` | `[DailyBar(date, open, high, low, close, volume)]` 날짜 오름차순 |

`DailyBar`는 `bars.py`에 `@dataclass(frozen=True)`로 둔다(`date`, Decimal 가격, int 거래량).

### 3.2 `selection.py` (순수 계산, DB·API 없음)

**상수**
```python
MAX_PRICE = Decimal("100000")
MAX_SELECTED = 5
SHORT_RATE_MAX = Decimal("0.10")      # 최근 5일 평균 공매도 거래대금 비중
MARGIN_RATE_MAX = Decimal("0.08")     # 최신 신용융자 잔고율
VOLATILITY_MAX = Decimal("0.05")      # 최근 20일 일간 수익률 표준편차
TURNOVER_MIN = Decimal("10000000000") # 최근 20일 일평균 거래대금(종가×거래량) 100억 원
BLOCKING_WARNINGS = {"LIQUIDATION_TRADING", "OVERHEATED", "INVESTMENT_WARNING", "INVESTMENT_RISK"}
MIN_SELECT_TRADES = 20
```

**함수**
- `candidates(rankings, stocks)`: 보통주·활성·가격 조건을 통과한 `[{"rank", "symbol", "name", "last_price"}]`와 탈락 `[(symbol, 사유)]`를 반환한다. ETF 등은 "보통주 아님", 가격 초과는 "10만 원 초과", 정보 없음은 "종목 정보 없음"
- `technicals(daily)`: 어제까지 일봉(오름차순)으로 `{"close", "ma20", "ma60", "return_20d", "volatility_20d", "turnover_20d"}`. 일봉이 21개 미만이면 해당 값은 None
- `risk_reason(today, stock, warnings, short, credit, tech)`: 첫 번째로 걸린 제외 사유 문자열, 없으면 None. 검사 순서: 거래정지 → 경고(`start <= today <= end` 또는 `end is None`) → 공매도(최근 5개 기록 중 값이 있는 것의 평균) → 신용(최신 값) → 변동성 → 유동성(None이면 "일봉 부족")
- `evaluate(select_trades, confirm_trades)`: `{"select": {"trades", "avg"}, "confirm": {"trades", "avg"}}`와 탈락 사유(선정 구간 20건 미만 / 선정 구간 평균 0 이하 / 확인 구간 거래 없음 / 확인 구간 평균 0 이하) 또는 None
- `rank_selected(passed)`: 확인 구간 평균 내림차순(같으면 확인 구간 거래 수 내림차순, 다음 순위 오름차순)으로 최대 5개
- `format_mail(run_date, selected, summary, kept=None, reason=None)`: 첫 줄(제목)과 본문. 요약은 `후보 N → 위험 제외 M (사유별 개수) → 백테스트 제외 K → 통과 P`

### 3.3 `selector.py`

**설정:** `.env`의 `TOSS_*`, `DATABASE_URL`, `GMAIL_*`. 로그 `logs/selector-YYYY-MM-DD.log`. 인자 `--no-deadline`(첫 백필용).

**흐름** (`run(client, conn, now, sleep, deadline)`; 기준일 D = 오늘)
1. `market_hours(D)`가 None이면 "휴장" 로그, 종료 코드 0 (메일 없음)
2. `rankings()` → `stocks(순위 종목)` → `selection.candidates`
3. 후보마다 `warnings`, `short_selling(count=5)`, `credit_trades(count=1)`, `investor_trading(count=5)`, `fetch_daily(count=80)`을 조회해 D 이전 일봉으로 `technicals`, `risk_reason`. 한 종목 조회가 `TossError`(인증 제외)로 실패하면 사유 "조회 실패"
4. 위험을 통과한 종목을 `collector.collect(conn, client, symbols, days, stop=마감확인)`으로 백필. `days`는 D−365일부터 D−1일까지 평일 최신순
5. 종목마다 `store.load_bars(conn, "toss", D−365, D−1, [symbol])`를 정규장 봉만 남겨(`backtest.regular_session`) `engine.run(Orb, {}, ..., COSTS)`. 거래를 청산일 기준으로 선정 구간(D−365 ~ D−92)과 확인 구간(D−91 ~ D−1)으로 나눠 `selection.evaluate`
6. `rank_selected`로 선정. 선정이 1개 이상이면 `paper_symbols.txt`를 임시 파일(`paper_symbols.txt.tmp`)에 쓰고 `os.replace`
7. `store.save_candidates`로 그날 전 후보 저장(선정 없음이어도 저장)
8. 메일 발송, 종료 코드 0

**마감:** `deadline`(기본 D 08:45 KST)을 단계 3의 종목마다, 단계 4의 날짜마다(`stop`), 단계 5의 종목마다 확인한다. 넘으면 남은 단계를 멈추고, 파일은 바꾸지 않고, 그때까지의 후보 결과를 저장한 뒤 "시간 초과, 전날 종목 유지" 메일을 보내고 종료 코드 0. `--no-deadline`이면 마감이 없다.

**`paper_symbols.txt` 형식:** 첫 줄 주석 `# YYYY-MM-DD 종목 선정기 자동 생성`, 이후 `코드  # 종목명` 한 줄씩. 기존 `read_symbols`로 읽힌다.

### 3.4 `collector.collect` 변경

`collect(conn, client, symbols, days, stop=None)`. 날짜 요청 직전에 `stop and stop()`이 참이면 그 종목과 이후 종목을 멈추고 지금까지의 결과를 반환한다. `stop`이 None이면 기존 동작과 같다.

### 3.5 `schema.sql` 추가

```sql
-- 매일 종목 선정 결과. 같은 날 재실행하면 덮어쓴다
CREATE TABLE IF NOT EXISTS selection_candidates (
    run_date   date    NOT NULL,
    symbol     text    NOT NULL,
    name       text    NOT NULL,
    rank       int     NOT NULL,          -- 거래대금 1년 순위
    status     text    NOT NULL CHECK (status IN ('selected', 'passed', 'rejected')),
    reason     text,                      -- rejected일 때 제외 사유
    metrics    jsonb   NOT NULL,          -- 위험 지표, 기술지표, 두 구간 백테스트 수치
    PRIMARY KEY (run_date, symbol)
);
```

`store.save_candidates(conn, run_date, rows)`: 한 트랜잭션으로 그날 행을 지우고 다시 넣는다. `metrics`의 Decimal·날짜는 문자열로 저장한다.

### 3.6 메일 형식

```
[종목선정] 2026-09-18 선정 3종목
1. 012345 OO전자  확인구간 9건 평균 +0.42% | 선정구간 31건 +0.18% | 종가 45,300 MA20 위 MA60 위 20일 +6.1% 변동성 2.3% 외국인 5일 +120,000주 기관 5일 -3,000주
2. ...
후보 38 → 위험 제외 21 (공매도 4, 신용 2, 변동성 6, 유동성 7, 경고 2) → 백테스트 제외 14 → 통과 3
순위 후보 탈락 62 (보통주 아님 25, 10만 원 초과 37)
```

선정 없음: 제목 `[종목선정] 2026-09-18 선정 없음, 전날 종목 유지(005930, 000660)`. 시간 초과: 제목 `[종목선정] 2026-09-18 시간 초과, 전날 종목 유지(...)`. 실행 실패: `[종목선정] 2026-09-18 실행 실패: 예외종류`.

## 4. 오류 처리

| 상황 | 처리 |
|---|---|
| 필수 환경변수 없음, DB 접속 실패 | 로그(접속 문자열 없음), 실행 실패 메일, 종료 코드 1 |
| `TossAuthError` (어느 단계든) | 파일 유지, 실행 실패 메일, 종료 코드 1 |
| 순위·종목 정보 조회 실패 | 선정 불가, 실행 실패 메일, 종료 코드 1 |
| 후보 한 종목 위험 데이터 조회 실패 | 그 종목 `rejected` "조회 실패", 계속 |
| 백필 날짜 오류 | `collect_runs`에 error 기록(기존), 계속. 봉이 부족하면 백테스트 조건 미달 |
| 마감 도달 | 3.3 마감 처리 |
| 파일 쓰기 실패 | 실행 실패 메일, 종료 코드 1 (임시 파일 방식이라 기존 파일은 그대로) |
| 메일 실패 | `send_mail`이 로그만 남김 |

## 5. 작업 스케줄러 (README에 기록)

평일 07:30, `MultipleInstances IgnoreNew`, `StartWhenAvailable`, 실행 제한 2시간. 관리자 권한 없이 현재 사용자 작업으로 등록한다(모의투자·수집기와 같음). 첫 실행은 등록 전에 `.\.venv\Scripts\python selector.py --no-deadline`으로 수동 실행한다(후보 1년치 백필 1~2시간). 장중(08:55~15:31)과 수집기 시각(20:30)을 피한다.

## 6. 테스트

- `test_selection.py`: 후보 거르기(ETF·우선주·비활성·가격 초과·정보 없음), 위험 필터마다 경계값 위·아래, 활성·만료·종료일 없는 경고, 거래정지, 기술지표(알려진 수열), 일봉 부족, 평가 탈락 사유 4종, 순위 정렬과 5개 제한, 메일 3종 제목과 사유별 집계
- `test_selector.py` (가짜 클라이언트·시계, 테스트 DB): 휴장이면 조회·메일 없음. 정상 선정 시 파일 내용·DB 행·메일. 한 종목 조회 실패 시 그 종목만 제외. 선정 0개면 파일 유지·"선정 없음" 메일. 마감 도달 시 백필 중단·파일 유지·"시간 초과" 메일. 인증 실패 시 종료 코드 1·실패 메일. 같은 날 재실행 시 DB 덮어쓰기
- `test_toss.py`: 새 메서드별 요청 경로·파라미터, 응답 변환, 일봉 날짜, 잘못된 응답
- `test_store.py`: `save_candidates` 덮어쓰기와 `metrics` 저장
- `test_collector.py`: `stop`이 참이 되면 이후 날짜를 요청하지 않음

### 수동 검증

1. 운영 DB에 `schema.sql` 적용
2. `.\.venv\Scripts\python selector.py --no-deadline` → 메일, `paper_symbols.txt`, `SELECT status, count(*) FROM selection_candidates WHERE run_date = CURRENT_DATE GROUP BY 1`
3. 작업 스케줄러 평일 07:30 등록
4. 다음 영업일 07:30 자동 실행 → 08:45 전 메일 → 08:55 모의투자가 새 종목으로 시작(`logs/paper-*.log`, 화면)

## 7. 완료 기준

- 자동 테스트 전부 통과
- 수동 첫 실행에서 선정 메일 수신, 파일·DB 일치
- 자동 실행 1일이 08:45 전에 끝나고 모의투자가 새 종목으로 시작
