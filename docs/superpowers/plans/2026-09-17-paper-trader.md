# 실시간 모의투자 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 장중 매분 토스 1분봉으로 orb 전략을 가상 체결해 DB에 기록·텔레그램으로 알리고, 로컬 웹 화면에서 실시간 상태를 본다.

**Architecture:** 백테스트 엔진의 체결 규칙을 봉 단위 `DayRunner`로 분리해 백테스트와 모의투자가 같은 코드를 쓴다. `paper.py`는 작업 스케줄러로 08:55에 시작해 오늘 봉으로 따라잡은 뒤 매분 :15초에 끝난 봉을 받아 처리하고, 15:31에 하루치 봉으로 백테스트를 다시 돌려 실시간 결과와 비교한다. `web.py`는 표준 라이브러리 HTTP 서버로 DB를 읽어 `web/`의 화면에 JSON을 준다.

**Tech Stack:** Python 3.11, requests, psycopg 3, python-dotenv, pytest, PostgreSQL 15, 순수 HTML/CSS/JS

**Spec:** `docs/superpowers/specs/2026-09-17-paper-trader-design.md`

## Global Constraints

- 작업 폴더는 `trader/`. 테스트: `.\.venv\Scripts\python -m pytest tests -v` (테스트 DB는 `.env`의 `TEST_DATABASE_URL`)
- 새 의존성 금지: `requests`, `psycopg[binary]`, `python-dotenv`, `pytest`만 사용
- 모든 함수·메서드에 무엇을 하는지 한국어 docstring (사용자 규칙)
- HTML·JS·CSS는 `web/index.html`, `web/app.js`, `web/style.css`로 분리 (사용자 규칙)
- 로그·알림에 토큰·client secret·접속 문자열을 남기지 않는다. 예외는 종류와 `TossError.code`만
- 모의투자 비용 고정값: fee `0.00015`, tax `0.002`, slippage `0.0005`, exit_at `15:15`
- 전략은 `orb` 기본 파라미터, 세션은 정규장(09:00~15:29 시작 봉)
- 모든 시각은 KST(`bars.KST`), 봉 `ts`는 봉 시작 시각
- 커밋 메시지는 한국어로 간결하게, 마지막 줄에 `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`
- 시작 기준선: 기존 테스트 79개 통과

---

### Task 1: `engine.DayRunner`로 체결 규칙 분리

**Files:**
- Modify: `trader/engine.py:37-61` (`run_day`)
- Test: `trader/tests/test_engine.py` (끝에 추가, 기존 테스트 수정 금지)

**Interfaces:**
- Consumes: 기존 `engine._close(symbol, entry_bar, exit_ts, exit_base, reason, costs) -> Trade`, `engine.Costs`, `engine.Trade`
- Produces:
  - `engine.DayRunner(symbol: str, strategy, costs: Costs)`
  - `DayRunner.step(bar: Bar) -> list[tuple[str, Bar | Trade]]` — `("buy", 체결 봉)` 또는 `("sell", Trade)`
  - `DayRunner.finish() -> Trade | None` — 보유 중이면 마지막 봉 종가로 `day_end` 청산
  - 속성 `holding: Bar | None`(진입 체결 봉), `last_bar: Bar | None`(마지막으로 받은 봉, done 이후도 갱신), `done: bool`
  - `engine.run_day` 시그니처·동작 불변

- [ ] **Step 1: 실패하는 테스트 작성**

`trader/tests/test_engine.py` 끝에 추가:

```python
def test_day_runner_step_by_step_matches_run_day():
    """봉을 하나씩 넣은 DayRunner 이벤트가 run_day 거래와 같고, exit_at 이후 봉은 이벤트 없이 last_bar만 갱신한다."""
    prices = [(str(p), str(p)) for p in range(100, 107)]
    bars = bars_from("15:10", prices)
    runner = engine.DayRunner("A", Script({0: "buy"}), NO_COST)
    events = [runner.step(b) for b in bars]
    assert events[1] == [("buy", bars[1])]
    sells = [e for events_of_bar in events for e in events_of_bar if e[0] == "sell"]
    assert [t for _, t in sells] == engine.run_day("A", Script({0: "buy"}), bars, NO_COST)
    assert sells[0][1].exit_reason == "close_time"
    assert events[6] == []
    assert runner.done and runner.holding is None and runner.last_bar == bars[6]
    assert runner.finish() is None


def test_day_runner_finish_closes_at_last_close():
    """exit_at 전에 봉이 끝나면 finish가 마지막 봉 종가로 day_end 청산하고 이후 None을 반환한다."""
    bars = bars_from("14:57", [("100", "100"), ("101", "101"), ("102", "105")])
    runner = engine.DayRunner("A", Script({0: "buy"}), NO_COST)
    for b in bars:
        runner.step(b)
    assert runner.holding == bars[1] and not runner.done
    trade = runner.finish()
    assert (trade.exit_ts, trade.exit_price, trade.exit_reason) == (bars[2].ts, Decimal("105"), "day_end")
    assert runner.holding is None and runner.finish() is None
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests/test_engine.py -v`
Expected: 새 테스트 2개 FAIL (`AttributeError: module 'engine' has no attribute 'DayRunner'`), 기존 9개 PASS

- [ ] **Step 3: 구현**

`trader/engine.py`의 `run_day` 함수 전체(37~61행)를 아래로 바꾼다. `_close`와 `run`은 그대로 둔다.

```python
class DayRunner:
    """한 종목 하루의 체결 규칙을 봉 하나씩 적용한다. run_day와 paper.py가 함께 쓴다."""

    def __init__(self, symbol, strategy, costs):
        """종목·전략 인스턴스·비용을 받고 미보유, 대기 신호 없음 상태로 시작한다."""
        self.symbol = symbol
        self.strategy = strategy
        self.costs = costs
        self.holding = None   # 보유 중이면 체결 봉
        self.pending = None   # 이번 봉 시가에 체결할 신호
        self.last_bar = None
        self.done = False

    def step(self, bar):
        """봉 하나를 처리하고 이 봉에서 일어난 체결 이벤트 목록을 반환한다. ("buy", 체결 봉) 또는 ("sell", Trade)."""
        self.last_bar = bar
        if self.done:
            return []
        c = self.costs
        if bar.ts.time() >= c.exit_at:
            self.done = True
            if self.holding:
                trade = _close(self.symbol, self.holding, bar.ts, bar.open, "close_time", c)
                self.holding = None
                return [("sell", trade)]
            return []
        events = []
        if self.pending == "buy":
            self.holding = bar
            events.append(("buy", bar))
        elif self.pending == "sell":
            events.append(("sell", _close(self.symbol, self.holding, bar.ts, bar.open, "signal", c)))
            self.holding = None
        signal = self.strategy.on_bar(bar, self.holding.open if self.holding else None)
        # 미보유 중 buy, 보유 중 sell만 다음 봉에 체결한다
        if (signal == "buy" and self.holding is None) or (signal == "sell" and self.holding is not None):
            self.pending = signal
        else:
            self.pending = None
        return events

    def finish(self):
        """데이터가 끝났을 때 보유 중이면 마지막 봉 종가로 day_end 청산 Trade를, 아니면 None을 반환한다."""
        if not self.holding:
            return None
        last = self.last_bar
        trade = _close(self.symbol, self.holding, last.ts, last.close, "day_end", self.costs)
        self.holding = None
        self.done = True
        return trade


def run_day(symbol, strategy, bars, costs):
    """한 종목 하루 봉(시각 오름차순)을 전략에 넘기고 체결 규칙에 따라 거래 목록을 만든다."""
    runner = DayRunner(symbol, strategy, costs)
    trades = []
    for bar in bars:
        trades += [item for kind, item in runner.step(bar) if kind == "sell"]
    last = runner.finish()
    return trades + [last] if last else trades
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests -v`
Expected: 81 passed (기존 79 + 새 2)

- [ ] **Step 5: 커밋**

```powershell
git add engine.py tests/test_engine.py
git commit -m @'
백테스트 체결 규칙을 봉 단위 DayRunner로 분리

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
'@
```

---

### Task 2: 토스 최근 봉·장 운영 시간 조회

**Files:**
- Modify: `trader/toss.py` (상수, `_to_bar` 추가, `fetch_day` 변환부, `fetch_recent`·`market_hours` 추가)
- Create: `trader/tests/fixtures/toss_candles_live.json`, `trader/tests/fixtures/toss_calendar_business.json`, `trader/tests/fixtures/toss_calendar_holiday.json`
- Test: `trader/tests/test_toss.py` (끝에 추가)

**Interfaces:**
- Consumes: `TossClient._get(path, params) -> dict`, `TossClient._now() -> datetime`, `bars.Bar`, `bars.KST`
- Produces:
  - `TossClient.fetch_recent(symbol: str, count: int) -> list[Bar]` — 클라이언트 시계 기준 끝난 봉만, 시작 시각 오름차순
  - `TossClient.market_hours(day: date) -> tuple[datetime, datetime] | None` — 정규장 (시작, 종료) KST, 휴장이면 None
  - 형식 오류는 `TossError("BAD_RESPONSE")`

- [ ] **Step 1: 픽스처 작성**

`trader/tests/fixtures/toss_candles_live.json` (2026-09-17 11:48:50 실측 형태, 맨 앞 봉은 진행 중):

```json
{
  "result": {
    "candles": [
      {"timestamp": "2026-09-17T11:49:00.000+09:00", "openPrice": "253000", "highPrice": "253250", "lowPrice": "253000", "closePrice": "253250", "volume": "4077", "currency": "KRW"},
      {"timestamp": "2026-09-17T11:48:00.000+09:00", "openPrice": "253000", "highPrice": "253500", "lowPrice": "253000", "closePrice": "253500", "volume": "14357", "currency": "KRW"},
      {"timestamp": "2026-09-17T11:47:00.000+09:00", "openPrice": "253000", "highPrice": "253000", "lowPrice": "253000", "closePrice": "253000", "volume": "10898", "currency": "KRW"}
    ],
    "nextBefore": "2026-09-17T11:46:00.000+09:00"
  }
}
```

`trader/tests/fixtures/toss_calendar_business.json` (공식 예시 `businessDay` 형태):

```json
{
  "result": {
    "today": {
      "date": "2026-09-17",
      "integrated": {
        "preMarket": {"startTime": "2026-09-17T08:00:00+09:00", "singlePriceAuctionStartTime": "2026-09-17T08:50:00+09:00", "endTime": "2026-09-17T09:00:00+09:00"},
        "regularMarket": {"startTime": "2026-09-17T09:00:00+09:00", "singlePriceAuctionStartTime": "2026-09-17T15:20:00+09:00", "endTime": "2026-09-17T15:30:00+09:00"},
        "afterMarket": {"startTime": "2026-09-17T15:30:00+09:00", "singlePriceAuctionEndTime": "2026-09-17T15:40:00+09:00", "endTime": "2026-09-17T20:00:00+09:00"}
      }
    },
    "previousBusinessDay": {"date": "2026-09-16", "integrated": null},
    "nextBusinessDay": {"date": "2026-09-18", "integrated": null}
  }
}
```

