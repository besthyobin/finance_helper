# 매일 종목 선정 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 평일 07:30에 토스 데이터로 모의투자 종목을 최대 5개 골라 `paper_symbols.txt`를 갱신하고 결과를 메일로 보낸다.

**Architecture:** `toss.py`에 순위·종목·위험·일봉 조회를 더하고, 선정 규칙은 DB·API 없는 순수 모듈 `selection.py`에, 실행 흐름(조회 → 위험 필터 → 백필 → 두 구간 백테스트 → 저장 → 파일 → 메일, 08:45 마감)은 `selector.py`에 둔다. 백필은 기존 `collector.collect`를 중단 콜백과 함께 재사용하고, 백테스트는 `engine.run`과 모의투자 비용 `paper.COSTS`를 쓴다.

**Tech Stack:** Python 3.11, requests, psycopg 3, python-dotenv, pytest, PostgreSQL 15

**Spec:** `docs/superpowers/specs/2026-09-17-daily-selector-design.md`

## Global Constraints

- 작업 폴더는 `trader/`. 테스트: `.\.venv\Scripts\python -m pytest tests -v` (테스트 DB는 `.env`의 `TEST_DATABASE_URL`)
- 새 의존성 금지: `requests`, `psycopg[binary]`, `python-dotenv`, `pytest`만 사용
- 모든 함수·메서드에 무엇을 하는지 한국어 docstring (사용자 규칙)
- 로그·메일에 토큰·client secret·앱 비밀번호·접속 문자열을 남기지 않는다. 예외는 종류와 `TossError.code`만
- 모든 시각은 KST(`bars.KST`)
- 선정 기준 상수: `MAX_PRICE=100000`, `MAX_SELECTED=5`, `SHORT_RATE_MAX=0.10`, `MARGIN_RATE_MAX=0.08`, `VOLATILITY_MAX=0.05`, `TURNOVER_MIN=10000000000`, `MIN_SELECT_TRADES=20`, 막는 경고 `LIQUIDATION_TRADING, OVERHEATED, INVESTMENT_WARNING, INVESTMENT_RISK`
- 기간: 백필·백테스트 D−365일 ~ D−1일, 확인 구간은 청산일 D−91일 이후, 마감 08:45
- 백테스트 조건은 `paper.COSTS`(fee 0.00015, tax 0.002, slippage 0.0005, exit_at 15:15), `strategies.Orb({})`, 정규장(`backtest.regular_session`)
- 커밋 메시지는 한국어로 간결하게, 마지막 줄에 `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`
- 시작 기준선: 기존 테스트 130개 통과

---

### Task 1: 토스 순위·종목·위험·일봉 조회

**Files:**
- Modify: `trader/bars.py` (`DailyBar` 추가)
- Modify: `trader/toss.py` (상수, `_parsed`·`_date`·`_decimal`·`_int` 함수, 메서드 7개)
- Test: `trader/tests/test_toss.py` (끝에 추가)

**Interfaces:**
- Consumes: `TossClient._get(path, params) -> dict`, `CANDLES_PATH`, `TossError`
- Produces:
  - `bars.DailyBar(date, open, high, low, close, volume)` — `date: date`, 가격 `Decimal`, `volume: int`
  - `TossClient.rankings() -> [{"rank": int, "symbol": str, "last_price": Decimal}]`
  - `TossClient.stocks(symbols) -> {symbol: {"name": str, "security_type": str, "common": bool, "active": bool, "suspended": bool}}`
  - `TossClient.warnings(symbol) -> [{"type": str, "start": date|None, "end": date|None}]`
  - `TossClient.short_selling(symbol, count) -> [{"date": date, "amount_rate": Decimal|None}]` 최신순
  - `TossClient.credit_trades(symbol, count) -> [{"date": date, "margin_balance_rate": Decimal|None}]` 최신순
  - `TossClient.investor_trading(symbol, count) -> [{"date": date, "foreigner": int|None, "institution": int|None}]` 최신순
  - `TossClient.fetch_daily(symbol, count) -> [DailyBar]` 날짜 오름차순
  - 형식 오류는 모두 `TossError("BAD_RESPONSE")`

- [ ] **Step 1: 실패하는 테스트 작성**

`trader/tests/test_toss.py` 상단 import의 `from bars import KST, Bar`를 `from bars import KST, Bar, DailyBar`로 바꾸고, 끝에 추가:

```python
def serve(body):
    """모든 GET에 같은 본문을 주는 FakeToss를 만든다."""
    return FakeToss(on_get=lambda params: FakeResponse(body))


def test_rankings_requests_trading_amount_1y_top_100(tmp_path):
    """시장 거래대금 1년 상위 100(투자 유의 제외)을 요청해 순위·종목·전일 종가로 바꾼다."""
    fake = serve({"result": {"rankedAt": "2026-09-17T17:30:48+09:00", "rankings": [
        {"rank": 1, "symbol": "000660", "currency": "KRW",
         "price": {"lastPrice": "1756000", "basePrice": "1759000", "changeRate": "-0.0017"},
         "tradingVolume": "1", "tradingAmount": "2"},
        {"rank": 2, "symbol": "005930", "currency": "KRW",
         "price": {"lastPrice": "254000", "basePrice": "253500", "changeRate": "0.002"},
         "tradingVolume": "1", "tradingAmount": "2"},
    ]}})
    assert make_client(tmp_path, fake).rankings() == [
        {"rank": 1, "symbol": "000660", "last_price": Decimal("1756000")},
        {"rank": 2, "symbol": "005930", "last_price": Decimal("254000")},
    ]
    _, url, kwargs = fake.gets()[0]
    assert url == "https://toss.test/api/v1/rankings"
    assert kwargs["params"] == {"type": "MARKET_TRADING_AMOUNT", "marketCountry": "KR", "duration": "1y",
                                "excludeInvestmentCaution": "true", "count": 100}


def test_stocks_maps_type_common_active_suspended(tmp_path):
    """종목 정보를 이름·유형·보통주·활성·거래정지로 바꾸고, 거래정지 정보가 없으면 False로 본다."""
    fake = serve({"result": [
        {"symbol": "005930", "name": "삼성전자", "securityType": "STOCK", "isCommonShare": True, "status": "ACTIVE",
         "koreanMarketDetail": {"liquidationTrading": False, "krxTradingSuspended": False}},
        {"symbol": "122630", "name": "KODEX 레버리지", "securityType": "ETF", "isCommonShare": True,
         "status": "ACTIVE", "koreanMarketDetail": {"krxTradingSuspended": True}},
        {"symbol": "005935", "name": "삼성전자우", "securityType": "STOCK", "isCommonShare": False,
         "status": "DELISTED", "koreanMarketDetail": None},
    ]})
    assert make_client(tmp_path, fake).stocks(["005930", "122630", "005935"]) == {
        "005930": {"name": "삼성전자", "security_type": "STOCK", "common": True, "active": True, "suspended": False},
        "122630": {"name": "KODEX 레버리지", "security_type": "ETF", "common": True, "active": True, "suspended": True},
        "005935": {"name": "삼성전자우", "security_type": "STOCK", "common": False, "active": False, "suspended": False},
    }
    _, url, kwargs = fake.gets()[0]
    assert url == "https://toss.test/api/v1/stocks"
    assert kwargs["params"] == {"symbols": "005930,122630,005935"}


def test_warnings_parses_type_and_dates(tmp_path):
    """경고 유형과 시작·종료일을 date로 바꾸고, 없는 날짜는 None으로 둔다."""
    fake = serve({"result": [
        {"warningType": "OVERHEATED", "exchange": "KRX", "startDate": "2026-09-17", "endDate": "2026-09-19"},
        {"warningType": "VI_STATIC", "exchange": None, "startDate": None, "endDate": None},
    ]})
    assert make_client(tmp_path, fake).warnings("005930") == [
        {"type": "OVERHEATED", "start": date(2026, 9, 17), "end": date(2026, 9, 19)},
        {"type": "VI_STATIC", "start": None, "end": None},
    ]
    _, url, _ = fake.gets()[0]
    assert url == "https://toss.test/api/v1/stocks/005930/warnings"


def test_short_selling_parses_amount_rate(tmp_path):
    """공매도 기록의 날짜와 거래대금 비중을 바꾸고, 비중이 null이면 None으로 둔다."""
    fake = serve({"result": {"nextUntil": None, "records": [
        {"date": "2026-09-17", "shortSellingVolume": "1", "shortSellingAmount": "2",
         "shortSellingVolumeRate": "0.05121", "shortSellingAmountRate": "0.0512"},
        {"date": "2026-09-16", "shortSellingAmountRate": None},
    ]}})
    assert make_client(tmp_path, fake).short_selling("005930", 5) == [
        {"date": date(2026, 9, 17), "amount_rate": Decimal("0.0512")},
        {"date": date(2026, 9, 16), "amount_rate": None},
    ]
    _, url, kwargs = fake.gets()[0]
    assert (url, kwargs["params"]) == ("https://toss.test/api/v1/stocks/005930/short-selling", {"count": 5})


def test_credit_trades_parses_margin_balance_rate(tmp_path):
    """신용 기록의 융자 잔고율을 바꾸고, 융자 객체가 null이면 None으로 둔다."""
    fake = serve({"result": {"nextUntil": None, "records": [
        {"date": "2026-09-16", "marginLoan": {"balanceQuantity": "1", "balanceRate": "0.0038", "tradingRate": "0.08"},
         "stockLoan": None},
        {"date": "2026-09-15", "marginLoan": None, "stockLoan": {"balanceRate": "0"}},
    ]}})
    assert make_client(tmp_path, fake).credit_trades("005930", 1) == [
        {"date": date(2026, 9, 16), "margin_balance_rate": Decimal("0.0038")},
        {"date": date(2026, 9, 15), "margin_balance_rate": None},
    ]
    _, url, kwargs = fake.gets()[0]
    assert (url, kwargs["params"]) == ("https://toss.test/api/v1/stocks/005930/credit-trades", {"count": 1})


def test_investor_trading_parses_net_buy_volume(tmp_path):
    """외국인·기관 순매수 주 수를 int로 바꾸고, 분류가 null이면 None으로 둔다."""
    fake = serve({"result": {"nextUntil": None, "records": [
        {"date": "2026-09-17", "individual": {"netBuyVolume": "119683"},
         "foreigner": {"buyVolume": "1", "sellVolume": "2", "netBuyVolume": "-2217618"},
         "institution": {"netBuyVolume": "83183"}},
        {"date": "2026-09-16", "foreigner": None, "institution": None},
    ]}})
    assert make_client(tmp_path, fake).investor_trading("005930", 5) == [
        {"date": date(2026, 9, 17), "foreigner": -2217618, "institution": 83183},
        {"date": date(2026, 9, 16), "foreigner": None, "institution": None},
    ]
    _, url, kwargs = fake.gets()[0]
    assert (url, kwargs["params"]) == ("https://toss.test/api/v1/stocks/005930/investor-trading", {"count": 5})


def test_fetch_daily_returns_daily_bars_ascending(tmp_path):
    """수정주가 일봉을 요청해 KST 날짜 오름차순 DailyBar로 바꾼다."""
    fake = serve({"result": {"candles": [
        {"timestamp": "2026-09-17T00:00:00.000+09:00", "openPrice": "251500", "highPrice": "259000",
         "lowPrice": "251000", "closePrice": "254000", "volume": "15484762", "currency": "KRW"},
        {"timestamp": "2026-09-16T00:00:00.000+09:00", "openPrice": "250000", "highPrice": "254000",
         "lowPrice": "247500", "closePrice": "253500", "volume": "16705728", "currency": "KRW"},
    ], "nextBefore": None}})
    assert make_client(tmp_path, fake).fetch_daily("005930", 2) == [
        DailyBar(date(2026, 9, 16), Decimal("250000"), Decimal("254000"), Decimal("247500"), Decimal("253500"), 16705728),
        DailyBar(date(2026, 9, 17), Decimal("251500"), Decimal("259000"), Decimal("251000"), Decimal("254000"), 15484762),
    ]
    _, url, kwargs = fake.gets()[0]
    assert url == "https://toss.test/api/v1/candles"
    assert kwargs["params"] == {"symbol": "005930", "interval": "1d", "count": 2, "adjusted": "true"}


@pytest.mark.parametrize("call", [
    lambda c: c.rankings(),
    lambda c: c.stocks(["005930"]),
    lambda c: c.warnings("005930"),
    lambda c: c.short_selling("005930", 5),
    lambda c: c.credit_trades("005930", 1),
    lambda c: c.investor_trading("005930", 5),
    lambda c: c.fetch_daily("005930", 80),
])
def test_market_data_rejects_bad_body(tmp_path, call):
    """result가 없거나 형식이 다르면 BAD_RESPONSE 예외를 낸다."""
    client = make_client(tmp_path, serve({"result": {"records": [{"date": "not-a-date"}], "rankings": [{}]}}))
    with pytest.raises(TossError) as e:
        call(client)
    assert e.value.code == "BAD_RESPONSE"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests/test_toss.py -v`
Expected: 수집 단계 ERROR `ImportError: cannot import name 'DailyBar' from 'bars'`

- [ ] **Step 3: 구현**

`trader/bars.py`: import를 `from datetime import date, datetime, timedelta, timezone`으로 바꾸고 파일 끝에 추가:

```python
@dataclass(frozen=True)
class DailyBar:
    """일봉 한 개. date는 KST 거래일."""
    date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
```

모듈 docstring을 `"""봉 공용 타입: KST 시간대, 1분봉 Bar, 일봉 DailyBar."""`로 바꾼다.

`trader/toss.py`:
- 모듈 docstring을 `"""토스증권 Open API 클라이언트: 토큰 캐시, 호출 간격 제한, 재시도, 봉·장 운영 시간·순위·종목·위험 지표 조회."""`로 바꾼다
- `from bars import KST, Bar`를 `from bars import KST, Bar, DailyBar`로 바꾼다
- 상수에 추가 (`MARKET_CALENDAR_PATH` 아래):

```python
RANKINGS_PATH = "/api/v1/rankings"
STOCKS_PATH = "/api/v1/stocks"
```

- `_to_bar` 아래에 추가:

```python
def _parsed(convert, body):
    """convert(body)로 응답을 바꾸고, 형식 오류는 BAD_RESPONSE 예외로 바꾼다."""
    try:
        return convert(body)
    except (KeyError, TypeError, AttributeError, ValueError, ArithmeticError):
        raise TossError("BAD_RESPONSE") from None


def _date(text):
    """YYYY-MM-DD 문자열을 date로 바꾼다. None이면 None."""
    return date.fromisoformat(text) if text is not None else None


def _decimal(text):
    """숫자 문자열을 Decimal로 바꾼다. None이면 None."""
    return Decimal(text) if text is not None else None


def _int(text):
    """정수 문자열을 int로 바꾼다. None이면 None."""
    return int(Decimal(text)) if text is not None else None
```

- `TossClient` 끝(`market_hours` 아래)에 추가:

```python
    def rankings(self):
        """시장 거래대금 1년 상위 100(투자 유의 제외)을 순위순 [{rank, symbol, last_price}]로 반환한다."""
        body = self._get(RANKINGS_PATH, {"type": "MARKET_TRADING_AMOUNT", "marketCountry": "KR", "duration": "1y",
                                         "excludeInvestmentCaution": "true", "count": 100})
        return _parsed(lambda b: [{"rank": int(r["rank"]), "symbol": r["symbol"],
                                   "last_price": Decimal(r["price"]["lastPrice"])}
                                  for r in b["result"]["rankings"]], body)

    def stocks(self, symbols):
        """종목 기본 정보(100개 이하)를 {symbol: {name, security_type, common, active, suspended}}로 반환한다."""
        body = self._get(STOCKS_PATH, {"symbols": ",".join(symbols)})
        return _parsed(lambda b: {s["symbol"]: {
            "name": s["name"], "security_type": s["securityType"], "common": bool(s["isCommonShare"]),
            "active": s["status"] == "ACTIVE",
            "suspended": bool((s.get("koreanMarketDetail") or {}).get("krxTradingSuspended")),
        } for s in b["result"]}, body)

    def warnings(self, symbol):
        """종목의 활성 매수 유의사항을 [{type, start, end}]로 반환한다."""
        body = self._get(f"{STOCKS_PATH}/{symbol}/warnings", {})
        return _parsed(lambda b: [{"type": w["warningType"], "start": _date(w.get("startDate")),
                                   "end": _date(w.get("endDate"))} for w in b["result"]], body)

    def short_selling(self, symbol, count):
        """최근 count일 공매도 거래대금 비중을 최신순 [{date, amount_rate}]로 반환한다."""
        body = self._get(f"{STOCKS_PATH}/{symbol}/short-selling", {"count": count})
        return _parsed(lambda b: [{"date": date.fromisoformat(r["date"]),
                                   "amount_rate": _decimal(r.get("shortSellingAmountRate"))}
                                  for r in b["result"]["records"]], body)

    def credit_trades(self, symbol, count):
        """최근 count일 신용융자 잔고율을 최신순 [{date, margin_balance_rate}]로 반환한다."""
        body = self._get(f"{STOCKS_PATH}/{symbol}/credit-trades", {"count": count})
        return _parsed(lambda b: [{"date": date.fromisoformat(r["date"]),
                                   "margin_balance_rate": _decimal((r.get("marginLoan") or {}).get("balanceRate"))}
                                  for r in b["result"]["records"]], body)

    def investor_trading(self, symbol, count):
        """최근 count일 외국인·기관 순매수 주 수를 최신순 [{date, foreigner, institution}]로 반환한다."""
        body = self._get(f"{STOCKS_PATH}/{symbol}/investor-trading", {"count": count})
        return _parsed(lambda b: [{"date": date.fromisoformat(r["date"]),
                                   "foreigner": _int((r.get("foreigner") or {}).get("netBuyVolume")),
                                   "institution": _int((r.get("institution") or {}).get("netBuyVolume"))}
                                  for r in b["result"]["records"]], body)

    def fetch_daily(self, symbol, count):
        """수정주가 일봉 count개를 날짜 오름차순 DailyBar로 반환한다. 장중이면 오늘 진행 중인 일봉이 섞일 수 있다."""
        body = self._get(CANDLES_PATH, {"symbol": symbol, "interval": "1d", "count": count, "adjusted": "true"})
        return _parsed(lambda b: sorted((DailyBar(
            datetime.fromisoformat(c["timestamp"]).astimezone(KST).date(), Decimal(c["openPrice"]),
            Decimal(c["highPrice"]), Decimal(c["lowPrice"]), Decimal(c["closePrice"]), int(Decimal(c["volume"])),
        ) for c in b["result"]["candles"]), key=lambda d: d.date), body)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests -v`
Expected: 144 passed (130 + 14)

- [ ] **Step 5: 커밋**

```powershell
git add bars.py toss.py tests/test_toss.py
git commit -m @'
토스 순위·종목·위험 지표·일봉 조회 추가

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
'@
```

---

### Task 2: 수집 중단 콜백과 선정 결과 저장

**Files:**
- Modify: `trader/collector.py` (`collect`)
- Modify: `trader/schema.sql` (끝에 추가), `trader/store.py` (끝에 추가), `trader/tests/conftest.py` (TRUNCATE)
- Test: `trader/tests/test_collector.py`, `trader/tests/test_store.py` (끝에 추가)

**Interfaces:**
- Consumes: 기존 `collector.collect`, `store` 모듈
- Produces:
  - `collector.collect(conn, client, symbols, days, stop=None) -> (counts, failures)` — 날짜 요청 직전 `stop()`이 참이면 즉시 반환
  - `store.save_candidates(conn, run_date, rows) -> None` — rows: `[{"symbol", "name", "rank", "status", "reason", "metrics"}]`
  - `store.load_candidates(conn, run_date) -> [dict]` symbol 오름차순 (테스트·확인용)

- [ ] **Step 1: 실패하는 테스트 작성**

`trader/tests/test_collector.py`의 `test_collect_propagates_auth_error_without_record` 아래에 추가:

```python
def test_collect_stops_before_next_day_when_stop_returns_true(conn):
    """stop()이 참이 되면 다음 날짜와 남은 종목을 요청하지 않고 지금까지의 결과를 반환한다."""
    days = [DAY, PREV_DAY, date(2026, 9, 10)]
    client = FakeClient({(s, d): bars(d, 1) for s in ("A", "B") for d in days})
    counts, failures = collector.collect(conn, client, ["A", "B"], days, stop=lambda: len(client.fetched) >= 2)
    assert client.fetched == [("A", DAY), ("A", PREV_DAY)]
    assert counts == {"A": {"ok": 2, "empty": 0, "error": 0, "skipped": 1}}
    assert failures == []
```

`trader/tests/conftest.py`의 TRUNCATE 줄을 바꾼다:

```python
        c.execute("TRUNCATE minute_bars, collect_runs, backtest_runs, paper_status, paper_trades, "
                  "selection_candidates CASCADE")
```

`trader/tests/test_store.py` 끝에 추가:

```python
def candidate(symbol, status, reason=None, **metrics):
    """선정 결과 행 dict를 만든다."""
    return {"symbol": symbol, "name": f"종목{symbol}", "rank": 3, "status": status, "reason": reason,
            "metrics": metrics}


def test_save_candidates_replaces_same_day_and_keeps_other_days(conn):
    """같은 날 결과는 지우고 다시 쓰고, 다른 날 결과는 그대로 두며 Decimal·date는 문자열로 저장한다."""
    day, prev = date(2026, 9, 18), date(2026, 9, 17)
    store.save_candidates(conn, prev, [candidate("X", "selected")])
    store.save_candidates(conn, day, [candidate("A", "rejected", "공매도"), candidate("B", "passed")])
    store.save_candidates(conn, day, [candidate("A", "selected", close=Decimal("50000"), day=date(2026, 9, 17))])
    [row] = store.load_candidates(conn, day)
    assert (row["symbol"], row["name"], row["rank"], row["status"], row["reason"]) == ("A", "종목A", 3, "selected", None)
    assert row["metrics"] == {"close": "50000", "day": "2026-09-17"}
    assert [r["symbol"] for r in store.load_candidates(conn, prev)] == ["X"]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests/test_collector.py tests/test_store.py -v`
Expected: DB 테스트가 conftest TRUNCATE에서 ERROR (`relation "selection_candidates" does not exist`), `test_collect_stops...`는 `TypeError: collect() got an unexpected keyword argument 'stop'`

- [ ] **Step 3: 구현**

`trader/collector.py`의 `collect`를 바꾼다(시그니처, docstring, 날짜 반복 맨 앞 검사):

```python
def collect(conn, client, symbols, days, stop=None):
    """종목마다 완료되지 않은 날짜를 최신순으로 수집·기록하고 (종목별 상태 개수, 실패 목록)을 반환한다.
    stop이 주어지면 날짜를 요청하기 전마다 확인해 참이면 남은 날짜·종목을 멈추고 바로 반환한다."""
    counts, failures = {}, []
    for symbol in symbols:
        done = store.done_days(conn, symbol)
        todo = [d for d in days if d not in done]
        c = counts[symbol] = {"ok": 0, "empty": 0, "error": 0, "skipped": 0}
        streak = 0
        for i, day in enumerate(todo):
            if stop and stop():
                c["skipped"] = len(todo) - i
                log.warning("%s 중단 요청, 남은 %d일 건너뜀", symbol, c["skipped"])
                return counts, failures
            if streak >= MAX_CONSECUTIVE_ERRORS:
```

(`if streak >= ...` 이하 기존 코드는 그대로)

`trader/schema.sql` 끝에 추가:

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

`trader/store.py`: import에 `import json`, `from functools import partial`을 추가하고, 모듈 docstring을 `"""minute_bars·collect_runs·backtest·paper·selection 테이블 저장과 조회."""`로 바꾼 뒤 끝에 추가:

```python
_dumps = partial(json.dumps, default=str, ensure_ascii=False)


def save_candidates(conn, run_date, rows):
    """그날 종목 선정 결과를 한 트랜잭션으로 지우고 다시 저장한다. metrics의 Decimal·date는 문자열로 저장한다."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM selection_candidates WHERE run_date = %s", (run_date,))
        cur.executemany(
            "INSERT INTO selection_candidates (run_date, symbol, name, rank, status, reason, metrics) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [(run_date, r["symbol"], r["name"], r["rank"], r["status"], r["reason"], Jsonb(r["metrics"], dumps=_dumps))
             for r in rows],
        )


def load_candidates(conn, run_date):
    """그날 종목 선정 결과를 symbol 오름차순 dict 목록으로 반환한다."""
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "SELECT symbol, name, rank, status, reason, metrics FROM selection_candidates "
            "WHERE run_date = %s ORDER BY symbol", (run_date,),
        ).fetchall()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests -v`
Expected: 146 passed

- [ ] **Step 5: 커밋**

```powershell
git add collector.py schema.sql store.py tests/conftest.py tests/test_collector.py tests/test_store.py
git commit -m @'
수집 중단 콜백과 종목 선정 결과 테이블 추가

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
'@
```

---

### Task 3: 선정 규칙 `selection.py`

**Files:**
- Create: `trader/selection.py`
- Test: `trader/tests/test_selection.py`

**Interfaces:**
- Consumes: `bars.DailyBar`, `engine.Trade` (필드 `exit_ts`, `return_pct`)
- Produces (모두 순수 함수):
  - `candidates(rankings, stocks) -> (kept, dropped)` — kept `[{"rank", "symbol", "last_price", "name"}]` 순위순, dropped `[(symbol, 사유)]`
  - `technicals(daily) -> {"close", "ma20", "ma60", "return_20d", "volatility_20d", "turnover_20d"}` (Decimal 또는 None)
  - `short_average(short) -> Decimal|None` — 최근 5개 중 값 있는 것의 평균
  - `investor_sums(records) -> {"foreigner": int|None, "institution": int|None}` — 값 있는 것의 합, 모두 없으면 None
  - `risk_reason(today, stock, warnings, short, credit, tech) -> str|None`
  - `split_trades(trades, confirm_from) -> (select, confirm)`
  - `evaluate(select_trades, confirm_trades) -> (result, reason)` — result `{"select": {"trades", "avg"}, "confirm": {"trades", "avg"}}`
  - `rank_selected(passed) -> list` — passed 항목은 `"rank"`, `"backtest"`(evaluate result) 키를 가진 dict
  - `format_mail(run_date, selected, summary, kept=None, reason=None) -> str` — summary `{"ranked", "dropped", "candidates", "risk", "backtest_rejected", "passed"}`(dropped·risk는 사유→개수 dict), selected 항목은 `"symbol", "name", "tech", "investor", "backtest"` 키

- [ ] **Step 1: 실패하는 테스트 작성**

`trader/tests/test_selection.py`:

```python
import statistics
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

import engine
import selection
from bars import KST, DailyBar

TODAY = date(2026, 9, 18)


def info(security_type="STOCK", common=True, active=True, suspended=False, name="종목"):
    """종목 정보 dict를 만든다."""
    return {"name": name, "security_type": security_type, "common": common, "active": active, "suspended": suspended}


def test_candidates_keep_common_active_stocks_under_100k_in_rank_order():
    """보통주·활성·10만 원 이하만 순위순으로 남기고 나머지는 사유와 함께 뺀다."""
    rankings = [{"rank": i + 1, "symbol": s, "last_price": Decimal(p)} for i, (s, p) in enumerate([
        ("A", "100000"), ("B", "50000"), ("C", "30000"), ("D", "20000"), ("E", "100001"), ("F", "10000")])]
    stocks = {"A": info(name="에이"), "B": info("ETF"), "C": info(common=False), "D": info(active=False),
              "E": info()}
    kept, dropped = selection.candidates(rankings, stocks)
    assert kept == [{"rank": 1, "symbol": "A", "last_price": Decimal("100000"), "name": "에이"}]
    assert dropped == [("B", "보통주 아님"), ("C", "보통주 아님"), ("D", "상장 상태 아님"),
                       ("E", "10만 원 초과"), ("F", "종목 정보 없음")]


def daily_bars(closes, volume=1000):
    """종가 목록으로 TODAY 전날까지 이어지는 일봉을 만든다(시가=고가=저가=종가)."""
    start = TODAY - timedelta(days=len(closes))
    return [DailyBar(start + timedelta(days=i), Decimal(c), Decimal(c), Decimal(c), Decimal(c), volume)
            for i, c in enumerate(closes)]


def test_technicals_from_61_rising_closes():
    """61개 일봉(100~160)으로 이동평균·20일 수익률·변동성·일평균 거래대금을 계산한다."""
    tech = selection.technicals(daily_bars(range(100, 161)))
    assert tech["close"] == Decimal("160")
    assert tech["ma20"] == Decimal("150.5")
    assert tech["ma60"] == Decimal("130.5")
    assert tech["return_20d"] == Decimal(160) / Decimal(140) - 1
    rets = [(140 + k + 1) / (140 + k) - 1 for k in range(20)]
    assert float(tech["volatility_20d"]) == pytest.approx(statistics.stdev(rets))
    assert tech["turnover_20d"] == Decimal("150500")


def test_technicals_short_history_leaves_values_none():
    """일봉이 20개면 20일 이동평균만 있고 나머지는 None, 없으면 모두 None."""
    tech = selection.technicals(daily_bars([100] * 20))
    assert tech["ma20"] == Decimal("100")
    assert [tech[k] for k in ("ma60", "return_20d", "volatility_20d", "turnover_20d")] == [None] * 4
    assert set(selection.technicals([]).values()) == {None}


def test_short_average_and_investor_sums_skip_missing():
    """공매도 평균은 최근 5개 중 값 있는 것만, 순매수 합은 값 있는 것만 쓴다."""
    short = [{"amount_rate": Decimal(v) if v else None} for v in ("0.1", None, "0.2", "0.3", "0.4", "0.9")]
    assert selection.short_average(short) == Decimal("0.25")
    assert selection.short_average([{"amount_rate": None}]) is None
    records = [{"foreigner": 10, "institution": None}, {"foreigner": -3, "institution": None}]
    assert selection.investor_sums(records) == {"foreigner": 7, "institution": None}


SAFE_TECH = {"volatility_20d": Decimal("0.02"), "turnover_20d": Decimal("20000000000")}


def risk(stock=None, warnings=(), short=("0.05",), margin="0.01", tech=None):
    """기본은 안전한 입력으로 risk_reason을 호출한다."""
    return selection.risk_reason(
        TODAY, stock or info(), list(warnings),
        [{"amount_rate": Decimal(v) if v is not None else None} for v in short],
        [{"margin_balance_rate": Decimal(margin) if margin is not None else None}] if margin != "none" else [],
        {**SAFE_TECH, **(tech or {})})


def warning(kind, start, end):
    """경고 dict를 TODAY 기준 상대 일수로 만든다."""
    day = lambda d: None if d is None else TODAY + timedelta(days=d)
    return {"type": kind, "start": day(start), "end": day(end)}


@pytest.mark.parametrize("kwargs, expected", [
    ({}, None),
    ({"stock": info(suspended=True)}, "거래정지"),
    ({"warnings": [warning("OVERHEATED", -1, 1)]}, "경고"),
    ({"warnings": [warning("INVESTMENT_RISK", -5, None)]}, "경고"),
    ({"warnings": [warning("INVESTMENT_WARNING", -3, -1)]}, None),
    ({"warnings": [warning("VI_STATIC", -1, 1)]}, None),
    ({"short": ("0.10",)}, "공매도"),
    ({"short": ("0.0999",)}, None),
    ({"short": ("0.12", None)}, "공매도"),
    ({"short": (None,)}, None),
    ({"margin": "0.08"}, "신용"),
    ({"margin": "0.0799"}, None),
    ({"margin": None}, None),
    ({"margin": "none"}, None),
    ({"tech": {"volatility_20d": Decimal("0.05")}}, "변동성"),
    ({"tech": {"volatility_20d": Decimal("0.0499")}}, None),
    ({"tech": {"turnover_20d": Decimal("9999999999")}}, "유동성"),
    ({"tech": {"turnover_20d": Decimal("10000000000")}}, None),
    ({"tech": {"volatility_20d": None}}, "일봉 부족"),
])
def test_risk_reason(kwargs, expected):
    """거래정지·경고·공매도·신용·변동성·유동성 기준의 경계에서 첫 제외 사유를 돌려준다."""
    assert risk(**kwargs) == expected


def trade(exit_day, ret):
    """exit_day 10:01에 청산하고 수익률 ret(%)인 거래."""
    ts = datetime.combine(exit_day, datetime.min.time(), KST).replace(hour=10, minute=1)
    return engine.Trade("A", ts - timedelta(minutes=20), Decimal("100"), ts, Decimal("101"), Decimal(ret), "signal")


def test_split_trades_by_exit_date():
    """청산일이 confirm_from 이전이면 선정 구간, 같거나 이후면 확인 구간이다."""
    confirm_from = date(2026, 6, 19)
    a, b, c = trade(date(2026, 6, 18), "1"), trade(date(2026, 6, 19), "2"), trade(date(2026, 9, 17), "3")
    assert selection.split_trades([a, b, c], confirm_from) == ([a], [b, c])


@pytest.mark.parametrize("select, confirm, reason", [
    ([("0.5", 19)], [("0.5", 3)], "선정 구간 거래 부족"),
    ([("0", 20)], [("0.5", 3)], "선정 구간 손실"),
    ([("0.5", 20)], [], "확인 구간 거래 없음"),
    ([("0.5", 20)], [("-0.1", 3)], "확인 구간 손실"),
    ([("0.5", 20)], [("0.4", 1), ("-0.1", 1)], None),
])
def test_evaluate(select, confirm, reason):
    """선정 구간 20건 이상·평균 플러스, 확인 구간 거래 있음·평균 플러스를 차례로 검사한다."""
    build = lambda spec: [trade(TODAY, r) for r, n in spec for _ in range(n)]
    result, got = selection.evaluate(build(select), build(confirm))
    assert got == reason
    if reason is None:
        assert result == {"select": {"trades": 20, "avg": Decimal("0.5")},
                          "confirm": {"trades": 2, "avg": Decimal("0.15")}}


def test_evaluate_without_trades_has_none_average():
    """거래가 없으면 평균은 None이다."""
    result, reason = selection.evaluate([], [])
    assert result == {"select": {"trades": 0, "avg": None}, "confirm": {"trades": 0, "avg": None}}
    assert reason == "선정 구간 거래 부족"


def passed_item(symbol, rank, avg, trades):
    """rank_selected 입력 항목."""
    return {"symbol": symbol, "rank": rank,
            "backtest": {"select": {"trades": 20, "avg": Decimal("0.1")},
                         "confirm": {"trades": trades, "avg": Decimal(avg)}}}


def test_rank_selected_orders_by_confirm_average_then_trades_then_rank_and_caps_5():
    """확인 구간 평균 내림차순, 같으면 거래 수 내림차순, 같으면 순위 오름차순으로 최대 5개."""
    items = [passed_item("A", 1, "0.1", 5), passed_item("B", 2, "0.3", 5), passed_item("C", 3, "0.3", 8),
             passed_item("D", 4, "0.2", 5), passed_item("E", 5, "0.2", 5), passed_item("F", 0, "0.2", 5)]
    assert [p["symbol"] for p in selection.rank_selected(items)] == ["C", "B", "F", "D", "E"]


SELECTED = [{
    "symbol": "012345", "name": "OO전자", "rank": 7,
    "tech": {"close": Decimal("45300"), "ma20": Decimal("44000"), "ma60": Decimal("46000"),
             "return_20d": Decimal("0.061"), "volatility_20d": Decimal("0.023"), "turnover_20d": Decimal("2E10")},
    "investor": {"foreigner": 120000, "institution": -3000},
    "backtest": {"select": {"trades": 31, "avg": Decimal("0.18")}, "confirm": {"trades": 9, "avg": Decimal("0.42")}},
}]
SUMMARY = {"ranked": 100, "dropped": {"보통주 아님": 25, "10만 원 초과": 37}, "candidates": 38,
           "risk": {"공매도": 4, "변동성": 6}, "backtest_rejected": 27, "passed": 1}


def test_format_mail_selected():
    """선정 메일: 첫 줄 제목, 종목별 근거, 단계별 집계(사유는 개수 내림차순, 같으면 이름순)."""
    assert selection.format_mail(TODAY, SELECTED, SUMMARY) == (
        "[종목선정] 2026-09-18 선정 1종목\n"
        "1. 012345 OO전자  확인구간 9건 평균 +0.42% | 선정구간 31건 평균 +0.18% | "
        "종가 45,300 MA20 위 MA60 아래 20일 +6.1% 변동성 2.3% 외국인 5일 +120,000주 기관 5일 -3,000주\n"
        "후보 38 → 위험 제외 10 (변동성 6, 공매도 4) → 백테스트 제외 27 → 통과 1\n"
        "순위 100 중 후보 탈락 62 (10만 원 초과 37, 보통주 아님 25)")


def test_format_mail_kept_previous_and_missing_values():
    """선정 없음·시간 초과 제목은 전날 종목을 적고, 없는 지표·빈 집계는 '-'와 괄호 없이 쓴다."""
    summary = {"ranked": 0, "dropped": {}, "candidates": 0, "risk": {}, "backtest_rejected": 0, "passed": 0}
    assert selection.format_mail(TODAY, [], summary, kept=["005930", "000660"], reason="선정 없음") == (
        "[종목선정] 2026-09-18 선정 없음, 전날 종목 유지(005930, 000660)\n"
        "후보 0 → 위험 제외 0 → 백테스트 제외 0 → 통과 0\n"
        "순위 0 중 후보 탈락 0")
    assert selection.format_mail(TODAY, [], summary, kept=[], reason="시간 초과").splitlines()[0] == (
        "[종목선정] 2026-09-18 시간 초과, 전날 종목 유지(없음)")
    blank = [{**SELECTED[0], "tech": dict.fromkeys(SELECTED[0]["tech"]),
              "investor": {"foreigner": None, "institution": None}}]
    assert selection.format_mail(TODAY, blank, SUMMARY).splitlines()[1].endswith(
        "종가 - MA20 - MA60 - 20일 - 변동성 - 외국인 5일 - 기관 5일 -")
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests/test_selection.py -v`
Expected: 수집 단계 ERROR `ModuleNotFoundError: No module named 'selection'`

- [ ] **Step 3: 구현**

`trader/selection.py`:

```python
"""매일 종목 선정 규칙(순수 계산): 후보 거르기, 위험 필터, 기술지표, 두 구간 백테스트 평가, 순위, 메일 문구."""
import statistics
from decimal import Decimal

MAX_PRICE = Decimal("100000")
MAX_SELECTED = 5
SHORT_RATE_MAX = Decimal("0.10")        # 최근 5일 평균 공매도 거래대금 비중
MARGIN_RATE_MAX = Decimal("0.08")       # 최신 신용융자 잔고율
VOLATILITY_MAX = Decimal("0.05")        # 최근 20일 일간 수익률 표준편차
TURNOVER_MIN = Decimal("10000000000")   # 최근 20일 일평균 거래대금 100억 원
BLOCKING_WARNINGS = {"LIQUIDATION_TRADING", "OVERHEATED", "INVESTMENT_WARNING", "INVESTMENT_RISK"}
MIN_SELECT_TRADES = 20


def candidates(rankings, stocks):
    """순위 종목 중 보통주·활성·10만 원 이하만 순위순으로 남기고, 나머지는 (종목, 사유)로 돌려준다."""
    kept, dropped = [], []
    for r in rankings:
        stock = stocks.get(r["symbol"])
        if stock is None:
            dropped.append((r["symbol"], "종목 정보 없음"))
        elif stock["security_type"] != "STOCK" or not stock["common"]:
            dropped.append((r["symbol"], "보통주 아님"))
        elif not stock["active"]:
            dropped.append((r["symbol"], "상장 상태 아님"))
        elif r["last_price"] > MAX_PRICE:
            dropped.append((r["symbol"], "10만 원 초과"))
        else:
            kept.append({**r, "name": stock["name"]})
    return kept, dropped


def _mean(values):
    """Decimal 목록의 평균."""
    return sum(values, Decimal(0)) / len(values)


def technicals(daily):
    """날짜 오름차순 일봉으로 종가·20·60일 이동평균·20일 수익률·변동성·일평균 거래대금을 계산한다. 모자라면 None."""
    closes = [d.close for d in daily]
    tech = {"close": closes[-1] if closes else None,
            "ma20": _mean(closes[-20:]) if len(closes) >= 20 else None,
            "ma60": _mean(closes[-60:]) if len(closes) >= 60 else None,
            "return_20d": None, "volatility_20d": None, "turnover_20d": None}
    if len(daily) >= 21:
        last = daily[-21:]
        returns = [b.close / a.close - 1 for a, b in zip(last, last[1:])]
        tech["return_20d"] = last[-1].close / last[0].close - 1
        tech["volatility_20d"] = statistics.stdev(returns)
        tech["turnover_20d"] = _mean([d.close * d.volume for d in last[1:]])
    return tech


def short_average(short):
    """최근 5개 공매도 기록 중 값이 있는 거래대금 비중의 평균. 없으면 None."""
    rates = [r["amount_rate"] for r in short[:5] if r["amount_rate"] is not None]
    return _mean(rates) if rates else None


def investor_sums(records):
    """외국인·기관 순매수 주 수를 값 있는 것만 더한다. 모두 없으면 None."""
    def total(key):
        """한 분류의 합."""
        values = [r[key] for r in records if r[key] is not None]
        return sum(values) if values else None
    return {"foreigner": total("foreigner"), "institution": total("institution")}


def _active(warning, today):
    """경고가 today에 적용 중이면 True."""
    return ((warning["start"] is None or warning["start"] <= today)
            and (warning["end"] is None or today <= warning["end"]))


def risk_reason(today, stock, warnings, short, credit, tech):
    """위험 필터를 거래정지·경고·공매도·신용·일봉 부족·변동성·유동성 순으로 검사해 첫 제외 사유를, 없으면 None을 반환한다."""
    if stock["suspended"]:
        return "거래정지"
    if any(w["type"] in BLOCKING_WARNINGS and _active(w, today) for w in warnings):
        return "경고"
    short_avg = short_average(short)
    if short_avg is not None and short_avg >= SHORT_RATE_MAX:
        return "공매도"
    margin = credit[0]["margin_balance_rate"] if credit else None
    if margin is not None and margin >= MARGIN_RATE_MAX:
        return "신용"
    if tech["volatility_20d"] is None or tech["turnover_20d"] is None:
        return "일봉 부족"
    if tech["volatility_20d"] >= VOLATILITY_MAX:
        return "변동성"
    if tech["turnover_20d"] < TURNOVER_MIN:
        return "유동성"
    return None


def split_trades(trades, confirm_from):
    """거래를 청산일 기준으로 (선정 구간, 확인 구간)으로 나눈다. confirm_from 당일부터 확인 구간이다."""
    select = [t for t in trades if t.exit_ts.date() < confirm_from]
    confirm = [t for t in trades if t.exit_ts.date() >= confirm_from]
    return select, confirm


def _stats(trades):
    """거래 수와 평균 수익률(%). 거래가 없으면 평균은 None."""
    return {"trades": len(trades), "avg": _mean([t.return_pct for t in trades]) if trades else None}


def evaluate(select_trades, confirm_trades):
    """두 구간 통계와 탈락 사유(선정 구간 거래 부족·손실, 확인 구간 거래 없음·손실, 통과면 None)를 반환한다."""
    result = {"select": _stats(select_trades), "confirm": _stats(confirm_trades)}
    s, c = result["select"], result["confirm"]
    if s["trades"] < MIN_SELECT_TRADES:
        reason = "선정 구간 거래 부족"
    elif s["avg"] <= 0:
        reason = "선정 구간 손실"
    elif c["trades"] == 0:
        reason = "확인 구간 거래 없음"
    elif c["avg"] <= 0:
        reason = "확인 구간 손실"
    else:
        reason = None
    return result, reason


def rank_selected(passed):
    """확인 구간 평균 내림차순, 거래 수 내림차순, 순위 오름차순으로 정렬해 최대 5개를 고른다."""
    key = lambda p: (-p["backtest"]["confirm"]["avg"], -p["backtest"]["confirm"]["trades"], p["rank"])
    return sorted(passed, key=key)[:MAX_SELECTED]


def _counts(reasons):
    """사유별 개수를 '사유 N, ...'로 쓴다. 개수 내림차순, 같으면 이름순. 비었으면 빈 문자열."""
    items = sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))
    return ", ".join(f"{k} {v}" for k, v in items)


def _with_counts(text, reasons):
    """집계가 있으면 괄호로 붙인다."""
    detail = _counts(reasons)
    return f"{text} ({detail})" if detail else text


def _num(value, fmt):
    """값이 있으면 fmt로, 없으면 '-'."""
    return "-" if value is None else format(value, fmt)


def _position(close, ma):
    """종가가 이동평균 이상이면 '위', 아래면 '아래', 계산 불가면 '-'."""
    if close is None or ma is None:
        return "-"
    return "위" if close >= ma else "아래"


def _stock_line(i, s):
    """선정 종목 한 줄: 두 구간 백테스트, 기술지표, 5일 순매수."""
    t, bt, inv = s["tech"], s["backtest"], s["investor"]
    pct = lambda v, fmt: "-" if v is None else f"{v * 100:{fmt}}%"
    shares = lambda v: "-" if v is None else f"{v:+,}주"
    return (f"{i}. {s['symbol']} {s['name']}  "
            f"확인구간 {bt['confirm']['trades']}건 평균 {_num(bt['confirm']['avg'], '+.2f')}% | "
            f"선정구간 {bt['select']['trades']}건 평균 {_num(bt['select']['avg'], '+.2f')}% | "
            f"종가 {_num(t['close'], ',.0f')} MA20 {_position(t['close'], t['ma20'])} "
            f"MA60 {_position(t['close'], t['ma60'])} 20일 {pct(t['return_20d'], '+.1f')} "
            f"변동성 {pct(t['volatility_20d'], '.1f')} 외국인 5일 {shares(inv['foreigner'])} "
            f"기관 5일 {shares(inv['institution'])}")


def format_mail(run_date, selected, summary, kept=None, reason=None):
    """선정 메일 문구. 첫 줄이 제목이다. reason(선정 없음·시간 초과)이 있으면 전날 종목 유지 제목을 쓴다."""
    if reason:
        title = f"[종목선정] {run_date} {reason}, 전날 종목 유지({', '.join(kept) if kept else '없음'})"
    else:
        title = f"[종목선정] {run_date} 선정 {len(selected)}종목"
    lines = [title] + [_stock_line(i, s) for i, s in enumerate(selected, 1)]
    risk_total = sum(summary["risk"].values())
    lines.append(f"후보 {summary['candidates']} → {_with_counts(f'위험 제외 {risk_total}', summary['risk'])} "
                 f"→ 백테스트 제외 {summary['backtest_rejected']} → 통과 {summary['passed']}")
    lines.append(_with_counts(f"순위 {summary['ranked']} 중 후보 탈락 {sum(summary['dropped'].values())}",
                              summary["dropped"]))
    return "\n".join(lines)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests -v`
Expected: 179 passed (146 + 33)

- [ ] **Step 5: 커밋**

```powershell
git add selection.py tests/test_selection.py
git commit -m @'
매일 종목 선정 규칙 모듈 추가

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
'@
```

---

### Task 4: 선정 실행 `selector.run`

**Files:**
- Create: `trader/selector.py`
- Test: `trader/tests/test_selector.py`

**Interfaces:**
- Consumes: Task 1 `TossClient` 메서드, Task 2 `collector.collect(..., stop)`·`store.save_candidates`·`store.load_candidates`, Task 3 `selection` 함수들, 기존 `store.load_bars(conn, source, date_from, date_to, symbols)`, `backtest.regular_session(bars_by_symbol)`, `engine.run(strategy_cls, params, bars_by_symbol, costs)`, `paper.COSTS`, `strategies.Orb`, `collector.read_symbols(path)`, `notify.send_mail(text)`
- Produces:
  - `selector.run(client, conn, symbols_path, now=lambda: datetime.now(KST), deadline=None) -> int`
  - `selector.backtest_trades(conn, symbol, today) -> [engine.Trade]`
  - `selector.backfill_days(today) -> [date]` 최신순
  - `selector.write_symbols(path, today, selected) -> None`
  - 상수 `HISTORY_DAYS = 365`, `CONFIRM_DAYS = 91`

- [ ] **Step 1: 실패하는 테스트 작성**

`trader/tests/test_selector.py`:

```python
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest

import engine
import selector
import store
from bars import KST, Bar, DailyBar
from toss import TossAuthError, TossError

TODAY = date(2026, 9, 18)  # 금요일
START = datetime(2026, 9, 18, 7, 30, tzinfo=KST)


class Clock:
    """가짜 시계."""

    def __init__(self, start):
        """시작 시각을 받는다."""
        self.t = start

    def now(self):
        """현재 가짜 시각."""
        return self.t


def daily(n=80):
    """TODAY 포함 n개 일봉. 어제까지는 50,000/50,500 번갈아, 오늘 봉은 999,999(제외 확인용)."""
    first = TODAY - timedelta(days=n - 1)
    out = []
    for i in range(n):
        day = first + timedelta(days=i)
        p = Decimal("999999") if day == TODAY else Decimal("50000" if i % 2 == 0 else "50500")
        out.append(DailyBar(day, p, p, p, p, 1_000_000))
    return out


class FakeClient:
    """TossClient 대역. specs[symbol]로 순위·종목·위험 데이터를 만들고, 호출마다 시계를 step만큼 흘린다."""

    def __init__(self, clock, specs, holiday=False, fail=None, step=timedelta(0)):
        """시계, 종목별 설정, 휴장 여부, 실패 규칙 fail(method, symbol), 호출당 흐를 시간을 받는다."""
        self.clock, self.specs, self.holiday, self.step = clock, specs, holiday, step
        self.fail = fail or (lambda method, symbol: None)
        self.calls = []

    def _call(self, method, symbol=None):
        """호출을 기록하고 시간을 흘리며, 실패 규칙이 예외를 주면 던진다."""
        self.calls.append((method, symbol))
        self.clock.t += self.step
        error = self.fail(method, symbol)
        if error:
            raise error

    def market_hours(self, day):
        """휴장이면 None, 아니면 정규장 시간."""
        self._call("market_hours")
        return None if self.holiday else (datetime.combine(day, time(9), KST), datetime.combine(day, time(15, 30), KST))

    def rankings(self):
        """specs 순서가 순위다."""
        self._call("rankings")
        return [{"rank": i + 1, "symbol": s, "last_price": Decimal(spec.get("price", "50000"))}
                for i, (s, spec) in enumerate(self.specs.items())]

    def stocks(self, symbols):
        """specs의 security_type(기본 STOCK)으로 종목 정보를 만든다."""
        self._call("stocks")
        return {s: {"name": f"종목{s}", "security_type": self.specs[s].get("type", "STOCK"), "common": True,
                    "active": True, "suspended": False} for s in symbols}

    def warnings(self, symbol):
        """경고 없음."""
        self._call("warnings", symbol)
        return []

    def short_selling(self, symbol, count):
        """specs의 short(기본 0.05) 비중 5일."""
        self._call("short_selling", symbol)
        return [{"date": TODAY - timedelta(days=i + 1), "amount_rate": Decimal(self.specs[symbol].get("short", "0.05"))}
                for i in range(count)]

    def credit_trades(self, symbol, count):
        """신용 잔고율 1%."""
        self._call("credit_trades", symbol)
        return [{"date": TODAY - timedelta(days=1), "margin_balance_rate": Decimal("0.01")}]

    def investor_trading(self, symbol, count):
        """외국인 +100주, 기관 -10주씩 count일."""
        self._call("investor_trading", symbol)
        return [{"date": TODAY - timedelta(days=i + 1), "foreigner": 100, "institution": -10} for i in range(count)]

    def fetch_daily(self, symbol, count):
        """오늘 봉까지 포함한 일봉."""
        self._call("fetch_daily", symbol)
        return daily()[-count:]


def trades_for(select_avg, confirm_avg, select_n=20, confirm_n=5):
    """선정 구간(청산 TODAY−200일) select_n건, 확인 구간(청산 TODAY−10일) confirm_n건의 일정 수익률 거래."""
    def make(day, ret, n):
        """같은 날 n건."""
        ts = datetime.combine(day, time(10, 1), KST)
        return [engine.Trade("X", ts, Decimal("100"), ts, Decimal("100"), Decimal(ret), "signal") for _ in range(n)]
    return make(TODAY - timedelta(days=200), select_avg, select_n) + make(TODAY - timedelta(days=10), confirm_avg, confirm_n)


@pytest.fixture
def env(monkeypatch, tmp_path):
    """메일·백필·백테스트를 대역으로 바꾸고, 전날 종목 파일을 만든다."""
    sent, collects = [], []
    backtests = {}
    monkeypatch.setattr(selector.notify, "send_mail", sent.append)
    monkeypatch.setattr(selector.collector, "collect",
                        lambda conn, client, symbols, days, stop=None: collects.append((symbols, days, stop)) or ({}, []))
    monkeypatch.setattr(selector, "backtest_trades", lambda conn, symbol, today: backtests[symbol])
    path = tmp_path / "paper_symbols.txt"
    path.write_text("005930\n000660\n", encoding="utf-8")
    return {"sent": sent, "collects": collects, "backtests": backtests, "path": path}


SPECS = {"A": {}, "B": {"type": "ETF"}, "C": {}, "D": {"short": "0.2"}, "E": {"price": "150000"}}


def rows(conn):
    """오늘 선정 결과를 (종목, 상태, 사유)로."""
    return [(r["symbol"], r["status"], r["reason"]) for r in store.load_candidates(conn, TODAY)]


def test_holiday_does_nothing(conn, env):
    """휴장이면 장 시간만 조회하고 파일·DB·메일을 건드리지 않는다."""
    client = FakeClient(Clock(START), SPECS, holiday=True)
    assert selector.run(client, conn, env["path"], now=client.clock.now) == 0
    assert client.calls == [("market_hours", None)]
    assert env["sent"] == [] and rows(conn) == []
    assert env["path"].read_text(encoding="utf-8") == "005930\n000660\n"


def test_selects_writes_file_saves_candidates_and_mails(conn, env):
    """위험 필터·백테스트를 거쳐 선정한 종목으로 파일을 바꾸고 DB·메일에 근거를 남긴다."""
    env["backtests"].update(A=trades_for("0.2", "0.4"), C=trades_for("0.2", "-0.1"))
    client = FakeClient(Clock(START), SPECS)
    assert selector.run(client, conn, env["path"], now=client.clock.now,
                        deadline=datetime(2026, 9, 18, 8, 45, tzinfo=KST)) == 0
    assert env["path"].read_text(encoding="utf-8") == "# 2026-09-18 종목 선정기 자동 생성\nA  # 종목A\n"
    assert rows(conn) == [("A", "selected", None), ("C", "rejected", "확인 구간 손실"), ("D", "rejected", "공매도")]
    [a] = [r for r in store.load_candidates(conn, TODAY) if r["symbol"] == "A"]
    assert a["metrics"]["tech"]["close"] == "50000"
    assert a["metrics"]["backtest"]["confirm"] == {"trades": 5, "avg": "0.4"}
    [(symbols, days, stop)] = env["collects"]
    assert symbols == ["A", "C"]
    assert days[0] == date(2026, 9, 17) and len(days) == 261 and all(d.weekday() < 5 for d in days)
    assert stop() is False
    [mail] = env["sent"]
    lines = mail.splitlines()
    assert lines[0] == "[종목선정] 2026-09-18 선정 1종목"
    assert lines[1].startswith("1. A 종목A  확인구간 5건 평균 +0.40% | 선정구간 20건 평균 +0.20% | 종가 50,000 ")
    assert lines[1].endswith("외국인 5일 +500주 기관 5일 -50주")
    assert lines[2] == "후보 3 → 위험 제외 1 (공매도 1) → 백테스트 제외 1 → 통과 1"
    assert lines[3] == "순위 5 중 후보 탈락 2 (10만 원 초과 1, 보통주 아님 1)"


def test_lookup_failure_rejects_symbol_and_no_selection_keeps_file(conn, env):
    """한 종목 조회 실패는 그 종목만 제외하고, 선정이 없으면 파일을 두고 전날 종목 유지 메일을 보낸다."""
    env["backtests"].update(C=trades_for("0.2", "-0.1"))
    fail = lambda method, symbol: TossError("HTTP500") if (method, symbol) == ("short_selling", "A") else None
    client = FakeClient(Clock(START), SPECS, fail=fail)
    assert selector.run(client, conn, env["path"], now=client.clock.now) == 0
    assert rows(conn) == [("A", "rejected", "조회 실패"), ("C", "rejected", "확인 구간 손실"), ("D", "rejected", "공매도")]
    assert env["path"].read_text(encoding="utf-8") == "005930\n000660\n"
    lines = env["sent"][0].splitlines()
    assert lines[0] == "[종목선정] 2026-09-18 선정 없음, 전날 종목 유지(005930, 000660)"
    assert lines[1] == "후보 3 → 위험 제외 2 (공매도 1, 조회 실패 1) → 백테스트 제외 1 → 통과 0"


def test_deadline_stops_keeps_file_and_mails_timeout(conn, env):
    """마감을 넘기면 남은 후보·백필·백테스트를 멈추고, 파일은 두고, 평가 못 한 후보를 표시하고 시간 초과 메일을 보낸다."""
    client = FakeClient(Clock(START), SPECS, step=timedelta(minutes=1))
    assert selector.run(client, conn, env["path"], now=client.clock.now,
                        deadline=datetime(2026, 9, 18, 7, 35, tzinfo=KST)) == 0
    assert env["collects"] == []
    assert rows(conn) == [("A", "rejected", "미평가(시간 초과)")]
    assert env["path"].read_text(encoding="utf-8") == "005930\n000660\n"
    assert env["sent"][0].splitlines()[0] == "[종목선정] 2026-09-18 시간 초과, 전날 종목 유지(005930, 000660)"


def test_deadline_passed_during_backfill_skips_backtest(conn, env, monkeypatch):
    """백필 중 마감이 지나면 백테스트를 하지 않고 시간 초과로 끝낸다."""
    clock = Clock(START)
    def slow_collect(conn, client, symbols, days, stop=None):
        """백필이 마감을 넘기는 상황."""
        clock.t = datetime(2026, 9, 18, 8, 46, tzinfo=KST)
        assert stop() is True
        return {}, []
    monkeypatch.setattr(selector.collector, "collect", slow_collect)
    monkeypatch.setattr(selector, "backtest_trades", lambda *args: pytest.fail("백테스트하면 안 됨"))
    client = FakeClient(clock, SPECS)
    assert selector.run(client, conn, env["path"], now=clock.now,
                        deadline=datetime(2026, 9, 18, 8, 45, tzinfo=KST)) == 0
    assert rows(conn) == [("A", "rejected", "미평가(시간 초과)"), ("C", "rejected", "미평가(시간 초과)"),
                          ("D", "rejected", "공매도")]
    assert env["sent"][0].splitlines()[0].startswith("[종목선정] 2026-09-18 시간 초과")


def test_auth_error_propagates(conn, env):
    """인증 오류는 run 밖으로 올리고 파일은 그대로 둔다."""
    fail = lambda method, symbol: TossAuthError("access_denied") if method == "warnings" else None
    client = FakeClient(Clock(START), SPECS, fail=fail)
    with pytest.raises(TossAuthError):
        selector.run(client, conn, env["path"], now=client.clock.now)
    assert env["path"].read_text(encoding="utf-8") == "005930\n000660\n"


def test_rerun_same_day_overwrites_candidates(conn, env):
    """같은 날 다시 실행하면 선정 결과가 중복되지 않는다."""
    env["backtests"].update(A=trades_for("0.2", "0.4"), C=trades_for("0.2", "-0.1"))
    for _ in range(2):
        client = FakeClient(Clock(START), SPECS)
        selector.run(client, conn, env["path"], now=client.clock.now)
    assert len(store.load_candidates(conn, TODAY)) == 3


def minute_day(day, breakout=True):
    """day 08:50~15:40 1분봉. 장전 봉은 200, 정규장은 100에서 09:40에 101, 10:00에 104(orb 매수·목표 매도)."""
    out = []
    start = datetime.combine(day, time(8, 50), KST)
    for i in range(411):
        ts = start + timedelta(minutes=i)
        t = ts.time()
        if t < time(9, 0):
            p = "200"
        elif not breakout or t < time(9, 40):
            p = "100"
        else:
            p = "101" if t < time(10, 0) else "104"
        out.append(Bar(ts, Decimal(p), Decimal(p), Decimal(p), Decimal(p), 100))
    return out


def test_backtest_trades_uses_regular_toss_bars_within_365_days(conn):
    """수집기 봉(toss)만, 365일 이내만, 정규장 봉만으로 모의투자 조건 orb 백테스트를 돌린다."""
    store.save_bars(conn, "A", minute_day(date(2026, 9, 17)), source="toss")
    store.save_bars(conn, "A", minute_day(date(2025, 9, 17)), source="toss")       # 366일 전
    store.save_bars(conn, "A", minute_day(date(2026, 9, 16)), source="toss_live")  # 장중 봉
    trades = selector.backtest_trades(conn, "A", TODAY)
    assert [(t.entry_ts, t.exit_ts, t.exit_reason) for t in trades] == [
        (datetime(2026, 9, 17, 9, 41, tzinfo=KST), datetime(2026, 9, 17, 10, 1, tzinfo=KST), "signal")]
    assert trades[0].entry_price == Decimal("101.0505")


def test_backfill_days_and_write_symbols(tmp_path):
    """백필 날짜는 어제부터 365일 전까지 평일 최신순이고, 종목 파일은 주석 헤더와 종목명 주석으로 쓴다."""
    days = selector.backfill_days(TODAY)
    assert (days[0], days[-1], len(days)) == (date(2026, 9, 17), date(2025, 9, 18), 261)
    path = tmp_path / "paper_symbols.txt"
    selector.write_symbols(path, TODAY, [{"symbol": "A", "name": "에이"}, {"symbol": "B", "name": "비"}])
    assert path.read_text(encoding="utf-8") == "# 2026-09-18 종목 선정기 자동 생성\nA  # 에이\nB  # 비\n"
    assert selector.read_symbols(path) == ["A", "B"]
    assert not (tmp_path / "paper_symbols.txt.tmp").exists()
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests/test_selector.py -v`
Expected: 수집 단계 ERROR `ModuleNotFoundError: No module named 'selector'`