`trader/tests/fixtures/toss_calendar_holiday.json` (공식 예시 `holidayToday` 형태):

```json
{
  "result": {
    "today": {"date": "2026-09-17", "integrated": null},
    "previousBusinessDay": {"date": "2026-09-16", "integrated": null},
    "nextBusinessDay": {"date": "2026-09-18", "integrated": null}
  }
}
```

- [ ] **Step 2: 실패하는 테스트 작성**

`trader/tests/test_toss.py` 끝에 추가 (`json`, `date`, `datetime`, `Decimal`, `pytest`, `KST`, `Bar`, `TossError`, `FakeToss`, `FakeResponse`, `make_client`, `FIXTURES`는 이미 파일에 있음):

```python
LIVE_NOW = datetime(2026, 9, 17, 11, 48, 50, tzinfo=KST)
CAL_DAY = date(2026, 9, 17)


def test_fetch_recent_drops_in_progress_bar(tmp_path):
    """끝나는 시각이 지금보다 늦은 봉은 버리고, 나머지를 봉 시작 시각 오름차순 Bar로 반환한다."""
    body = json.loads((FIXTURES / "toss_candles_live.json").read_text(encoding="utf-8"))
    fake = FakeToss(on_get=lambda params: FakeResponse(body))
    client = make_client(tmp_path, fake)
    client._now = lambda: LIVE_NOW
    bars = client.fetch_recent("005930", 3)
    assert [b.ts for b in bars] == [datetime(2026, 9, 17, 11, 46, tzinfo=KST),
                                    datetime(2026, 9, 17, 11, 47, tzinfo=KST)]
    assert bars[-1] == Bar(datetime(2026, 9, 17, 11, 47, tzinfo=KST), Decimal("253000"),
                           Decimal("253500"), Decimal("253000"), Decimal("253500"), 14357)
    _, url, kwargs = fake.gets()[0]
    assert url == "https://toss.test/api/v1/candles"
    assert kwargs["params"] == {"symbol": "005930", "interval": "1m", "count": 3, "adjusted": "false"}


def test_fetch_recent_keeps_bar_ending_exactly_now(tmp_path):
    """끝나는 시각이 지금과 같으면 끝난 봉으로 본다."""
    body = json.loads((FIXTURES / "toss_candles_live.json").read_text(encoding="utf-8"))
    client = make_client(tmp_path, FakeToss(on_get=lambda params: FakeResponse(body)))
    client._now = lambda: datetime(2026, 9, 17, 11, 49, tzinfo=KST)
    assert len(client.fetch_recent("005930", 3)) == 3


def test_fetch_recent_rejects_body_without_candles(tmp_path):
    """result.candles가 없으면 BAD_RESPONSE 예외를 낸다."""
    client = make_client(tmp_path, FakeToss(on_get=lambda params: FakeResponse({"result": {}})))
    with pytest.raises(TossError) as e:
        client.fetch_recent("005930", 3)
    assert e.value.code == "BAD_RESPONSE"


def calendar(name):
    """장 운영 정보 픽스처를 새 dict로 읽는다."""
    return json.loads((FIXTURES / f"toss_calendar_{name}.json").read_text(encoding="utf-8"))


def test_market_hours_returns_regular_session(tmp_path):
    """영업일이면 정규장 시작·종료 시각을 KST datetime으로 반환한다."""
    fake = FakeToss(on_get=lambda params: FakeResponse(calendar("business")))
    assert make_client(tmp_path, fake).market_hours(CAL_DAY) == (
        datetime(2026, 9, 17, 9, 0, tzinfo=KST), datetime(2026, 9, 17, 15, 30, tzinfo=KST))
    _, url, kwargs = fake.gets()[0]
    assert url == "https://toss.test/api/v1/market-calendar/KR"
    assert kwargs["params"] == {"date": "2026-09-17"}


def holiday_bodies():
    """휴장으로 봐야 하는 응답: integrated null, regularMarket null, today가 다른 날."""
    no_regular = calendar("business")
    no_regular["result"]["today"]["integrated"]["regularMarket"] = None
    other_day = calendar("business")
    other_day["result"]["today"]["date"] = "2026-09-18"
    return [calendar("holiday"), no_regular, other_day]


@pytest.mark.parametrize("body", holiday_bodies())
def test_market_hours_returns_none_on_holiday(tmp_path, body):
    """휴장 응답이면 None을 반환한다."""
    fake = FakeToss(on_get=lambda params: FakeResponse(body))
    assert make_client(tmp_path, fake).market_hours(CAL_DAY) is None


def test_market_hours_rejects_bad_body(tmp_path):
    """result.today가 없으면 BAD_RESPONSE 예외를 낸다."""
    client = make_client(tmp_path, FakeToss(on_get=lambda params: FakeResponse({"result": {}})))
    with pytest.raises(TossError) as e:
        client.market_hours(CAL_DAY)
    assert e.value.code == "BAD_RESPONSE"
```

- [ ] **Step 3: 테스트 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests/test_toss.py -v`
Expected: 새 테스트 8개 FAIL (`AttributeError: 'TossClient' object has no attribute 'fetch_recent'` / `'market_hours'`), 기존 테스트 PASS

- [ ] **Step 4: 구현**

`trader/toss.py` 상수에 추가 (`CANDLES_PATH` 아래):

```python
MARKET_CALENDAR_PATH = "/api/v1/market-calendar/KR"
```

`_retry_after` 함수 아래에 추가:

```python
def _to_bar(candle):
    """캔들 한 개를 봉 시작 시각(끝나는 시각 − 1분)·Decimal 가격·int 거래량의 Bar로 바꾼다."""
    end = datetime.fromisoformat(candle["timestamp"]).astimezone(KST)
    return Bar(end - timedelta(minutes=1), Decimal(candle["openPrice"]), Decimal(candle["highPrice"]),
               Decimal(candle["lowPrice"]), Decimal(candle["closePrice"]), int(Decimal(candle["volume"])))
```

`fetch_day`의 캔들 반복부를 `_to_bar`를 쓰도록 바꾼다:

```python
            reached_prev_day = False
            for c in candles:
                bar = _to_bar(c)
                end_date = (bar.ts + timedelta(minutes=1)).date()
                if end_date < day:
                    reached_prev_day = True
                elif end_date == day:
                    bars[bar.ts] = bar
```

`fetch_day` 아래에 메서드 추가:

```python
    def fetch_recent(self, symbol, count):
        """최근 1분봉 count개를 받아 클라이언트 시계 기준 끝난 봉만 시작 시각 오름차순으로 반환한다."""
        body = self._get(CANDLES_PATH, {"symbol": symbol, "interval": "1m", "count": count,
                                        "adjusted": "false"})
        try:
            bars = [_to_bar(c) for c in body["result"]["candles"]]
        except (KeyError, TypeError, AttributeError):
            raise TossError("BAD_RESPONSE") from None
        now = self._now()
        return sorted((b for b in bars if b.ts + timedelta(minutes=1) <= now), key=lambda b: b.ts)

    def market_hours(self, day: date):
        """day의 정규장 (시작, 종료) KST 시각을 반환한다. 휴장이면 None."""
        body = self._get(MARKET_CALENDAR_PATH, {"date": day.isoformat()})
        try:
            today = body["result"]["today"]
            regular = (today["integrated"] or {}).get("regularMarket")
            if today["date"] != day.isoformat() or not regular:
                return None
            return (datetime.fromisoformat(regular["startTime"]).astimezone(KST),
                    datetime.fromisoformat(regular["endTime"]).astimezone(KST))
        except (KeyError, TypeError, AttributeError, ValueError):
            raise TossError("BAD_RESPONSE") from None
```

모듈 docstring을 `"""토스증권 Open API 클라이언트: 토큰 캐시, 호출 간격 제한, 재시도, 1분봉·장 운영 시간 조회."""`로 바꾼다.

- [ ] **Step 5: 테스트 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests -v`
Expected: 89 passed

- [ ] **Step 6: 커밋**

```powershell
git add toss.py tests/test_toss.py tests/fixtures/toss_candles_live.json tests/fixtures/toss_calendar_business.json tests/fixtures/toss_calendar_holiday.json
git commit -m @'
토스 최근 끝난 봉과 정규장 시간 조회 추가

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
'@
```

---

### Task 3: 모의투자 테이블과 저장소

**Files:**
- Modify: `trader/schema.sql` (끝에 추가), `trader/store.py`, `trader/tests/conftest.py:20`
- Test: `trader/tests/test_store.py` (끝에 추가)

**Interfaces:**
- Consumes: `engine.Trade(symbol, entry_ts, entry_price, exit_ts, exit_price, return_pct, exit_reason)`
- Produces:
  - `store.save_paper_status(conn, row: dict) -> None` — row 키: `symbol, trade_date, last_bar_ts, last_close, qty, entry_ts, entry_price`
  - `store.save_paper_trade(conn, strategy: str, qty: int, trade: Trade, pnl_krw: Decimal) -> None`
  - `store.load_paper_status(conn) -> list[dict]` — 위 키 + `updated_at`, symbol 오름차순
  - `store.load_paper_trades(conn, day: date) -> list[dict]` — 키: `symbol, strategy, qty, entry_ts, entry_price, exit_ts, exit_price, pnl_krw, return_pct, exit_reason`, KST 청산일이 day, exit_ts 오름차순
  - `store.load_paper_daily(conn) -> list[dict]` — 키: `day, trades, wins, pnl_krw`, 최신순

- [ ] **Step 1: 실패하는 테스트 작성**

`trader/tests/conftest.py`의 TRUNCATE 줄을 바꾼다:

```python
        c.execute("TRUNCATE minute_bars, collect_runs, backtest_runs, paper_status, paper_trades CASCADE")
```

`trader/tests/test_store.py` 상단 import에 `timedelta`를 추가한다(`from datetime import date, datetime, timedelta`). 끝에 추가:

```python
def paper_status_row(**changes):
    """미보유 상태의 paper_status 행 dict를 만들고 changes로 덮어쓴다."""
    row = {"symbol": "A", "trade_date": date(2026, 9, 17),
           "last_bar_ts": datetime(2026, 9, 17, 9, 0, tzinfo=KST), "last_close": Decimal("100"),
           "qty": 0, "entry_ts": None, "entry_price": None}
    return {**row, **changes}


def test_schema_creates_paper_tables(conn):
    """스키마 적용 후 모의투자 테이블 두 개가 존재한다."""
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    ).fetchall()
    assert {"paper_status", "paper_trades"} <= {r[0] for r in rows}


def test_save_paper_status_upserts(conn):
    """같은 종목 상태를 다시 저장하면 덮어쓰고 updated_at이 채워진다."""
    store.save_paper_status(conn, paper_status_row())
    row = paper_status_row(qty=10, last_close=Decimal("104"),
                           entry_ts=datetime(2026, 9, 17, 9, 41, tzinfo=KST), entry_price=Decimal("101.0505"))
    store.save_paper_status(conn, row)
    [got] = store.load_paper_status(conn)
    assert {k: got[k] for k in row} == row
    assert got["updated_at"] is not None


def paper_trade(symbol, exit_ts):
    """exit_ts에 청산한 20분짜리 모의 거래를 만든다."""
    return engine.Trade(symbol, exit_ts - timedelta(minutes=20), Decimal("101.0505"), exit_ts,
                        Decimal("103.948"), Decimal("2.63"), "signal")


def test_save_paper_trade_overwrites_same_entry(conn):
    """같은 (종목, 진입 시각) 거래를 다시 저장하면 덮어쓴다."""
    t = paper_trade("A", datetime(2026, 9, 17, 10, 1, tzinfo=KST))
    store.save_paper_trade(conn, "orb", 10, t, Decimal("100"))
    store.save_paper_trade(conn, "orb", 12, t, Decimal("120"))
    [got] = store.load_paper_trades(conn, date(2026, 9, 17))
    assert (got["symbol"], got["strategy"], got["qty"], got["pnl_krw"], got["exit_reason"]) == (
        "A", "orb", 12, Decimal("120"), "signal")
    assert (got["entry_ts"], got["entry_price"], got["exit_price"], got["return_pct"]) == (
        t.entry_ts, Decimal("101.0505"), Decimal("103.948"), Decimal("2.63"))


def test_load_paper_trades_and_daily_use_kst_dates(conn):
    """거래 조회와 일별 합계는 청산 시각의 KST 날짜로 묶는다(UTC 전날 15:30 = KST 00:30)."""
    store.save_paper_trade(conn, "orb", 1, paper_trade("A", datetime(2026, 9, 16, 15, 0, tzinfo=KST)), Decimal("-50"))
    store.save_paper_trade(conn, "orb", 1, paper_trade("A", datetime(2026, 9, 17, 0, 30, tzinfo=KST)), Decimal("100"))
    store.save_paper_trade(conn, "orb", 1, paper_trade("B", datetime(2026, 9, 17, 10, 1, tzinfo=KST)), Decimal("-30"))
    assert [(r["symbol"], r["pnl_krw"]) for r in store.load_paper_trades(conn, date(2026, 9, 17))] == [
        ("A", Decimal("100")), ("B", Decimal("-30"))]
    assert store.load_paper_daily(conn) == [
        {"day": date(2026, 9, 17), "trades": 2, "wins": 1, "pnl_krw": Decimal("70")},
        {"day": date(2026, 9, 16), "trades": 1, "wins": 0, "pnl_krw": Decimal("-50")},
    ]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests/test_store.py -v`
Expected: 모든 DB 테스트가 conftest의 TRUNCATE에서 ERROR (`relation "paper_status" does not exist`)

- [ ] **Step 3: 구현**

`trader/schema.sql` 끝에 추가:

```sql
-- 종목별 모의투자 현재 상태. paper.py가 새 봉을 처리할 때마다 덮어쓴다
CREATE TABLE IF NOT EXISTS paper_status (
    symbol       text        PRIMARY KEY,
    trade_date   date        NOT NULL,
    last_bar_ts  timestamptz,           -- 마지막으로 받은 봉 시작 시각
    last_close   numeric,
    qty          int         NOT NULL,  -- 0이면 미보유
    entry_ts     timestamptz,           -- 진입 체결 봉 시각
    entry_price  numeric,               -- 슬리피지 반영 매수가
    updated_at   timestamptz NOT NULL DEFAULT now()
);

-- 완결된 모의 거래. 재시작 따라잡기로 같은 거래가 다시 오면 덮어쓴다
CREATE TABLE IF NOT EXISTS paper_trades (
    symbol       text        NOT NULL,
    strategy     text        NOT NULL,
    entry_ts     timestamptz NOT NULL,
    qty          int         NOT NULL,
    entry_price  numeric     NOT NULL,  -- 슬리피지 반영 매수가
    exit_ts      timestamptz NOT NULL,
    exit_price   numeric     NOT NULL,  -- 슬리피지 반영 매도가
    pnl_krw      numeric     NOT NULL,  -- qty × (exit_price × (1 − fee − tax) − entry_price × (1 + fee))
    return_pct   numeric     NOT NULL,  -- 비용 반영 수익률(%)
    exit_reason  text        NOT NULL CHECK (exit_reason IN ('signal', 'close_time', 'day_end')),
    PRIMARY KEY (symbol, entry_ts)
);
```

`trader/store.py`: 모듈 docstring을 `"""minute_bars·collect_runs·backtest·paper 테이블 저장과 조회."""`로 바꾸고, import에 `from psycopg.rows import dict_row`를 추가한 뒤 끝에 추가:

```python
def save_paper_status(conn, row):
    """종목별 모의투자 상태를 symbol 기준으로 덮어쓴다. row는 updated_at을 뺀 paper_status 컬럼 dict."""
    conn.execute(
        "INSERT INTO paper_status (symbol, trade_date, last_bar_ts, last_close, qty, entry_ts, entry_price) "
        "VALUES (%(symbol)s, %(trade_date)s, %(last_bar_ts)s, %(last_close)s, %(qty)s, %(entry_ts)s, "
        "%(entry_price)s) "
        "ON CONFLICT (symbol) DO UPDATE SET trade_date = EXCLUDED.trade_date, "
        "last_bar_ts = EXCLUDED.last_bar_ts, last_close = EXCLUDED.last_close, qty = EXCLUDED.qty, "
        "entry_ts = EXCLUDED.entry_ts, entry_price = EXCLUDED.entry_price, updated_at = now()",
        row,
    )


def save_paper_trade(conn, strategy, qty, trade, pnl_krw):
    """완결된 모의 거래를 저장하고, 같은 (종목, 진입 시각)이 있으면 덮어쓴다."""
    conn.execute(
        "INSERT INTO paper_trades (symbol, strategy, entry_ts, qty, entry_price, exit_ts, exit_price, "
        "pnl_krw, return_pct, exit_reason) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (symbol, entry_ts) DO UPDATE SET strategy = EXCLUDED.strategy, qty = EXCLUDED.qty, "
        "entry_price = EXCLUDED.entry_price, exit_ts = EXCLUDED.exit_ts, exit_price = EXCLUDED.exit_price, "
        "pnl_krw = EXCLUDED.pnl_krw, return_pct = EXCLUDED.return_pct, exit_reason = EXCLUDED.exit_reason",
        (trade.symbol, strategy, trade.entry_ts, qty, trade.entry_price, trade.exit_ts, trade.exit_price,
         pnl_krw, trade.return_pct, trade.exit_reason),
    )


def load_paper_status(conn):
    """모의투자 종목별 상태 전체를 symbol 오름차순 dict 목록으로 반환한다."""
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "SELECT symbol, trade_date, last_bar_ts, last_close, qty, entry_ts, entry_price, updated_at "
            "FROM paper_status ORDER BY symbol"
        ).fetchall()


def load_paper_trades(conn, day):
    """청산 시각의 KST 날짜가 day인 모의 거래를 청산 시각 오름차순 dict 목록으로 반환한다."""
    start = datetime.combine(day, time(0), KST)
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "SELECT symbol, strategy, qty, entry_ts, entry_price, exit_ts, exit_price, pnl_krw, return_pct, "
            "exit_reason FROM paper_trades WHERE exit_ts >= %s AND exit_ts < %s ORDER BY exit_ts, symbol",
            (start, start + timedelta(days=1)),
        ).fetchall()


def load_paper_daily(conn):
    """KST 청산일별 거래 수·수익 거래 수·원화 손익 합계를 최신 날짜부터 반환한다."""
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "SELECT (exit_ts AT TIME ZONE 'Asia/Seoul')::date AS day, count(*)::int AS trades, "
            "(count(*) FILTER (WHERE pnl_krw > 0))::int AS wins, sum(pnl_krw) AS pnl_krw "
            "FROM paper_trades GROUP BY 1 ORDER BY 1 DESC"
        ).fetchall()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests -v`
Expected: 93 passed

- [ ] **Step 5: 커밋**

```powershell
git add schema.sql store.py tests/conftest.py tests/test_store.py
git commit -m @'
모의투자 상태·거래 테이블과 저장 함수 추가

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
'@
```

---

### Task 4: `paper.run` 하루 실행 루프

**Files:**
- Create: `trader/paper.py`
- Test: `trader/tests/test_paper.py`

**Interfaces:**
- Consumes:
  - `engine.DayRunner`, `engine.run_day`, `engine.Costs`, `engine.Trade` (Task 1)
  - `client.market_hours(day) -> (datetime, datetime) | None`, `client.fetch_recent(symbol, count) -> list[Bar]`, `client.fetch_day(symbol, day) -> list[Bar]` (Task 2, `fetch_day`는 진행 중인 봉을 포함할 수 있음)
  - `store.save_paper_status`, `store.save_paper_trade` (Task 3)
  - `backtest.REGULAR_OPEN = time(9, 0)`, `backtest.REGULAR_CLOSE = time(15, 30)`, `strategies.Orb`, `notify.send_telegram`
- Produces:
  - `paper.COSTS: engine.Costs` (Task 6 `web.py`가 slippage를 씀)
  - `paper.run(client, conn, symbols: list[str], capital: Decimal, now=..., sleep=...) -> int`
  - `paper.pnl_krw(qty, trade, costs) -> Decimal`, `paper.regular(bars, now) -> list[Bar]`, `paper.next_fetch_at(now) -> datetime`
  - `paper.REQUIRED_ENV`, `paper.read_symbols`(collector에서 import), `paper.TossClient`(toss에서 import) — Task 5 테스트가 monkeypatch

- [ ] **Step 1: 실패하는 테스트 작성**

`trader/tests/test_paper.py`:

```python
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest

import engine
import paper
from bars import KST, Bar
from toss import TossAuthError, TossError

TODAY = date(2026, 9, 17)


def at(hh_mm, second=0):
    """오늘 HH:MM:SS KST 시각."""
    h, m = map(int, hh_mm.split(":"))
    return datetime(2026, 9, 17, h, m, second, tzinfo=KST)


REGULAR = (at("09:00"), at("15:30"))


def day_bars(price_at):
    """08:50~15:40 시작 1분봉 411개. price_at(time)이 시가=고가=저가=종가."""
    out = []
    for i in range(411):
        ts = at("08:50") + timedelta(minutes=i)
        p = Decimal(price_at(ts.time()))
        out.append(Bar(ts, p, p, p, p, 100))
    return out


def breakout(t):
    """09:40에 범위 고가 100을 101로 돌파(09:41 체결)하고, 10:00에 104로 +2% 목표에 닿는다(10:01 청산)."""
    if t < time(9, 40):
        return "100"
    return "101" if t < time(10, 0) else "104"


def flat(t):
    """하루 종일 100."""
    return "100"


class Clock:
    """가짜 시계. sleep한 만큼 시간이 흐른다."""

    def __init__(self, start):
        """시작 시각을 받는다."""
        self.t = start

    def now(self):
        """현재 가짜 시각을 반환한다."""
        return self.t

    def sleep(self, seconds):
        """음수 대기가 없는지 확인하고 시간을 흘린다."""
        assert seconds >= 0
        self.t += timedelta(seconds=seconds)


class FakeClient:
    """TossClient 대역. fetch_recent는 끝난 봉만, fetch_day는 진행 중인 봉까지 준다."""

    def __init__(self, clock, days, hours=REGULAR, fail=None, final=None):
        """시계, {종목: 봉}, 장 시간(또는 예외), 실패 규칙 fail(kind, symbol, now), 15:31 이후 fetch_day용 봉을 받는다."""
        self.clock = clock
        self.days = days
        self.hours = hours
        self.fail = fail or (lambda kind, symbol, now: None)
        self.final = final
        self.calls = []

    def _call(self, kind, symbol):
        """호출을 기록하고, 실패 규칙이 예외를 주면 던진다. 현재 시각을 반환한다."""
        now = self.clock.now()
        self.calls.append((kind, symbol))
        error = self.fail(kind, symbol, now)
        if error:
            raise error
        return now

    def market_hours(self, day):
        """준비된 장 시간을 반환하거나 예외를 던진다."""
        if isinstance(self.hours, Exception):
            raise self.hours
        return self.hours

    def fetch_recent(self, symbol, count):
        """지금 이전에 끝난 봉 중 최근 count개를 반환한다."""
        now = self._call("recent", symbol)
        return [b for b in self.days[symbol] if b.ts + timedelta(minutes=1) <= now][-count:]

    def fetch_day(self, symbol, day):
        """지금까지 시작한 봉 전부(진행 중인 봉 포함)를 반환한다. 15:31 이후 final이 있으면 그 봉을 쓴다."""
        now = self._call("day", symbol)
        days = self.final if self.final and now.time() >= time(15, 31) else self.days
        return [b for b in days[symbol] if b.ts <= now]

    def count(self, kind, symbol):
        """종류·종목별 호출 수를 반환한다."""
        return self.calls.count((kind, symbol))


@pytest.fixture
def sent(monkeypatch):
    """텔레그램 전송 대신 메시지를 목록에 기록한다."""
    messages = []
    monkeypatch.setattr(paper.notify, "send_telegram", messages.append)
    return messages


def run_from(conn, start, days, capital="1000000", **client_kwargs):
    """start부터 가짜 시계로 paper.run을 돌려 (종료 코드, 클라이언트, 끝난 시각)을 반환한다."""
    clock = Clock(start)
    client = FakeClient(clock, days, **client_kwargs)
    code = paper.run(client, conn, list(days), Decimal(capital), now=clock.now, sleep=clock.sleep)
    return code, client, clock.now()


def saved_trades(conn):
    """paper_trades를 진입 시각 순으로 조회한다."""
    return conn.execute(
        "SELECT symbol, qty, entry_ts, entry_price, exit_ts, exit_price, pnl_krw, exit_reason "
        "FROM paper_trades ORDER BY entry_ts"
    ).fetchall()


SUMMARY_MATCH = ("[모의투자] 2026-09-17 요약\n거래 1건 (승 1 / 패 0) 손익 +13,156원\n"
                 "A 1건 +13,156원\n마감 비교: 일치")


def test_pnl_krw_applies_fee_and_tax():
    """원화 손익 = 수량 × (매도가 × (1 − 수수료 − 세금) − 매수가 × (1 + 수수료))."""
    costs = engine.Costs(Decimal("0.001"), Decimal("0.002"), Decimal("0"), time(15, 15))
    trade = engine.Trade("A", at("09:00"), Decimal("10000"), at("10:00"), Decimal("10100"), Decimal("0"), "signal")
    assert paper.pnl_krw(10, trade, costs) == Decimal("597.000")


def test_regular_keeps_finished_regular_session_bars():
    """09:00~15:29 시작 봉 중 now 이전에 끝난 봉만 남긴다."""
    bars = [b for b in day_bars(flat) if b.ts.time() in (time(8, 59), time(9, 0), time(15, 29), time(15, 30))]
    assert [b.ts.time() for b in paper.regular(bars, at("15:30"))] == [time(9, 0), time(15, 29)]
    assert [b.ts.time() for b in paper.regular(bars, at("15:29", 59))] == [time(9, 0)]


@pytest.mark.parametrize("now, expected", [
    (at("09:00", 10), at("09:00", 15)),
    (at("09:00", 15), at("09:01", 15)),
    (at("09:00", 59), at("09:01", 15)),
])
def test_next_fetch_at_is_next_15_seconds(now, expected):
    """다음 조회 시각은 now보다 늦은 가장 가까운 매분 15초다."""
    assert paper.next_fetch_at(now) == expected


def test_holiday_returns_0_without_fetching(conn, sent):
    """휴장일이면 봉을 조회하지 않고 알림 없이 0을 반환한다."""
    code, client, _ = run_from(conn, at("08:55"), {"A": day_bars(breakout)}, hours=None)
    assert code == 0 and client.calls == [] and sent == []


def test_irregular_hours_alerts_and_skips(conn, sent):
    """정규장이 09:00~15:30이 아니면 알리고 거래 없이 0을 반환한다."""
    code, client, _ = run_from(conn, at("08:55"), {"A": day_bars(breakout)}, hours=(at("10:00"), at("16:30")))
    assert code == 0 and client.calls == []
    assert sent == ["[모의투자] 2026-09-17 정규장 시간 변경일(10:00~16:30), 모의투자 안 함"]


def test_full_day_saves_trade_status_and_matches_backtest(conn, sent):
    """08:55부터 돌면 백테스트와 같은 거래를 저장·알리고, 15:31에 비교 일치 요약을 보낸다."""
    code, client, end = run_from(conn, at("08:55"), {"A": day_bars(breakout), "B": day_bars(flat)})
    assert code == 0 and end == at("15:31")
    assert saved_trades(conn) == [("A", 4948, at("09:41"), Decimal("101.0505"), at("10:01"),
                                   Decimal("103.9480"), Decimal("13156.010705300"), "signal")]
    assert sent == [
        "[모의투자] 2026-09-17 시작(따라잡기 완료): 2종목, 보유 0종목, 거래 0건",
        "[모의투자] 매수 A 4948주 @ 101원 (09:41)",
        "[모의투자] 매도 A 4948주 @ 104원 (10:01) 손익 +13,156원 (+2.63%)",
        SUMMARY_MATCH,
    ]
    status = conn.execute("SELECT symbol, last_bar_ts, last_close, qty, entry_ts FROM paper_status ORDER BY symbol").fetchall()
    assert status == [("A", at("15:29"), Decimal("104"), 0, None), ("B", at("15:29"), Decimal("100"), 0, None)]
    assert client.count("day", "A") == 2  # 시작 따라잡기 + 마감 비교, 빈 분 없음


def test_status_shows_holding_during_trade(conn, sent):
    """보유 중에 끝나면(09:50 시작 → 따라잡기 직후) 상태에 수량·진입 정보가 남는다."""
    clock = Clock(at("09:50"))
    client = FakeClient(clock, {"A": day_bars(breakout)})
    p = paper.Paper(conn, ["A"], Decimal("500000"), TODAY)
    p.poll(client, "A", clock.now(), catch_up=True)
    [row] = conn.execute("SELECT qty, entry_ts, entry_price, last_bar_ts FROM paper_status").fetchall()
    assert row == (4948, at("09:41"), Decimal("101.0505"), at("09:49"))
    assert sent == []


def test_restart_catches_up_without_duplicates_or_trade_alerts(conn, sent):
    """10:30에 두 번 시작해도 거래는 1건이고, 따라잡기 중 체결은 알리지 않는다."""
    for _ in range(2):
        code, _, _ = run_from(conn, at("10:30"), {"A": day_bars(breakout), "B": day_bars(flat)})
        assert code == 0
    assert len(saved_trades(conn)) == 1
    assert not any("매수" in m or "매도" in m for m in sent)
    assert sent[0] == "[모의투자] 2026-09-17 시작(따라잡기 완료): 2종목, 보유 0종목, 거래 1건"
    assert sent[1] == SUMMARY_MATCH


def test_zero_quantity_does_not_save_trade(conn, sent):
    """종목당 금액으로 1주도 못 사면 거래를 저장·알리지 않고, 마감 비교는 일치한다."""
    code, _, _ = run_from(conn, at("08:55"), {"A": day_bars(breakout)}, capital="100")
    assert code == 0 and saved_trades(conn) == []
    assert sent[-1] == "[모의투자] 2026-09-17 요약\n거래 0건 (승 0 / 패 0) 손익 +0원\n마감 비교: 일치"


def test_fetch_failures_alert_once_and_gap_is_filled(conn, sent):
    """09:38~09:44 조회가 7분 실패하면 5번째에 한 번 알리고, 복구 후 빈 분을 하루치 조회로 채워 같은 거래를 낸다."""
    def fail(kind, symbol, now):
        """A의 최근 봉 조회만 09:38~09:44에 실패시킨다."""
        if kind == "recent" and symbol == "A" and time(9, 38) <= now.time() < time(9, 45):
            return TossError("HTTP500")
        return None

    code, client, _ = run_from(conn, at("08:55"), {"A": day_bars(breakout)}, capital="500000", fail=fail)
    assert code == 0
    assert [m for m in sent if "실패" in m] == ["[모의투자] A 연속 5분 실패: TossError"]
    assert client.count("day", "A") == 3  # 시작 따라잡기 + 빈 분 + 마감 비교
    assert len(saved_trades(conn)) == 1
    assert sent[-1] == SUMMARY_MATCH


def test_close_reports_mismatch_when_final_bars_differ(conn, sent):
    """마감 때 받은 봉이 실시간과 달라 거래가 다르면 요약에 불일치를 적는다."""
    code, _, _ = run_from(conn, at("08:55"), {"A": day_bars(breakout)}, final={"A": day_bars(flat)})
    assert code == 0
    assert sent[-1].endswith("마감 비교: A 불일치: 실시간 1건 / 마감 0건")


def test_auth_error_propagates(conn, sent):
    """인증 오류는 run 밖으로 올린다."""
    with pytest.raises(TossAuthError):
        run_from(conn, at("08:55"), {"A": day_bars(breakout)}, hours=TossAuthError("access_denied"))
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests/test_paper.py -v`
Expected: 수집 단계 ERROR (`ModuleNotFoundError: No module named 'paper'`)