- [ ] **Step 3: 구현**

`trader/selector.py`:

```python
"""매일 종목 선정 진입점. 작업 스케줄러가 평일 07:30에 실행해 모의투자 종목(paper_symbols.txt)을 고르고 메일로 알린다."""
import logging
import os
from collections import Counter
from datetime import datetime, timedelta

import collector
import engine
import notify
import selection
import store
from backtest import regular_session
from bars import KST
from collector import read_symbols
from paper import COSTS
from strategies import Orb
from toss import TossAuthError, TossError

HISTORY_DAYS = 365
CONFIRM_DAYS = 91
DAILY_COUNT = 80
TIMEOUT_REASON = "미평가(시간 초과)"

log = logging.getLogger("selector")


def backfill_days(today):
    """어제부터 365일 전까지의 평일을 최신순으로 반환한다."""
    days = (today - timedelta(days=i) for i in range(1, HISTORY_DAYS + 1))
    return [d for d in days if d.weekday() < 5]


def backtest_trades(conn, symbol, today):
    """최근 365일 수집기 봉(toss) 중 정규장 봉으로 모의투자 조건 orb 백테스트를 돌려 거래 목록을 반환한다."""
    bars = store.load_bars(conn, "toss", today - timedelta(days=HISTORY_DAYS), today - timedelta(days=1), [symbol])
    return engine.run(Orb, {}, regular_session(bars), COSTS)


def write_symbols(path, today, selected):
    """선정 종목을 임시 파일에 쓴 뒤 바꿔치기해 모의투자 종목 파일을 갱신한다."""
    lines = [f"# {today} 종목 선정기 자동 생성"] + [f"{s['symbol']}  # {s['name']}" for s in selected]
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def run(client, conn, symbols_path, now=lambda: datetime.now(KST), deadline=None):
    """종목 선정 1회: 휴장 확인 → 후보 → 위험 필터 → 백필 → 두 구간 백테스트 → 저장 → 파일 → 메일. 종료 코드를 반환한다.
    deadline(datetime)을 넘기면 남은 단계를 멈추고 전날 종목을 유지한다. None이면 마감이 없다."""
    today = now().date()
    if client.market_hours(today) is None:
        log.info("%s 휴장", today)
        return 0

    def over():
        """마감을 넘겼으면 True."""
        return deadline is not None and now() >= deadline

    rankings = client.rankings()
    stocks = client.stocks([r["symbol"] for r in rankings])
    kept, dropped = selection.candidates(rankings, stocks)
    summary = {"ranked": len(rankings), "dropped": dict(Counter(reason for _, reason in dropped)),
               "candidates": len(kept), "risk": Counter(), "backtest_rejected": 0, "passed": 0}
    rows, survivors, passed = {}, [], []
    timed_out = False

    for cand in kept:
        if over():
            timed_out = True
            break
        symbol = cand["symbol"]
        row = rows[symbol] = {"symbol": symbol, "name": cand["name"], "rank": cand["rank"], "status": "rejected",
                              "reason": None, "metrics": {"last_price": cand["last_price"]}}
        try:
            warnings = client.warnings(symbol)
            short = client.short_selling(symbol, 5)
            credit = client.credit_trades(symbol, 1)
            investor = client.investor_trading(symbol, 5)
            daily = [d for d in client.fetch_daily(symbol, DAILY_COUNT) if d.date < today]
        except TossAuthError:
            raise
        except TossError as e:
            row["reason"] = "조회 실패"
            summary["risk"]["조회 실패"] += 1
            log.error("%s 조회 실패 %s", symbol, e.code)
            continue
        tech = selection.technicals(daily)
        row["metrics"].update(tech=tech, investor=selection.investor_sums(investor),
                              short_avg=selection.short_average(short),
                              margin=credit[0]["margin_balance_rate"] if credit else None,
                              warnings=[w["type"] for w in warnings])
        reason = selection.risk_reason(today, stocks[symbol], warnings, short, credit, tech)
        if reason:
            row["reason"] = reason
            summary["risk"][reason] += 1
        else:
            survivors.append(cand)

    if survivors and not timed_out:
        collector.collect(conn, client, [c["symbol"] for c in survivors], backfill_days(today), stop=over)
        timed_out = over()

    if not timed_out:
        confirm_from = today - timedelta(days=CONFIRM_DAYS)
        for cand in survivors:
            if over():
                timed_out = True
                break
            row = rows[cand["symbol"]]
            result, reason = selection.evaluate(
                *selection.split_trades(backtest_trades(conn, cand["symbol"], today), confirm_from))
            row["metrics"]["backtest"] = result
            if reason:
                row["reason"] = reason
                summary["backtest_rejected"] += 1
            else:
                row["status"] = "passed"
                passed.append({**cand, "backtest": result, "tech": row["metrics"]["tech"],
                               "investor": row["metrics"]["investor"]})

    selected = [] if timed_out else selection.rank_selected(passed)
    for s in selected:
        rows[s["symbol"]]["status"] = "selected"
    for row in rows.values():
        if row["status"] == "rejected" and row["reason"] is None:
            row["reason"] = TIMEOUT_REASON
    summary["passed"] = len(passed)
    store.save_candidates(conn, today, list(rows.values()))

    previous = read_symbols(symbols_path) if symbols_path.exists() else []
    if timed_out:
        reason = "시간 초과"
    elif not selected:
        reason = "선정 없음"
    else:
        reason = None
        write_symbols(symbols_path, today, selected)
    log.info("%s 선정 %s", today, [s["symbol"] for s in selected] if not reason else reason)
    notify.send_mail(selection.format_mail(today, selected, summary, kept=previous, reason=reason))
    return 0
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests -v`
Expected: 188 passed (179 + 9)

- [ ] **Step 5: 커밋**

```powershell
git add selector.py tests/test_selector.py
git commit -m @'
매일 종목 선정 실행 흐름 추가

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
'@
```

---

### Task 5: `selector.py` 진입점과 README

**Files:**
- Modify: `trader/selector.py` (import, 상수, `setup_logging`, `main`, `__main__`)
- Modify: `trader/README.md`
- Test: `trader/tests/test_selector.py` (끝에 추가)

**Interfaces:**
- Consumes: `selector.run` (Task 4), `TossClient(client_id, client_secret, base_url, rps, token_path)`
- Produces: `selector.main(argv=None, now=None) -> int`, `selector.REQUIRED_ENV`, `selector.ROOT`, `selector.DEADLINE = time(8, 45)`

- [ ] **Step 1: 실패하는 테스트 작성**

`trader/tests/test_selector.py` 상단 import에 `import logging`, `import os`를 추가하고 끝에 추가:

```python
@pytest.fixture
def main_env(monkeypatch):
    """main이 실제 .env·메일을 쓰지 않도록 막고 보낸 메일 목록을 돌려준다."""
    monkeypatch.setattr(selector, "load_dotenv", lambda *args, **kwargs: None)
    for key in selector.REQUIRED_ENV + ("TOSS_BASE_URL", "TOSS_RPS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TOSS_CLIENT_ID", "CID-X")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "SECRET-X")
    sent = []
    monkeypatch.setattr(selector.notify, "send_mail", sent.append)
    return sent


def test_main_missing_env_fails_without_secrets(main_env, monkeypatch, caplog):
    """설정이 빠지면 1을 반환하고 로그·메일에는 변수 이름과 예외 종류만 남긴다."""
    monkeypatch.setenv("DATABASE_URL", "SECRETPW")
    monkeypatch.delenv("TOSS_CLIENT_ID")
    with caplog.at_level(logging.INFO):
        assert selector.main([], now=START) == 1
    assert ".env 누락: TOSS_CLIENT_ID" in caplog.text
    assert "SECRET-X" not in caplog.text and "SECRETPW" not in caplog.text
    assert main_env == ["[종목선정] 2026-09-18 실행 실패: RuntimeError"]


@pytest.mark.parametrize("argv, deadline", [
    ([], datetime(2026, 9, 18, 8, 45, tzinfo=KST)),
    (["--no-deadline"], None),
])
def test_main_passes_deadline_and_default_client(conn, main_env, monkeypatch, argv, deadline):
    """토스 기본 설정으로 클라이언트를 만들고, 기본은 08:45 마감, --no-deadline이면 마감 없이 run을 부른다."""
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    created, calls = [], []
    monkeypatch.setattr(selector, "TossClient", lambda *args: created.append(args[:4]) or "CLIENT")
    monkeypatch.setattr(selector, "run", lambda client, conn, path, deadline=None: calls.append((client, path, deadline)) or 0)
    assert selector.main(argv, now=START) == 0
    assert created == [("CID-X", "SECRET-X", "https://openapi.tossinvest.com", 15.0)]
    assert calls == [("CLIENT", selector.ROOT / "paper_symbols.txt", deadline)]


def test_main_auth_error_returns_1_and_mails(conn, main_env, monkeypatch):
    """인증 오류면 실행 실패 메일을 보내고 1을 반환한다."""
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setattr(selector, "TossClient", lambda *args: "CLIENT")
    def auth_fail(*args, **kwargs):
        """인증 실패를 흉내 낸다."""
        raise TossAuthError("access_denied")
    monkeypatch.setattr(selector, "run", auth_fail)
    assert selector.main([], now=START) == 1
    assert main_env == ["[종목선정] 2026-09-18 실행 실패: TossAuthError"]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests/test_selector.py -v`
Expected: 새 테스트 4개 FAIL/ERROR (`AttributeError: module 'selector' has no attribute 'load_dotenv'` 또는 `REQUIRED_ENV`)

- [ ] **Step 3: 구현**

`trader/selector.py`의 import 블록을 아래로 바꾼다:

```python
import argparse
import logging
import os
import sys
from collections import Counter
from datetime import datetime, time, timedelta
from pathlib import Path

import psycopg
from dotenv import load_dotenv

import collector
import engine
import notify
import selection
import store
from backtest import regular_session
from bars import KST
from collector import read_symbols
from paper import COSTS
from strategies import Orb
from toss import TossAuthError, TossClient, TossError
```

상수 블록 맨 위에 추가:

```python
ROOT = Path(__file__).resolve().parent
REQUIRED_ENV = ("TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET", "DATABASE_URL")
DEADLINE = time(8, 45)
```

파일 끝에 추가:

```python
def setup_logging(today):
    """콘솔과 logs/selector-YYYY-MM-DD.log에 로그를 남기도록 설정한다."""
    (ROOT / "logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler(ROOT / "logs" / f"selector-{today}.log", encoding="utf-8")],
    )


def main(argv=None, now=None):
    """종목 선정 1회 실행. 정상·휴장·시간 초과면 0, 설정·DB·인증 등 실행 전체 실패면 1을 반환한다."""
    parser = argparse.ArgumentParser(description="매일 모의투자 종목 선정")
    parser.add_argument("--no-deadline", action="store_true", help="08:45 마감 없이 실행 (첫 백필용)")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
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
        deadline = None if args.no_deadline else datetime.combine(now.date(), DEADLINE, KST)
        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
            return run(client, conn, ROOT / "paper_symbols.txt", deadline=deadline)
    except Exception as e:
        # 예외 문자열에 접속 문자열 등이 섞일 수 있어 종류와 토스 오류 코드만 남긴다
        log.error("실행 실패: %s %s", type(e).__name__, e.code if isinstance(e, TossError) else "")
        notify.send_mail(f"[종목선정] {now.date()} 실행 실패: {type(e).__name__}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

`trader/README.md`:
- 1행 제목을 `# 토스증권 1분봉 수집기, 백테스터, 모의투자, 종목 선정`으로 바꾸고, 3행 끝 문장을 `..., 장중 실시간 봉으로 모의투자하고, 매일 아침 모의투자 종목을 고른다.`로, 4행 설계 목록 끝에 `, \`docs/superpowers/specs/2026-09-17-daily-selector-design.md\``를 더한다
- "첫 실행 수동 검증 (1회)" 절 바로 앞에 아래 절을 넣는다:

````markdown
## 매일 종목 선정

```powershell
.\.venv\Scripts\python selector.py
```

평일 07:30에 실행해 모의투자 종목을 최대 5개 고르고 `paper_symbols.txt`를 갱신한 뒤 결과를 메일로 보낸다.

- 후보: 시장 거래대금 1년 상위 100(투자 유의 제외) 중 보통주·상장 중·전일 종가 10만 원 이하
- 위험 제외: 거래정지, 활성 경고(정리매매·단기과열·투자경고·투자위험), 최근 5일 평균 공매도 거래대금 비중 10% 이상, 신용융자 잔고율 8% 이상, 최근 20일 일간 수익률 표준편차 5% 이상, 최근 20일 일평균 거래대금 100억 원 미만(일봉 부족 포함)
- 백필: 남은 후보의 최근 365일 1분봉 중 없는 날짜만 받는다(`collect_runs` 재사용)
- 백테스트: 모의투자와 같은 조건(orb 기본값, 정규장, 수수료·세금·슬리피지). 청산일 기준 91일 전까지는 선정 구간(거래 20건 이상·평균 플러스), 이후는 확인 구간(거래 있음·평균 플러스)
- 선정: 확인 구간 평균 수익률 순 최대 5개. 없으면 파일을 바꾸지 않는다(전날 종목 유지)
- 마감: 08:45를 넘기면 멈추고 전날 종목 유지 메일을 보낸다
- 기록: `selection_candidates`(날짜·종목별 상태, 제외 사유, 지표·백테스트 수치), 로그 `logs/selector-YYYY-MM-DD.log`
- 첫 실행은 후보 1년치 백필에 1~2시간 걸리므로 작업 스케줄러 등록 전에 `.\.venv\Scripts\python selector.py --no-deadline`으로 수동 실행한다. 장중(08:55~15:31)과 수집기 시각(20:30)을 피한다

작업 스케줄러 등록(`trader` 폴더 기준, 관리자 권한 불필요):

```powershell
$dir = (Get-Location).Path
$action = New-ScheduledTaskAction -Execute "$dir\.venv\Scripts\python.exe" -Argument "selector.py" -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 07:30
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName "토스 종목 선정" -Action $action -Trigger $trigger -Settings $settings
```

결과 확인:

```sql
SELECT status, reason, count(*) FROM selection_candidates WHERE run_date = CURRENT_DATE GROUP BY 1, 2 ORDER BY 1, 3 DESC;
```
````

- [ ] **Step 4: 테스트 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests -v`
Expected: 192 passed (188 + 4)

- [ ] **Step 5: 커밋**

```powershell
git add selector.py README.md tests/test_selector.py
git commit -m @'
종목 선정 실행 진입점과 사용법 추가

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
'@
```

---

### Task 6: 운영 환경 수동 검증

코드 변경 없음. 사용자 PC에서 실제 토스 키·운영 DB·Gmail로 확인한다. `.env` 값은 출력하지 않는다. 장중(08:55~15:31)과 20:30 수집기 시각을 피해 실행한다.

- [ ] **Step 1: 운영 DB 스키마 적용**

```powershell
.\.venv\Scripts\python -c "import os, psycopg; from dotenv import load_dotenv; load_dotenv('.env'); c = psycopg.connect(os.environ['DATABASE_URL'], autocommit=True); c.execute(open('schema.sql', encoding='utf-8').read()); print(c.execute(\"select to_regclass('selection_candidates')\").fetchone())"
```

Expected: `('selection_candidates',)`

- [ ] **Step 2: 첫 실행 (마감 없음)**

`paper_symbols.txt`를 백업한 뒤 실행:

```powershell
Copy-Item paper_symbols.txt paper_symbols.txt.bak
.\.venv\Scripts\python selector.py --no-deadline
```

Expected: 종료 코드 0, 로그 마지막에 `선정 [...]` 또는 `선정 없음`. 1~2시간 걸릴 수 있다(백그라운드가 메모리 부족으로 종료될 수 있으므로 별도 PowerShell 창에서 실행)

- [ ] **Step 3: 결과 확인**

- Gmail에 `[종목선정] YYYY-MM-DD ...` 메일 수신
- `Get-Content paper_symbols.txt`가 메일의 선정 종목과 같음(선정 없음이면 백업과 같음)
- `SELECT status, reason, count(*) FROM selection_candidates WHERE run_date = CURRENT_DATE GROUP BY 1, 2;`의 합계가 메일의 "후보 N"과 같음
- 곧바로 한 번 더 `selector.py --no-deadline` 실행 → 백필이 거의 없이 수 분 안에 끝남, DB 행 수 동일

- [ ] **Step 4: 작업 스케줄러 등록과 다음 영업일 확인**

README "매일 종목 선정"의 등록 명령을 실행하고(사용자 승인 후) 다음을 사용자에게 보고한다:
- 다음 영업일 07:30 자동 실행 → 08:45 전 메일 수신
- 08:55 모의투자가 새 종목으로 시작(`logs/paper-YYYY-MM-DD.log`, 화면 종목 목록)
- 후속: 2단계 재무·밸류에이션(DART) 스펙