- [ ] **Step 3: 구현**

`trader/paper.py`:

```python
"""장중 모의투자 진입점. 작업 스케줄러가 평일 08:55에 실행하고, 매분 끝난 1분봉으로 orb 전략을 가상 체결한다."""
import logging
import time as _time
from datetime import datetime, time, timedelta
from decimal import Decimal

import engine
import notify
import store
from backtest import REGULAR_CLOSE, REGULAR_OPEN
from bars import KST
from collector import read_symbols  # noqa: F401  main에서 사용
from strategies import Orb
from toss import TossAuthError, TossClient, TossError  # noqa: F401  TossClient는 main에서 사용

COSTS = engine.Costs(Decimal("0.00015"), Decimal("0.002"), Decimal("0.0005"), time(15, 15))
FETCH_SECOND = 15
RECENT_COUNT = 5
LOOP_END = time(15, 31)
ALERT_AFTER_FAILS = 5

log = logging.getLogger("paper")


def pnl_krw(qty, trade, costs):
    """수량과 거래로 수수료·세금을 반영한 원화 손익을 계산한다."""
    return qty * (trade.exit_price * (1 - costs.fee - costs.tax) - trade.entry_price * (1 + costs.fee))


def regular(bars, now):
    """정규장(09:00~15:29 시작) 봉 중 now 이전에 끝난 봉만 남긴다."""
    return [b for b in bars
            if REGULAR_OPEN <= b.ts.time() < REGULAR_CLOSE and b.ts + timedelta(minutes=1) <= now]


def next_fetch_at(now):
    """now보다 늦은 가장 가까운 매분 FETCH_SECOND초 시각을 반환한다."""
    at = now.replace(second=FETCH_SECOND, microsecond=0)
    return at if at > now else at + timedelta(minutes=1)


def trade_key(trade):
    """마감 비교에 쓰는 거래 값(진입·청산 시각과 가격, 청산 사유)."""
    return (trade.entry_ts, trade.entry_price, trade.exit_ts, trade.exit_price, trade.exit_reason)


class Paper:
    """종목별 DayRunner와 보유 수량을 들고, 봉을 처리해 거래·상태를 저장하고 체결을 알린다."""

    def __init__(self, conn, symbols, capital, today):
        """종목마다 새 orb 전략의 DayRunner를 만들고 종목당 금액을 정한다."""
        self.conn = conn
        self.today = today
        self.budget = capital / len(symbols)
        self.runners = {s: engine.DayRunner(s, Orb({}), COSTS) for s in symbols}
        self.qty = dict.fromkeys(symbols, 0)
        self.trades = {s: [] for s in symbols}  # 전략 기준 거래 전체(0주 포함), 마감 비교용
        self.saved = []                           # 저장한 거래의 (종목, 원화 손익)
        self.fails = dict.fromkeys(symbols, 0)

    def poll(self, client, symbol, now, catch_up=False):
        """끝난 봉을 받아 처리한다. 따라잡기거나 빈 분이 있으면 하루치를 받는다.
        인증 오류는 올리고, 그 밖의 오류는 로그만 남기며 연속 5회째에 한 번 알린다."""
        try:
            bars = [] if catch_up else regular(client.fetch_recent(symbol, RECENT_COUNT), now)
            if catch_up or self._has_gap(symbol, bars):
                bars = regular(client.fetch_day(symbol, self.today), now)
            # ponytail: 처리 중 DB 저장이 실패하면 runner는 앞서 나가고 그 거래는 저장되지 않는다. 재시작 따라잡기로 복구
            self.process(symbol, bars, alert=not catch_up)
            self.fails[symbol] = 0
        except TossAuthError:
            raise
        except Exception as e:
            self.fails[symbol] += 1
            log.error("%s 조회·처리 실패 %d회: %s %s", symbol, self.fails[symbol], type(e).__name__,
                      e.code if isinstance(e, TossError) else "")
            if self.fails[symbol] == ALERT_AFTER_FAILS:
                notify.send_telegram(f"[모의투자] {symbol} 연속 {ALERT_AFTER_FAILS}분 실패: {type(e).__name__}")

    def _new(self, symbol, bars):
        """마지막으로 받은 봉 이후의 봉만 반환한다."""
        last = self.runners[symbol].last_bar
        return [b for b in bars if last is None or b.ts > last.ts]

    def _has_gap(self, symbol, bars):
        """새 봉의 첫 봉이 마지막 봉 바로 다음(받은 봉이 없으면 09:00)이 아니면 True."""
        new = self._new(symbol, bars)
        if not new:
            return False
        last = self.runners[symbol].last_bar
        expected = last.ts + timedelta(minutes=1) if last else datetime.combine(self.today, REGULAR_OPEN, KST)
        return new[0].ts != expected

    def process(self, symbol, bars, alert=True):
        """새 봉을 DayRunner에 넣어 체결을 처리하고, 새 봉이 있었으면 상태를 저장한다."""
        new = self._new(symbol, bars)
        for bar in new:
            for kind, item in self.runners[symbol].step(bar):
                if kind == "buy":
                    self._buy(symbol, item, alert)
                else:
                    self._sell(symbol, item, alert)
        if new:
            store.save_paper_status(self.conn, self.status_row(symbol))

    def _buy(self, symbol, bar, alert):
        """진입 체결: 종목당 금액으로 살 수 있는 정수 주를 정한다. 0주면 거래하지 않는다."""
        price = bar.open * (1 + COSTS.slippage)
        qty = int(self.budget // price)
        self.qty[symbol] = qty
        if qty == 0:
            log.warning("%s %s 금액 부족으로 매수 안 함 (매수가 %s, 종목당 %s)", symbol, bar.ts, price, self.budget)
            return
        log.info("%s %s 매수 %d주 @ %s", symbol, bar.ts, qty, price)
        if alert:
            notify.send_telegram(f"[모의투자] 매수 {symbol} {qty}주 @ {price:,.0f}원 ({bar.ts:%H:%M})")

    def _sell(self, symbol, trade, alert):
        """청산 체결: 비교용 거래에 더하고, 수량이 있으면 원화 손익과 함께 저장·알린다."""
        self.trades[symbol].append(trade)
        qty, self.qty[symbol] = self.qty[symbol], 0
        if qty == 0:
            return
        pnl = pnl_krw(qty, trade, COSTS)
        store.save_paper_trade(self.conn, Orb.name, qty, trade, pnl)
        self.saved.append((symbol, pnl))
        log.info("%s %s 매도 %d주 @ %s 손익 %s", symbol, trade.exit_ts, qty, trade.exit_price, pnl)
        if alert:
            notify.send_telegram(
                f"[모의투자] 매도 {symbol} {qty}주 @ {trade.exit_price:,.0f}원 ({trade.exit_ts:%H:%M}) "
                f"손익 {pnl:+,.0f}원 ({trade.return_pct:+.2f}%)")

    def status_row(self, symbol):
        """paper_status에 저장할 현재 상태 dict를 만든다. 0주 진입은 미보유로 본다."""
        runner = self.runners[symbol]
        last, holding, qty = runner.last_bar, runner.holding, self.qty[symbol]
        held = holding is not None and qty > 0
        return {
            "symbol": symbol, "trade_date": self.today,
            "last_bar_ts": last.ts if last else None, "last_close": last.close if last else None,
            "qty": qty if held else 0,
            "entry_ts": holding.ts if held else None,
            "entry_price": holding.open * (1 + COSTS.slippage) if held else None,
        }

    def close(self, client, now):
        """보유 중이면 청산하고, 오늘 봉 전체의 백테스트 거래와 실시간 거래를 비교한 요약 문구를 반환한다."""
        diffs = []
        for symbol, runner in self.runners.items():
            trade = runner.finish()
            if trade:
                self._sell(symbol, trade, alert=True)
                store.save_paper_status(self.conn, self.status_row(symbol))
            try:
                bars = regular(client.fetch_day(symbol, self.today), now)
            except TossError as e:
                diffs.append(f"{symbol} 비교 실패 {e.code}")
                continue
            expected = [trade_key(t) for t in engine.run_day(symbol, Orb({}), bars, COSTS)]
            live = [trade_key(t) for t in self.trades[symbol]]
            if live != expected:
                diffs.append(f"{symbol} 불일치: 실시간 {len(live)}건 / 마감 {len(expected)}건")
                log.warning("%s 불일치 실시간 %s / 마감 %s", symbol, live, expected)
        return self.summary(diffs)

    def summary(self, diffs):
        """일일 요약 문구: 전체·종목별 거래 수와 원화 손익, 마감 비교 결과."""
        wins = sum(1 for _, p in self.saved if p > 0)
        total = sum((p for _, p in self.saved), Decimal(0))
        lines = [f"[모의투자] {self.today} 요약",
                 f"거래 {len(self.saved)}건 (승 {wins} / 패 {len(self.saved) - wins}) 손익 {total:+,.0f}원"]
        for symbol in self.runners:
            pnls = [p for s, p in self.saved if s == symbol]
            if pnls:
                lines.append(f"{symbol} {len(pnls)}건 {sum(pnls):+,.0f}원")
        lines.append("마감 비교: " + (", ".join(diffs) if diffs else "일치"))
        return "\n".join(lines)


def run(client, conn, symbols, capital, now=lambda: datetime.now(KST), sleep=_time.sleep):
    """모의투자 하루 실행: 장 시간 확인 → 따라잡기 → 매분 조회 → 15:31 마감 비교·요약. 종료 코드를 반환한다."""
    today = now().date()
    hours = client.market_hours(today)
    if hours is None:
        log.info("%s 휴장", today)
        return 0
    start, end = hours
    if (start.time(), end.time()) != (REGULAR_OPEN, REGULAR_CLOSE):
        log.warning("%s 정규장 시간 변경일 %s~%s", today, start, end)
        notify.send_telegram(f"[모의투자] {today} 정규장 시간 변경일({start:%H:%M}~{end:%H:%M}), 모의투자 안 함")
        return 0

    paper = Paper(conn, symbols, capital, today)
    for symbol in symbols:
        paper.poll(client, symbol, now(), catch_up=True)
    held = sum(1 for s in symbols if paper.status_row(s)["qty"])
    notify.send_telegram(f"[모의투자] {today} 시작(따라잡기 완료): {len(symbols)}종목, "
                         f"보유 {held}종목, 거래 {len(paper.saved)}건")

    while True:
        at = next_fetch_at(now())
        if at.time() >= LOOP_END:
            break
        sleep((at - now()).total_seconds())
        for symbol in symbols:
            paper.poll(client, symbol, now())

    loop_end = datetime.combine(today, LOOP_END, KST)
    if now() < loop_end:
        sleep((loop_end - now()).total_seconds())
    notify.send_telegram(paper.close(client, now()))
    return 0
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests -v`
Expected: 107 passed (93 + 14). 실패 시 `test_full_day...`의 `sent` 차이부터 확인한다

- [ ] **Step 5: 커밋**

```powershell
git add paper.py tests/test_paper.py
git commit -m @'
장중 모의투자 하루 실행 루프 추가

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
'@
```

---

### Task 5: `paper.py` 진입점, 설정, README

**Files:**
- Modify: `trader/paper.py` (import·상수·`setup_logging`·`main` 추가)
- Create: `trader/paper_symbols.txt`
- Modify: `trader/.env.example`, `trader/README.md`
- Test: `trader/tests/test_paper.py` (끝에 추가)

**Interfaces:**
- Consumes: `paper.run` (Task 4), `collector.read_symbols(path) -> list[str]`, `TossClient(client_id, client_secret, base_url, rps, token_path)`
- Produces: `paper.main(now=None) -> int` (정상·휴장 0, 설정·DB·인증 실패 1), `paper.REQUIRED_ENV`, `paper.ROOT`

- [ ] **Step 1: 실패하는 테스트 작성**

`trader/tests/test_paper.py` 상단 import에 `import logging`, `import os`를 추가하고 끝에 추가:

```python
@pytest.fixture
def main_env(monkeypatch, sent):
    """main 실행 환경: .env 읽기를 막고 필수 값과 종목을 채운 뒤 알림 목록을 반환한다."""
    monkeypatch.setattr(paper, "load_dotenv", lambda *args, **kwargs: None)
    for key in paper.REQUIRED_ENV:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TOSS_CLIENT_ID", "CID-X")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "SECRET-X")
    monkeypatch.setenv("PAPER_CAPITAL", "2000000")
    monkeypatch.setattr(paper, "read_symbols", lambda path: ["A"])
    return sent


def test_main_missing_env_fails_without_secrets(main_env, monkeypatch, caplog):
    """설정이 빠지면 1을 반환하고 로그·알림에는 변수 이름과 예외 종류만 남긴다."""
    monkeypatch.delenv("PAPER_CAPITAL")
    monkeypatch.setenv("DATABASE_URL", "SECRETPW")
    with caplog.at_level(logging.INFO):
        assert paper.main(at("08:55")) == 1
    assert ".env 누락: PAPER_CAPITAL" in caplog.text
    assert "SECRET-X" not in caplog.text and "SECRETPW" not in caplog.text
    assert main_env == ["[모의투자] 2026-09-17 실행 실패: RuntimeError"]


def test_main_auth_error_returns_1(conn, main_env, monkeypatch):
    """인증 오류면 실행 실패 알림을 보내고 1을 반환하며, 토스 기본 설정으로 클라이언트를 만든다."""
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    created = []
    fake = FakeClient(Clock(at("08:55")), {"A": []}, hours=TossAuthError("access_denied"))
    monkeypatch.setattr(paper, "TossClient", lambda *args: created.append(args[:4]) or fake)
    assert paper.main(at("08:55")) == 1
    assert main_env == ["[모의투자] 2026-09-17 실행 실패: TossAuthError"]
    assert created == [("CID-X", "SECRET-X", "https://openapi.tossinvest.com", 15.0)]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests/test_paper.py -v`
Expected: 새 테스트 2개 FAIL/ERROR (`AttributeError: module 'paper' has no attribute 'load_dotenv'`)

- [ ] **Step 3: 구현**

`trader/paper.py` import 블록을 아래로 바꾼다(`# noqa` 주석 제거):

```python
import logging
import os
import sys
import time as _time
from datetime import datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

import psycopg
from dotenv import load_dotenv

import engine
import notify
import store
from backtest import REGULAR_CLOSE, REGULAR_OPEN
from bars import KST
from collector import read_symbols
from strategies import Orb
from toss import TossAuthError, TossClient, TossError
```

상수 블록 맨 위에 추가:

```python
ROOT = Path(__file__).resolve().parent
REQUIRED_ENV = ("TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET", "DATABASE_URL", "PAPER_CAPITAL")
```

파일 끝에 추가:

```python
def setup_logging(today):
    """콘솔과 logs/paper-YYYY-MM-DD.log에 로그를 남기도록 설정한다."""
    (ROOT / "logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler(ROOT / "logs" / f"paper-{today}.log", encoding="utf-8")],
    )


def main(now=None):
    """모의투자 1일 실행. 정상·휴장이면 0, 설정·DB·인증 등 실행 전체 실패면 1을 반환한다."""
    now = now or datetime.now(KST)
    load_dotenv(ROOT / ".env")
    setup_logging(now.date())
    try:
        missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
        if missing:
            log.error(".env 누락: %s", ", ".join(missing))
            raise RuntimeError("missing env")
        capital = Decimal(os.environ["PAPER_CAPITAL"])
        symbols = read_symbols(ROOT / "paper_symbols.txt")
        client = TossClient(
            os.environ["TOSS_CLIENT_ID"], os.environ["TOSS_CLIENT_SECRET"],
            os.environ.get("TOSS_BASE_URL") or "https://openapi.tossinvest.com",
            float(os.environ.get("TOSS_RPS") or "15"), ROOT / ".token.json",
        )
        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
            return run(client, conn, symbols, capital)
    except Exception as e:
        # 예외 문자열에 접속 문자열 등이 섞일 수 있어 종류와 토스 오류 코드만 남긴다
        log.error("실행 실패: %s %s", type(e).__name__, e.code if isinstance(e, TossError) else "")
        notify.send_telegram(f"[모의투자] {now.date()} 실행 실패: {type(e).__name__}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

`trader/paper_symbols.txt`:

```
# 모의투자 종목코드, 한 줄에 하나 (# 뒤는 주석)
005930  # 삼성전자
000660  # SK하이닉스
```

`trader/.env.example` 끝에 추가:

```
PAPER_CAPITAL=2000000
```

`trader/README.md`:
- 1행 제목을 `# 토스증권 1분봉 수집기, 백테스터, 모의투자`로, 3~4행을 아래로 바꾼다:

```markdown
`symbols.txt` 종목의 1분봉을 토스증권 Open API에서 받아 PostgreSQL에 저장하고, 저장한 봉으로 전략을 백테스트하고, 장중 실시간 봉으로 모의투자한다.
설계: `docs/superpowers/specs/2026-09-15-toss-collector-design.md`, `docs/superpowers/specs/2026-09-15-backtester-design.md`, `docs/superpowers/specs/2026-09-17-paper-trader-design.md`
```

- "첫 실행 수동 검증 (1회)" 절 바로 앞에 아래 절을 넣는다:

````markdown
## 모의투자

```powershell
.\.venv\Scripts\python paper.py
```

`paper_symbols.txt` 종목을 orb 기본 파라미터로 장중 가상 체결한다. 실주문은 하지 않는다.

- 설정: `.env`의 `PAPER_CAPITAL`(모의 총액, 예 2000000). 종목당 금액 = 총액 ÷ 종목 수, 수량은 슬리피지 반영 매수가로 나눈 정수 주(내림). 0주면 거래하지 않는다
- 비용: 수수료 0.015%, 매도세 0.20%, 슬리피지 0.05%, 15:15 강제 청산, 정규장 봉만 (백테스트 기본값과 같음)
- 흐름: 휴장·정규장 시간 변경일이면 바로 종료 → 오늘 이미 끝난 봉으로 따라잡기 → 매분 :15초에 끝난 봉 조회 → 15:31에 오늘 봉으로 백테스트를 다시 돌려 실시간 거래와 비교 → 텔레그램 요약 후 종료
- 알림: 시작(따라잡기 완료), 매수·매도 체결, 같은 종목 5분 연속 조회 실패, 일일 요약(마감 비교 `일치`/`불일치`). 재시작 따라잡기 중 체결은 다시 알리지 않는다
- 기록: `paper_status`(종목별 현재 상태), `paper_trades`(완결 거래). 중간에 꺼졌다 다시 켜면 같은 상태로 복구되고 거래는 중복 저장되지 않는다
- 로그: `logs/paper-YYYY-MM-DD.log`
- 토스 토큰은 클라이언트당 1개라 장중(08:55~15:31)에는 `collector.py`를 수동 실행하지 않는다

작업 스케줄러 등록(관리자 PowerShell, `trader` 폴더 기준):

```powershell
$dir = (Get-Location).Path
$action = New-ScheduledTaskAction -Execute "$dir\.venv\Scripts\python.exe" -Argument "paper.py" -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 08:55
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 8)
Register-ScheduledTask -TaskName "토스 모의투자" -Action $action -Trigger $trigger -Settings $settings
```

거래 확인:

```sql
SELECT (exit_ts AT TIME ZONE 'Asia/Seoul')::date AS day, symbol, qty, entry_price, exit_price, pnl_krw, exit_reason
FROM paper_trades ORDER BY exit_ts DESC LIMIT 20;
```
````

- [ ] **Step 4: 테스트 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests -v`
Expected: 109 passed

- [ ] **Step 5: 커밋**

```powershell
git add paper.py paper_symbols.txt .env.example README.md tests/test_paper.py
git commit -m @'
모의투자 실행 진입점과 사용법 추가

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
'@
```

---

### Task 6: 로컬 모의투자 화면

**Files:**
- Create: `trader/web.py`, `trader/web/index.html`, `trader/web/app.js`, `trader/web/style.css`
- Modify: `trader/README.md` ("모의투자" 절 끝에 화면 안내 추가)
- Test: `trader/tests/test_web.py`

**Interfaces:**
- Consumes: `store.load_paper_status/load_paper_trades/load_paper_daily`, `store.save_paper_status/save_paper_trade` (테스트), `paper.COSTS.slippage`
- Produces:
  - `web.make_server(db_url: str, port: int) -> ThreadingHTTPServer` (127.0.0.1, port 0이면 빈 포트)
  - `GET /api/status` → `[{symbol, trade_date, last_bar_ts, last_close, qty, entry_ts, entry_price, updated_at, eval_krw}]`
  - `GET /api/trades?date=YYYY-MM-DD` → `[{symbol, strategy, qty, entry_ts, entry_price, exit_ts, exit_price, pnl_krw, return_pct, exit_reason}]`, 날짜 없으면 오늘, 형식 오류 400
  - `GET /api/daily` → `[{day, trades, wins, pnl_krw}]`
  - 숫자(Decimal)는 문자열, 시각은 `+09:00` ISO 8601, 날짜는 `YYYY-MM-DD`

- [ ] **Step 1: 실패하는 테스트 작성**

`trader/tests/test_web.py`:

```python
import json
import os
import threading
from datetime import date, datetime
from decimal import Decimal
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

import engine
import store
import web
from bars import KST


@pytest.fixture
def base(conn):
    """테스트 DB를 읽는 화면 서버를 빈 포트에 띄우고 기본 URL을 준다."""
    server = web.make_server(os.environ["TEST_DATABASE_URL"], 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def get(url):
    """GET 요청의 (상태 코드, Content-Type, 본문 bytes)를 반환한다. 오류 응답도 그대로 돌려준다."""
    try:
        with urlopen(url) as r:
            return r.status, r.headers["Content-Type"], r.read()
    except HTTPError as e:
        return e.code, e.headers["Content-Type"], e.read()


def kst(day, hh, mm):
    """2026-09-day HH:MM KST."""
    return datetime(2026, 9, day, hh, mm, tzinfo=KST)


def save_trade(conn, symbol, day, qty, pnl):
    """day 09:41 진입, 10:01 청산한 모의 거래를 저장한다."""
    trade = engine.Trade(symbol, kst(day, 9, 41), Decimal("101.0505"), kst(day, 10, 1),
                         Decimal("103.948"), Decimal("2.63"), "signal")
    store.save_paper_trade(conn, "orb", qty, trade, Decimal(pnl))


@pytest.mark.parametrize("path, content_type, text", [
    ("/", "text/html", "모의투자"),
    ("/app.js", "text/javascript", "refresh"),
    ("/style.css", "text/css", "body"),
])
def test_static_files_are_served(base, path, content_type, text):
    """화면 파일 3개를 알맞은 Content-Type으로 준다."""
    status, ctype, body = get(base + path)
    assert status == 200 and ctype.startswith(content_type) and text in body.decode("utf-8")


@pytest.mark.parametrize("path", ["/.env", "/../.env", "/web/index.html", "/paper.py", "/api/nope"])
def test_other_paths_are_404(base, path):
    """정해진 화면 파일·API 외 경로는 404."""
    assert get(base + path)[0] == 404


def test_status_adds_eval_krw_for_holdings(base, conn):
    """보유 종목은 (현재가 × (1 − 슬리피지) − 매수가) × 수량 평가손익을, 미보유는 null을 준다."""
    store.save_paper_status(conn, {"symbol": "A", "trade_date": date(2026, 9, 17), "last_bar_ts": kst(17, 10, 0),
                                   "last_close": Decimal("104"), "qty": 10, "entry_ts": kst(17, 9, 41),
                                   "entry_price": Decimal("101.0505")})
    store.save_paper_status(conn, {"symbol": "B", "trade_date": date(2026, 9, 17), "last_bar_ts": kst(17, 10, 0),
                                   "last_close": Decimal("100"), "qty": 0, "entry_ts": None, "entry_price": None})
    status, ctype, body = get(base + "/api/status")
    assert status == 200 and ctype.startswith("application/json")
    a, b = json.loads(body)
    assert (a["symbol"], a["eval_krw"], a["entry_ts"], a["trade_date"]) == (
        "A", "28.9750", "2026-09-17T09:41:00+09:00", "2026-09-17")
    assert (b["symbol"], b["qty"], b["eval_krw"], b["entry_price"]) == ("B", 0, None, None)


def test_trades_by_date_and_bad_date(base, conn):
    """date 파라미터의 KST 날짜 거래만 주고, 날짜 형식이 틀리면 400."""
    save_trade(conn, "A", 17, 10, "100")
    save_trade(conn, "B", 16, 5, "-50")
    rows = json.loads(get(base + "/api/trades?date=2026-09-16")[2])
    assert [(r["symbol"], r["qty"], r["pnl_krw"], r["exit_ts"]) for r in rows] == [
        ("B", 5, "-50", "2026-09-16T10:01:00+09:00")]
    assert get(base + "/api/trades?date=2026-13-01")[0] == 400


def test_daily_sums_by_day(base, conn):
    """일별 거래 수·수익 거래 수·손익 합계를 최신 날짜부터 준다."""
    save_trade(conn, "A", 17, 10, "100")
    save_trade(conn, "B", 17, 10, "-30")
    save_trade(conn, "A", 16, 5, "-50")
    assert json.loads(get(base + "/api/daily")[2]) == [
        {"day": "2026-09-17", "trades": 2, "wins": 1, "pnl_krw": "70"},
        {"day": "2026-09-16", "trades": 1, "wins": 0, "pnl_krw": "-50"},
    ]


def test_db_failure_returns_500_without_connection_string(conn):
    """DB 접속이 실패하면 500과 예외 종류만 주고 접속 문자열은 노출하지 않는다."""
    server = web.make_server("postgresql://nobody:SECRETPW@127.0.0.1:1/none", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        status, _, body = get(f"http://127.0.0.1:{server.server_address[1]}/api/status")
    finally:
        server.shutdown()
        server.server_close()
    assert status == 500 and b"SECRETPW" not in body
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests/test_web.py -v`
Expected: 수집 단계 ERROR (`ModuleNotFoundError: No module named 'web'`)

- [ ] **Step 3: 서버 구현**

`trader/web.py`:

```python
"""모의투자 로컬 화면 서버. web/의 화면 파일과 DB 조회 JSON API를 127.0.0.1에서 제공한다."""
import json
import os
import sys
from datetime import date, datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import psycopg
from dotenv import load_dotenv

import store
from bars import KST
from paper import COSTS

ROOT = Path(__file__).resolve().parent
PORT = 8765
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}


def to_json(value):
    """json.dumps 보조: Decimal은 문자열, 시각은 KST ISO 8601, 날짜는 YYYY-MM-DD로 바꾼다."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(KST).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def with_eval(row):
    """보유 중인 상태 행에 슬리피지 반영 평가손익 eval_krw를 더한다. 미보유면 None."""
    held = row["qty"] and row["last_close"] is not None
    row["eval_krw"] = ((row["last_close"] * (1 - COSTS.slippage) - row["entry_price"]) * row["qty"]
                       if held else None)
    return row


class Handler(BaseHTTPRequestHandler):
    """GET만 처리한다. 정해진 화면 파일 3개와 API 3개 외에는 404."""

    def do_GET(self):
        """경로에 따라 화면 파일이나 API 응답을 보낸다."""
        url = urlparse(self.path)
        if url.path in STATIC:
            name, content_type = STATIC[url.path]
            return self._send(200, content_type, (ROOT / "web" / name).read_bytes())
        try:
            if url.path == "/api/status":
                return self._json(200, [with_eval(r) for r in self._query(store.load_paper_status)])
            if url.path == "/api/trades":
                values = parse_qs(url.query).get("date")
                try:
                    day = date.fromisoformat(values[0]) if values else datetime.now(KST).date()
                except ValueError:
                    return self._json(400, {"error": "date는 YYYY-MM-DD"})
                return self._json(200, self._query(store.load_paper_trades, day))
            if url.path == "/api/daily":
                return self._json(200, self._query(store.load_paper_daily))
        except psycopg.Error as e:
            # 예외 문자열에 접속 문자열이 섞일 수 있어 종류만 보낸다
            return self._json(500, {"error": type(e).__name__})
        self._send(404, "text/plain; charset=utf-8", b"not found")

    def log_message(self, format, *args):
        """요청마다 콘솔에 찍는 기본 로그를 끈다(화면이 5초마다 조회한다)."""

    def _query(self, fn, *args):
        """요청마다 DB에 새로 연결해 store 조회 함수를 실행한다."""
        with psycopg.connect(self.server.db_url, autocommit=True) as conn:
            return fn(conn, *args)

    def _json(self, status, body):
        """body를 JSON으로 보낸다."""
        data = json.dumps(body, default=to_json, ensure_ascii=False).encode("utf-8")
        self._send(status, "application/json; charset=utf-8", data)

    def _send(self, status, content_type, data):
        """상태 코드·Content-Type·본문을 보낸다."""
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def make_server(db_url, port):
    """127.0.0.1:port에 화면 서버를 만든다. port가 0이면 빈 포트를 쓴다."""
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.db_url = db_url
    return server


def main():
    """.env의 DATABASE_URL로 화면 서버를 띄우고 Ctrl+C까지 실행한다."""
    load_dotenv(ROOT / ".env")
    server = make_server(os.environ["DATABASE_URL"], PORT)
    print(f"http://127.0.0.1:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 화면 파일 작성**

`trader/web/index.html`:

```html
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>모의투자</title>
  <link rel="stylesheet" href="/style.css">
</head>
<body>
  <header>
    <h1>모의투자</h1>
    <span class="muted">갱신 <span id="updated">-</span></span>
  </header>
  <p id="banner" hidden></p>

  <section>
    <h2>현재 상태</h2>
    <table>
      <thead><tr><th>종목</th><th>보유</th><th>매수가</th><th>현재가</th><th>평가손익</th><th>마지막 봉</th></tr></thead>
      <tbody id="status"></tbody>
    </table>
  </section>

  <section>
    <h2>오늘 체결</h2>
    <table>
      <thead><tr><th>종목</th><th>수량</th><th>진입</th><th>매수가</th><th>청산</th><th>매도가</th><th>손익</th><th>수익률</th><th>사유</th></tr></thead>
      <tbody id="trades"></tbody>
    </table>
  </section>

  <section>
    <h2>일별 손익</h2>
    <table>
      <thead><tr><th>날짜</th><th>거래</th><th>수익 거래</th><th>손익</th></tr></thead>
      <tbody id="daily"></tbody>
    </table>
  </section>

  <script src="/app.js"></script>
</body>
</html>
```

`trader/web/app.js`:

```javascript
// 모의투자 화면: 5초마다 API를 조회해 표 3개와 경고를 갱신한다.
const REFRESH_MS = 5000;
const STALE_MS = 2 * 60 * 1000;
const REASONS = { signal: "신호", close_time: "15:15 청산", day_end: "장 마감" };

/** 숫자 문자열을 반올림한 원화 표기로 바꾼다. 값이 없으면 "-". */
function won(value) {
  return value == null ? "-" : Math.round(Number(value)).toLocaleString("ko-KR");
}

/** ISO 시각 문자열에서 HH:MM만 뽑는다. 값이 없으면 "-". */
function hm(value) {
  return value ? value.slice(11, 16) : "-";
}

/** 손익 부호에 맞는 셀 클래스 이름을 고른다. */
function sign(value) {
  if (value == null || Number(value) === 0) return "";
  return Number(value) > 0 ? "up" : "down";
}

/** tbody를 행 목록으로 다시 채운다. 각 행은 [글자, 클래스] 쌍의 배열이다. */
function fill(id, rows) {
  const trs = rows.map((cells) => {
    const tr = document.createElement("tr");
    for (const [text, cls] of cells) {
      const td = document.createElement("td");
      td.textContent = text;
      if (cls) td.className = cls;
      tr.append(td);
    }
    return tr;
  });
  document.getElementById(id).replaceChildren(...trs);
}

/** 평일 09:03~15:31이면 true. 이 PC 시계(KST) 기준이며 휴장일은 구분하지 않는다. */
function marketOpen(now) {
  const minutes = now.getHours() * 60 + now.getMinutes();
  const weekday = now.getDay() >= 1 && now.getDay() <= 5;
  return weekday && minutes >= 9 * 60 + 3 && minutes < 15 * 60 + 31;
}

/** API 하나를 JSON으로 조회하고, 오류 응답이면 예외를 던진다. */
async function load(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} ${res.status}`);
  return res.json();
}

/** 세 API를 조회해 표·경고·갱신 시각을 바꾼다. 실패하면 연결 끊김을 표시한다. */
async function refresh() {
  const banner = document.getElementById("banner");
  try {
    const [status, trades, daily] = await Promise.all([
      load("/api/status"), load("/api/trades"), load("/api/daily"),
    ]);
    fill("status", status.map((s) => [
      [s.symbol], [s.qty ? `${s.qty}주` : "미보유"], [won(s.entry_price)],
      [won(s.last_close)], [won(s.eval_krw), sign(s.eval_krw)], [hm(s.last_bar_ts)],
    ]));
    fill("trades", trades.map((t) => [
      [t.symbol], [`${t.qty}주`], [hm(t.entry_ts)], [won(t.entry_price)], [hm(t.exit_ts)],
      [won(t.exit_price)], [won(t.pnl_krw), sign(t.pnl_krw)],
      [`${Number(t.return_pct).toFixed(2)}%`, sign(t.return_pct)], [REASONS[t.exit_reason] || t.exit_reason],
    ]));
    fill("daily", daily.map((d) => [
      [d.day], [`${d.trades}건`], [`${d.wins}건`], [won(d.pnl_krw), sign(d.pnl_krw)],
    ]));
    const latest = Math.max(0, ...status.map((s) => Date.parse(s.updated_at)));
    const stale = marketOpen(new Date()) && Date.now() - latest > STALE_MS;
    banner.textContent = stale ? "모의투자 프로세스 멈춤 (2분 넘게 갱신 없음)" : "";
    banner.hidden = !stale;
    document.getElementById("updated").textContent = new Date().toLocaleTimeString("ko-KR");
  } catch (err) {
    banner.textContent = "연결 끊김: web.py가 실행 중인지 확인하세요";
    banner.hidden = false;
  }
}

refresh();
setInterval(refresh, REFRESH_MS);
```

`trader/web/style.css`:

```css
:root {
  --bg: #f7f7f8;
  --fg: #1d1d1f;
  --muted: #6e6e73;
  --line: #e1e1e6;
  --up: #d70015;
  --down: #0a5fd6;
  --warn-bg: #fff4d6;
}

body {
  margin: 0 auto;
  max-width: 960px;
  padding: 16px;
  background: var(--bg);
  color: var(--fg);
  font-family: system-ui, "Malgun Gothic", sans-serif;
}

header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
}

h1 { font-size: 1.4rem; margin: 0 0 8px; }
h2 { font-size: 1.05rem; margin: 24px 0 8px; }
.muted { color: var(--muted); font-size: 0.9rem; }

#banner {
  background: var(--warn-bg);
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 8px 12px;
}

section { overflow-x: auto; }

table {
  width: 100%;
  border-collapse: collapse;
  background: #fff;
  font-variant-numeric: tabular-nums;
}

th, td {
  border-bottom: 1px solid var(--line);
  padding: 6px 8px;
  text-align: right;
  white-space: nowrap;
}

th:first-child, td:first-child { text-align: left; }
th { color: var(--muted); font-weight: 600; }
.up { color: var(--up); }
.down { color: var(--down); }
```

`trader/README.md`의 "모의투자" 절 끝(거래 확인 SQL 뒤)에 추가:

````markdown
화면(이 PC 브라우저 전용, 읽기 전용):

```powershell
.\.venv\Scripts\python web.py
```

`http://127.0.0.1:8765`에서 종목별 현재 상태(보유·평가손익·마지막 봉), 오늘 체결, 일별 손익을 5초마다 갱신한다. 장중에 2분 넘게 상태 갱신이 없으면 "모의투자 프로세스 멈춤"을 띄운다(휴장일에도 뜰 수 있다). `paper.py`와 따로 실행하므로 장 마감 후에도 볼 수 있다.
````

- [ ] **Step 5: 테스트 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests -v`
Expected: 121 passed (109 + 12)

- [ ] **Step 6: 브라우저에서 화면 확인**

운영 DB에 스키마를 적용하고(Task 7 Step 2와 같은 명령) `.\.venv\Scripts\python web.py`를 실행한 뒤 `http://127.0.0.1:8765`를 연다.
Expected: 표 3개가 빈 상태로 보이고 "갱신" 시각이 5초마다 바뀐다. 개발자 도구 콘솔에 오류가 없다. 확인 후 Ctrl+C로 종료

- [ ] **Step 7: 커밋**

```powershell
git add web.py web/index.html web/app.js web/style.css README.md tests/test_web.py
git commit -m @'
모의투자 로컬 화면 서버와 화면 추가

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
'@
```

---

### Task 7: 운영 환경 수동 검증

코드 변경 없음(1단계 결과로 `FETCH_SECOND`를 바꿀 때만 예외). 사용자 PC에서 실제 토스 키·운영 DB로 **장중에** 확인한다. `.env` 값과 토큰은 화면·로그·커밋에 출력하지 않는다.

- [ ] **Step 1: 봉 확정 대기 시간 측정 (장중 10분)**

`trader` 폴더에서 (코드는 커밋하지 않는 일회성 명령):

```powershell
.\.venv\Scripts\python -c @'
import os, time
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv
from bars import KST
from toss import TossClient
load_dotenv(".env")
c = TossClient(os.environ["TOSS_CLIENT_ID"], os.environ["TOSS_CLIENT_SECRET"], "https://openapi.tossinvest.com", 15, Path(".token.json"))
end = datetime.now(KST) + timedelta(minutes=10)
while datetime.now(KST) < end:
    now = datetime.now(KST)
    targets = [now.replace(second=s, microsecond=0) for s in (5, 15, 30, 50)]
    nxt = min([t for t in targets if t > now] or [now.replace(second=5, microsecond=0) + timedelta(minutes=1)])
    time.sleep((nxt - now).total_seconds())
    b = c.fetch_recent("005930", 3)[-1]
    print(f"{datetime.now(KST):%H:%M:%S} bar {b.ts:%H:%M} O{b.open} H{b.high} L{b.low} C{b.close} V{b.volume}")
'@
```

Expected: 같은 `bar HH:MM`의 `:15` 줄과 `:50` 줄 값이 같다. `:15`에서 다르고 `:30`에서 같으면 `paper.py`의 `FETCH_SECOND`를 30으로 바꾸고 `test_next_fetch_at_is_next_15_seconds` 기대값을 맞춘 뒤 테스트·커밋한다(`모의투자 봉 조회 시각을 :30초로 조정`). `:50`에서도 다르면 멈추고 사용자에게 보고한다

- [ ] **Step 2: 운영 DB 스키마와 설정**

```powershell
D:\PIE\PostgreSQL_15\bin\psql.exe -h localhost -U trader -d trader -v ON_ERROR_STOP=1 -f schema.sql
```

Expected: 오류 없이 종료. `SELECT count(*) FROM paper_trades;` 실행 가능

`.env`에 `PAPER_CAPITAL=2000000` 줄을 추가한다(사용자에게 요청하거나 승인 후 추가). 키 이름만 확인:
`Select-String -Path .env -Pattern '^(\w+)=' | ForEach-Object { $_.Matches[0].Groups[1].Value }`

- [ ] **Step 3: 장중 수동 실행과 화면**

터미널 두 개에서:

```powershell
.\.venv\Scripts\python paper.py
```

```powershell
.\.venv\Scripts\python web.py
```

Expected: `paper.py` 콘솔에 오류 없음, 텔레그램 "시작(따라잡기 완료)" 수신. 브라우저 `http://127.0.0.1:8765`의 "마지막 봉"이 매분 바뀐다. `SELECT symbol, last_bar_ts, updated_at FROM paper_status;`의 `last_bar_ts`가 현재 시각 −2분 이내

- [ ] **Step 4: 중간 재시작**

`paper.py`를 Ctrl+C로 끄고 2분 뒤 다시 실행한다.
Expected: 체결 알림이 다시 오지 않고 "시작(따라잡기 완료)"의 거래 수가 재시작 전과 같다. `SELECT symbol, entry_ts, count(*) FROM paper_trades GROUP BY 1, 2 HAVING count(*) > 1;` 결과 0행

- [ ] **Step 5: 마감 비교와 요약**

15:31 이후 `paper.py`가 종료 코드 0으로 끝날 때까지 둔다.
Expected: 텔레그램 요약의 마지막 줄 `마감 비교: 일치`. `불일치`면 `logs/paper-YYYY-MM-DD.log`의 `불일치 실시간 ... / 마감 ...` 줄을 사용자에게 보고하고 Step 1의 대기 시간을 다시 본다

- [ ] **Step 6: 작업 스케줄러 등록과 남은 작업 보고**

README "모의투자"의 작업 스케줄러 등록 명령을 관리자 PowerShell에서 실행한다(사용자 승인 후). 사용자에게 보고:
- 다음 영업일 08:55 자동 실행 → 15:31 요약 `일치` 확인
- 후속 작업: 종목 자동 선정 스펙(설계 문서 8절)
- `.env`의 사용하지 않는 `KIS_*` 4줄 정리(사용자 직접 또는 승인 후)
