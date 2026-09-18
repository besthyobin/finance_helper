# 일봉 스윙 전략 백테스트 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 수정주가 일봉 10년치로 신고가 돌파 + 추세 필터 + 트레일링 청산 전략을 비용을 넣어 백테스트하고, 개발·검증 구간 결과를 출력한다.

**Architecture:** 1분봉 엔진의 비용 계산을 `engine.make_trade`로 뽑아 공유하고, 일봉 전용 `swing.py`(다음 날 시가 체결 엔진 + `BreakoutTrend` 전략)를 새로 둔다. 일봉은 `toss.fetch_daily_history`로 페이지를 넘겨 받아 `daily_bars` 테이블에 덮어쓰고, `swing_backtest.py`가 수집·개발 구간 조합표·검증 구간 실행을 맡는다. 기존 1분봉 엔진·orb·모의투자·선정기는 동작을 바꾸지 않는다.

**Tech Stack:** Python 3.11, requests, psycopg 3, python-dotenv, pytest, PostgreSQL 15

**Spec:** `docs/superpowers/specs/2026-09-18-swing-backtest-design.md`

## Global Constraints

- 작업 폴더는 `trader/`. 테스트: `.\.venv\Scripts\python -m pytest tests -v` (테스트 DB는 `.env`의 `TEST_DATABASE_URL`)
- 새 의존성 금지: `requests`, `psycopg[binary]`, `python-dotenv`, `pytest`만 사용
- 모든 함수·메서드에 무엇을 하는지 한국어 docstring (사용자 규칙)
- 로그·출력에 토큰·secret·앱 비밀번호·접속 문자열을 남기지 않는다. 예외는 종류와 `TossError.code`만
- 모든 시각은 KST(`bars.KST`)
- 비용은 `paper.COSTS`(fee 0.00015, tax 0.002, slippage 0.0005)를 쓴다. 일봉 엔진은 `exit_at`을 쓰지 않는다
- 전략 기본값 `entry_days=20`, `trend_days=50`, `exit_days=10`, 조합표는 `entry_days` 20/55 × `trend_days` 50/100 × `exit_days` 10/20
- 기존 1분봉 엔진·orb·모의투자·선정기 동작 불변. `BreakoutTrend`는 `strategies.STRATEGIES`에 넣지 않는다
- 커밋 메시지는 한국어로 간결하게, 마지막 줄에 `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`
- 시작 기준선: 기존 테스트 194개 통과

---

### Task 1: 비용 계산 분리와 일봉 엔진·전략 `swing.py`

**Files:**
- Modify: `trader/engine.py:29-34` (`_close` → `make_trade` 분리)
- Create: `trader/swing.py`
- Test: `trader/tests/test_engine.py` (끝에 1개 추가), `trader/tests/test_swing.py` (신규)

**Interfaces:**
- Consumes: `engine.Trade`, `engine.Costs`, `strategies.Strategy`, `bars.DailyBar`, `bars.KST`
- Produces:
  - `engine.make_trade(symbol, entry_ts, entry_open, exit_ts, exit_base, reason, costs) -> Trade`
  - `swing.BreakoutTrend(params)` — `name="breakout_trend"`, `defaults={"entry_days": 20, "trend_days": 50, "exit_days": 10}`, `on_bar(bar, entry_price) -> "buy"|"sell"|None`
  - `swing.run_symbol(symbol, bars, strategy, costs) -> [Trade]` — 진입·청산 시각 `datetime.combine(day, time(9, 0), KST)`, 데이터 끝 청산은 `time(15, 30)`·`"day_end"`

- [ ] **Step 1: 실패하는 테스트 작성**

`trader/tests/test_engine.py` 끝에 추가:

```python
def test_make_trade_matches_close_formula():
    """make_trade는 진입 시가·청산 기준가로 run_day와 같은 슬리피지·수수료·세금 계산을 한다."""
    costs = engine.Costs(Decimal("0.001"), Decimal("0.002"), Decimal("0.001"), time(15, 15))
    t = engine.make_trade("A", datetime(2026, 9, 11, 10, tzinfo=KST), Decimal("10000"),
                          datetime(2026, 9, 11, 11, tzinfo=KST), Decimal("10100"), "signal", costs)
    assert (t.entry_price, t.exit_price) == (Decimal("10010"), Decimal("10089.9"))
    assert float(t.return_pct) == pytest.approx(0.395412, abs=1e-6)
    assert (t.symbol, t.entry_ts.hour, t.exit_ts.hour, t.exit_reason) == ("A", 10, 11, "signal")
```

`trader/tests/test_swing.py`:

```python
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest

import engine
import swing
from bars import KST, DailyBar

NO_COST = engine.Costs(Decimal(0), Decimal(0), Decimal(0), time(15, 15))
START = date(2023, 1, 2)


def days(prices, start=START):
    """(시가, 종가) 목록으로 start부터 하루씩 일봉을 만든다. 고가·저가는 둘의 최대·최소."""
    out = []
    for i, (o, c) in enumerate(prices):
        o, c = Decimal(o), Decimal(c)
        out.append(DailyBar(start + timedelta(days=i), o, max(o, c), min(o, c), c, 1000))
    return out


def closes(values, start=START):
    """종가 목록으로 시가=종가인 일봉을 만든다."""
    return days([(v, v) for v in values], start)


class Script:
    """봉 순번별로 정해진 신호를 내고 받은 entry_price를 기록하는 테스트용 전략."""

    def __init__(self, signals):
        """봉 순번 → 신호 매핑."""
        self.signals = signals
        self.i = 0
        self.seen = []

    def on_bar(self, bar, entry_price):
        """기록 후 현재 순번의 신호를 반환한다."""
        self.seen.append(entry_price)
        signal = self.signals.get(self.i)
        self.i += 1
        return signal


def at(day, hour, minute=0):
    """day HH:MM KST."""
    return datetime.combine(day, time(hour, minute), KST)


def test_signal_fills_next_day_open_and_holds_multiple_days():
    """0일째 매수 신호는 1일째 시가에, 3일째 매도 신호는 4일째 시가에 체결되고 그 사이 보유한다."""
    bars = days([("100", "101"), ("102", "103"), ("104", "105"), ("106", "107"), ("108", "109")])
    strategy = Script({0: "buy", 3: "sell"})
    [t] = swing.run_symbol("A", bars, strategy, NO_COST)
    assert (t.entry_ts, t.entry_price) == (at(bars[1].date, 9), Decimal("102"))
    assert (t.exit_ts, t.exit_price, t.exit_reason) == (at(bars[4].date, 9), Decimal("108"), "signal")
    assert strategy.seen == [None, Decimal("102"), Decimal("102"), Decimal("102"), None]


def test_ignores_buy_while_holding_and_sell_while_flat():
    """보유 중 매수 신호와 미보유 중 매도 신호는 무시한다."""
    bars = closes(["100"] * 6)
    trades = swing.run_symbol("A", bars, Script({0: "sell", 1: "buy", 2: "buy", 3: "sell"}), NO_COST)
    assert [(t.entry_ts.date(), t.exit_ts.date()) for t in trades] == [(bars[2].date, bars[4].date)]


def test_day_end_exit_at_last_close():
    """데이터가 끝날 때 보유 중이면 마지막 날 종가로 15:30에 청산한다. 마지막 날 신호는 버린다."""
    bars = days([("100", "100"), ("101", "102"), ("103", "105")])
    [t] = swing.run_symbol("A", bars, Script({0: "buy", 2: "sell"}), NO_COST)
    assert (t.exit_ts, t.exit_price, t.exit_reason) == (at(bars[2].date, 15, 30), Decimal("105"), "day_end")


def test_costs_applied_like_intraday_engine():
    """비용 계산은 engine.make_trade와 같다."""
    costs = engine.Costs(Decimal("0.001"), Decimal("0.002"), Decimal("0.001"), time(15, 15))
    bars = closes(["10000", "10000", "10100", "10100"])
    [t] = swing.run_symbol("A", bars, Script({0: "buy", 2: "sell"}), costs)
    assert (t.entry_price, t.exit_price) == (Decimal("10010"), Decimal("10089.9"))
    assert float(t.return_pct) == pytest.approx(0.395412, abs=1e-6)


def signals(strategy, bars, entry_price=None):
    """봉을 차례로 넣어 신호 목록을 반환한다."""
    return [strategy.on_bar(b, entry_price) for b in bars]


def test_breakout_buys_when_close_tops_prior_high_above_trend():
    """직전 20일 최고 종가를 넘고 50일 평균 위인 종가에서 매수 신호를 낸다(첫 50개는 판단 불가·돌파 없음)."""
    got = signals(swing.BreakoutTrend({}), closes(["100"] * 50 + ["101"]))
    assert got == [None] * 50 + ["buy"]


def test_breakout_ignored_below_trend_average():
    """20일 고가를 넘어도 50일 평균보다 낮으면 매수하지 않는다."""
    got = signals(swing.BreakoutTrend({}), closes(["200"] * 40 + ["100"] * 20 + ["101"]))
    assert got[-1] is None


def test_exit_when_close_falls_below_prior_low():
    """보유 중에는 직전 10일 최저 종가보다 낮은 종가에서 매도 신호를 낸다."""
    got = signals(swing.BreakoutTrend({}), closes([str(v) for v in range(100, 130)] + ["110"]),
                  entry_price=Decimal("100"))
    assert got == [None] * 30 + ["sell"]


def test_no_signal_without_enough_history():
    """필요한 종가 수가 모자라면 신호가 없다."""
    assert signals(swing.BreakoutTrend({}), closes(["100"] * 10 + ["200"])) == [None] * 11
    assert signals(swing.BreakoutTrend({}), closes(["100"] * 5 + ["10"]), entry_price=Decimal("100")) == [None] * 6


@pytest.mark.parametrize("params", [{"entry_days": 0}, {"trend_days": 0}, {"exit_days": -1}])
def test_breakout_rejects_non_positive_days(params):
    """일수 파라미터가 0 이하면 ValueError."""
    with pytest.raises(ValueError):
        swing.BreakoutTrend(params)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests/test_engine.py tests/test_swing.py -v`
Expected: `test_make_trade...` FAIL (`AttributeError: module 'engine' has no attribute 'make_trade'`), `test_swing.py` 수집 ERROR (`ModuleNotFoundError: No module named 'swing'`)

- [ ] **Step 3: 구현**

`trader/engine.py`의 `_close`(29~34행)를 아래로 바꾼다:

```python
def make_trade(symbol, entry_ts, entry_open, exit_ts, exit_base, reason, costs):
    """진입 시가·청산 기준가에 슬리피지를, 수익률에 수수료(양쪽)·매도세를 반영한 Trade를 만든다."""
    buy = entry_open * (1 + costs.slippage)
    sell = exit_base * (1 - costs.slippage)
    ret = (sell * (1 - costs.fee - costs.tax) / (buy * (1 + costs.fee)) - 1) * 100
    return Trade(symbol, entry_ts, buy, exit_ts, sell, ret, reason)


def _close(symbol, entry_bar, exit_ts, exit_base, reason, costs):
    """진입 봉과 청산 기준가로 슬리피지·수수료·세금을 반영한 Trade를 만든다."""
    return make_trade(symbol, entry_bar.ts, entry_bar.open, exit_ts, exit_base, reason, costs)
```

`trader/swing.py`:

```python
"""일봉 스윙 백테스트: 신호 다음 날 시가에 체결하는 엔진과 신고가 돌파·추세 전략."""
from collections import deque
from datetime import datetime, time

from bars import KST
from engine import make_trade
from strategies import Strategy

OPEN_TIME = time(9, 0)
CLOSE_TIME = time(15, 30)


class BreakoutTrend(Strategy):
    """종가가 직전 entry_days일 최고 종가를 넘고 trend_days일 평균 위면 매수, 직전 exit_days일 최저 종가 아래면 매도."""
    name = "breakout_trend"
    defaults = {"entry_days": 20, "trend_days": 50, "exit_days": 10}

    def __init__(self, params):
        """일수 파라미터를 검증하고 필요한 만큼의 최근 종가를 보관할 준비를 한다."""
        super().__init__(params)
        e, t, x = self.p["entry_days"], self.p["trend_days"], self.p["exit_days"]
        if min(e, t, x) <= 0:
            raise ValueError("breakout_trend는 entry_days·trend_days·exit_days가 모두 0보다 커야 함")
        self.closes = deque(maxlen=max(e, t, x) + 1)

    def on_bar(self, bar, entry_price):
        """오늘 종가를 쌓고, 미보유면 돌파·추세로 매수, 보유면 최저 이탈로 매도 신호를 낸다. 종가가 모자라면 None."""
        self.closes.append(bar.close)
        closes = list(self.closes)
        today, past = closes[-1], closes[:-1]
        if entry_price is None:
            e, t = self.p["entry_days"], self.p["trend_days"]
            if len(past) < e or len(closes) < t:
                return None
            trend = sum(closes[-t:]) / t
            return "buy" if today > max(past[-e:]) and today > trend else None
        x = self.p["exit_days"]
        if len(past) < x:
            return None
        return "sell" if today < min(past[-x:]) else None


def _at(day, at_time):
    """거래일과 시각을 KST datetime으로 합친다."""
    return datetime.combine(day, at_time, KST)


def run_symbol(symbol, bars, strategy, costs):
    """일봉(날짜 오름차순)을 하루씩 전략에 넣어, 신호 다음 날 시가에 체결한 거래 목록을 반환한다.
    데이터가 끝날 때 보유 중이면 마지막 종가로 청산한다."""
    trades = []
    entry = None     # 보유 중이면 진입한 날의 일봉
    pending = None   # 오늘 시가에 체결할 전날 신호
    for bar in bars:
        if pending == "buy" and entry is None:
            entry = bar
        elif pending == "sell" and entry is not None:
            trades.append(make_trade(symbol, _at(entry.date, OPEN_TIME), entry.open,
                                     _at(bar.date, OPEN_TIME), bar.open, "signal", costs))
            entry = None
        pending = strategy.on_bar(bar, entry.open if entry else None)
    if entry is not None:
        last = bars[-1]
        trades.append(make_trade(symbol, _at(entry.date, OPEN_TIME), entry.open,
                                 _at(last.date, CLOSE_TIME), last.close, "day_end", costs))
    return trades
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests -v`
Expected: 206 passed (194 + 1 + 11)

- [ ] **Step 5: 커밋**

```powershell
git add engine.py swing.py tests/test_engine.py tests/test_swing.py
git commit -m @'
비용 계산 분리와 일봉 스윙 엔진·전략 추가

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
'@
```

---

### Task 2: 일봉 테이블·저장소와 토스 일봉 이력 조회

**Files:**
- Modify: `trader/schema.sql` (끝에 추가), `trader/store.py` (import, 끝에 추가), `trader/toss.py` (상수, `_to_daily`, `fetch_daily` 변환부, `fetch_daily_history`), `trader/tests/conftest.py` (TRUNCATE)
- Test: `trader/tests/test_store.py`, `trader/tests/test_toss.py` (끝에 추가)

**Interfaces:**
- Consumes: `bars.DailyBar`, `TossClient._get`, `_parsed`, `CANDLES_PATH`
- Produces:
  - `store.save_daily_bars(conn, symbol, bars) -> None` — `(symbol, day)` upsert
  - `store.load_daily_bars(conn, symbols, date_from, date_to) -> {symbol: [DailyBar]}` — `symbols`가 None이면 전 종목, 날짜 양끝 포함, 날짜 오름차순
  - `TossClient.fetch_daily_history(symbol, since) -> [DailyBar]` — `since` 이후(포함), 날짜 오름차순

- [ ] **Step 1: 실패하는 테스트 작성**

`trader/tests/conftest.py`의 TRUNCATE를 바꾼다:

```python
        c.execute("TRUNCATE minute_bars, collect_runs, backtest_runs, paper_status, paper_trades, "
                  "selection_candidates, daily_bars CASCADE")
```

`trader/tests/test_store.py`: import의 `from bars import KST, Bar`를 `from bars import KST, Bar, DailyBar`로 바꾸고 끝에 추가:

```python
def daily(day, close):
    """시가=고가=저가=종가인 일봉."""
    p = Decimal(close)
    return DailyBar(day, p, p, p, p, 1000)


def test_save_daily_bars_round_trip_and_updates_same_day(conn):
    """일봉을 저장·조회하고, 같은 날을 다시 저장하면 새 값(수정주가)으로 바뀐다."""
    store.save_daily_bars(conn, "A", [daily(date(2026, 9, 17), "100"), daily(date(2026, 9, 16), "90")])
    store.save_daily_bars(conn, "A", [daily(date(2026, 9, 17), "50")])
    assert store.load_daily_bars(conn, ["A"], date(2026, 9, 1), date(2026, 9, 30)) == {
        "A": [daily(date(2026, 9, 16), "90"), daily(date(2026, 9, 17), "50")]}


def test_load_daily_bars_filters_symbols_and_dates(conn):
    """종목 목록과 날짜 범위(양끝 포함)로 거르고, 종목이 None이면 전 종목을 준다."""
    for symbol in ("A", "B"):
        store.save_daily_bars(conn, symbol, [daily(date(2026, 9, d), "100") for d in (14, 15, 16)])
    got = store.load_daily_bars(conn, ["B"], date(2026, 9, 15), date(2026, 9, 16))
    assert {s: [b.date.day for b in bs] for s, bs in got.items()} == {"B": [15, 16]}
    assert sorted(store.load_daily_bars(conn, None, date(2026, 9, 14), date(2026, 9, 14))) == ["A", "B"]
```

`trader/tests/test_toss.py` 끝에 추가 (`ok`, `FakeToss`, `FakeResponse`, `make_client`, `date`, `timedelta`, `pytest`, `TossError`는 파일에 있음):

```python
def daily_candle(day, price="100"):
    """일봉 캔들 한 개를 응답 형식으로 만든다."""
    return {"timestamp": f"{day.isoformat()}T00:00:00.000+09:00", "openPrice": price, "highPrice": price,
            "lowPrice": price, "closePrice": price, "volume": "10", "currency": "KRW"}


def daily_market(all_days):
    """before 이하(없으면 전체) 날짜를 최신순 200개씩 주고 nextBefore를 마지막 날짜 하루 전으로 주는 on_get."""
    def on_get(params):
        """토스 일봉 페이지를 흉내 낸다."""
        before = date.fromisoformat(params["before"][:10]) if "before" in params else None
        page = sorted((d for d in all_days if before is None or d <= before), reverse=True)[:200]
        next_before = (page[-1] - timedelta(days=1)).isoformat() + "T00:00:00.000+09:00" if page else None
        return ok([daily_candle(d) for d in page], next_before)
    return on_get


def test_fetch_daily_history_walks_pages_until_since(tmp_path):
    """200개씩 거꾸로 받다가 since보다 이른 날짜가 나온 페이지에서 멈추고 since 이후만 오름차순으로 준다."""
    all_days = [date(2024, 1, 1) + timedelta(days=i) for i in range(500)]
    fake = FakeToss(on_get=daily_market(all_days))
    bars = make_client(tmp_path, fake).fetch_daily_history("005930", date(2024, 3, 1))
    assert (bars[0].date, bars[-1].date, len(bars)) == (date(2024, 3, 1), all_days[-1], 440)
    assert len(fake.gets()) == 3
    first, second = fake.gets()[0][2]["params"], fake.gets()[1][2]["params"]
    assert first == {"symbol": "005930", "interval": "1d", "count": 200, "adjusted": "true"}
    assert second["before"] == "2024-10-26T00:00:00.000+09:00"


def test_fetch_daily_history_empty_page_returns_empty(tmp_path):
    """첫 페이지가 비면 빈 목록."""
    fake = FakeToss(on_get=lambda params: ok([], None))
    assert make_client(tmp_path, fake).fetch_daily_history("005930", date(2020, 1, 1)) == []


def test_fetch_daily_history_raises_after_40_pages(tmp_path):
    """40페이지를 받아도 since에 닿지 않으면 PAGINATION 예외."""
    fake = FakeToss(on_get=lambda params: ok([daily_candle(date(2026, 1, 1))], "2025-12-31T00:00:00+09:00"))
    with pytest.raises(TossError) as e:
        make_client(tmp_path, fake).fetch_daily_history("005930", date(2020, 1, 1))
    assert e.value.code == "PAGINATION"
    assert len(fake.gets()) == 40


def test_fetch_daily_history_rejects_bad_body(tmp_path):
    """candles가 없으면 BAD_RESPONSE."""
    fake = FakeToss(on_get=lambda params: FakeResponse({"result": {}}))
    with pytest.raises(TossError) as e:
        make_client(tmp_path, fake).fetch_daily_history("005930", date(2020, 1, 1))
    assert e.value.code == "BAD_RESPONSE"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests/test_store.py tests/test_toss.py -v`
Expected: DB 테스트가 conftest TRUNCATE에서 ERROR (`relation "daily_bars" does not exist`), toss 새 테스트는 `AttributeError: ... 'fetch_daily_history'`

- [ ] **Step 3: 구현**

`trader/schema.sql` 끝에 추가:

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

`trader/store.py`: `from bars import KST, Bar`를 `from bars import KST, Bar, DailyBar`로 바꾸고, 모듈 docstring을 `"""minute_bars·daily_bars·collect_runs·backtest·paper·selection 테이블 저장과 조회."""`로 바꾼 뒤 끝에 추가:

```python
def save_daily_bars(conn, symbol, bars):
    """일봉을 한 트랜잭션으로 저장한다. 같은 (symbol, day)는 새 값으로 덮어쓴다(수정주가 갱신)."""
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO daily_bars (symbol, day, open, high, low, close, volume) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (symbol, day) DO UPDATE SET open = EXCLUDED.open, high = EXCLUDED.high, "
            "low = EXCLUDED.low, close = EXCLUDED.close, volume = EXCLUDED.volume",
            [(symbol, b.date, b.open, b.high, b.low, b.close, b.volume) for b in bars],
        )


def load_daily_bars(conn, symbols, date_from, date_to):
    """종목·기간(양끝 포함) 일봉을 {symbol: 날짜 오름차순 DailyBar}로 반환한다. symbols가 None이면 전 종목."""
    sql = ("SELECT symbol, day, open, high, low, close, volume FROM daily_bars "
           "WHERE day >= %s AND day <= %s")
    args = [date_from, date_to]
    if symbols is not None:
        sql += " AND symbol = ANY(%s)"
        args.append(list(symbols))
    out = {}
    for symbol, day, o, h, l, c, v in conn.execute(sql + " ORDER BY symbol, day", args):
        out.setdefault(symbol, []).append(DailyBar(day, o, h, l, c, v))
    return out
```

`trader/toss.py`:
- 상수 `MAX_PAGES = 10` 아래에 `DAILY_MAX_PAGES = 40`을 추가한다
- `_to_bar` 아래에 추가:

```python
def _to_daily(candle):
    """일봉 캔들 한 개를 KST 날짜·Decimal 가격·int 거래량의 DailyBar로 바꾼다."""
    return DailyBar(datetime.fromisoformat(candle["timestamp"]).astimezone(KST).date(),
                    Decimal(candle["openPrice"]), Decimal(candle["highPrice"]), Decimal(candle["lowPrice"]),
                    Decimal(candle["closePrice"]), int(Decimal(candle["volume"])))
```

- `fetch_daily`의 반환문을 `_to_daily`를 쓰도록 바꾼다(동작 같음):

```python
        return _parsed(lambda b: sorted((_to_daily(c) for c in b["result"]["candles"]),
                                        key=lambda d: d.date), body)
```

- `fetch_daily` 아래에 추가:

```python
    def fetch_daily_history(self, symbol, since):
        """since(포함) 이후 수정주가 일봉을 200개씩 거꾸로 받아 날짜 오름차순 DailyBar로 반환한다."""
        bars = {}
        before = None
        for _ in range(DAILY_MAX_PAGES):
            params = {"symbol": symbol, "interval": "1d", "count": 200, "adjusted": "true"}
            if before:
                params["before"] = before
            body = self._get(CANDLES_PATH, params)
            candles, next_before = _parsed(
                lambda b: ([_to_daily(c) for c in b["result"]["candles"]], b["result"].get("nextBefore")), body)
            for d in candles:
                if d.date >= since:
                    bars[d.date] = d
            if not candles or not next_before or min(d.date for d in candles) < since:
                break
            before = next_before
        else:
            raise TossError("PAGINATION")
        return [bars[d] for d in sorted(bars)]
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests -v`
Expected: 212 passed (206 + 2 + 4)

- [ ] **Step 5: 커밋**

```powershell
git add schema.sql store.py toss.py tests/conftest.py tests/test_store.py tests/test_toss.py
git commit -m @'
일봉 테이블과 토스 일봉 이력 조회 추가

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
'@
```

---

### Task 3: 스윙 백테스트 CLI `swing_backtest.py`

**Files:**
- Create: `trader/swing_backtest.py`
- Modify: `trader/README.md`
- Test: `trader/tests/test_swing_backtest.py`

**Interfaces:**
- Consumes: Task 1 `swing.BreakoutTrend`, `swing.run_symbol`; Task 2 `store.save_daily_bars`, `store.load_daily_bars`, `TossClient.fetch_daily_history`; 기존 `TossClient.rankings()`, `TossClient.stocks(symbols)`, `paper.COSTS`, `toss.TossError`/`TossAuthError`
- Produces:
  - `swing_backtest.main(argv=None, today=None) -> int` (성공 0, 실행 실패 1, 잘못된 인자 2)
  - `swing_backtest.universe(client) -> [symbol]`, `collect(conn, client, symbols, since) -> (ok, failed)`, `grid() -> [params]`, `backtest(bars_by_symbol, params) -> [Trade]`, `summarize_swing(trades, bars_by_symbol) -> dict`, `format_row(label, summary) -> str`, `years_ago(day, n) -> date`

- [ ] **Step 1: 실패하는 테스트 작성**

`trader/tests/test_swing_backtest.py`:

```python
import os
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

import engine
import store
import swing_backtest
from bars import KST, DailyBar
from toss import TossAuthError, TossError

TODAY = date(2026, 9, 18)


def flat(symbol_start, values):
    """start부터 하루씩, 시가=고가=저가=종가인 일봉 목록."""
    return [DailyBar(symbol_start + timedelta(days=i), Decimal(v), Decimal(v), Decimal(v), Decimal(v), 1000)
            for i, v in enumerate(values)]


def trade(symbol, entry_day, exit_day, ret):
    """수익률 ret(%)인 거래."""
    return engine.Trade(symbol, datetime.combine(entry_day, datetime.min.time(), KST), Decimal("100"),
                        datetime.combine(exit_day, datetime.min.time(), KST), Decimal("100"), Decimal(ret), "signal")


def test_summarize_swing_metrics():
    """거래 수·승률·평균 수익률·평균 보유일·종목당 누적 수익·종목당 단순 보유 수익을 계산한다."""
    bars = {"A": flat(date(2023, 1, 1), ["100", "110"]), "B": flat(date(2023, 1, 1), ["50", "45"])}
    trades = [trade("A", date(2023, 1, 2), date(2023, 1, 5), "2"), trade("A", date(2023, 1, 10), date(2023, 1, 11), "-1")]
    s = swing_backtest.summarize_swing(trades, bars)
    assert s == {"trades": 2, "win_rate": Decimal(50), "avg": Decimal("0.5"), "hold_days": Decimal(2),
                 "cum_per_symbol": Decimal("0.5"), "buy_hold": Decimal(0)}
    assert swing_backtest.summarize_swing([], {"A": []})["trades"] == 0


def test_grid_and_format_row():
    """조합은 entry 20/55 × trend 50/100 × exit 10/20 순서이고, 행은 없는 값을 '-'로 쓴다."""
    labels = [f"{p['entry_days']}/{p['trend_days']}/{p['exit_days']}" for p in swing_backtest.grid()]
    assert labels == ["20/50/10", "20/50/20", "20/100/10", "20/100/20",
                      "55/50/10", "55/50/20", "55/100/10", "55/100/20"]
    row = swing_backtest.format_row("20/50/10", {"trades": 0, "win_rate": None, "avg": None, "hold_days": None,
                                                 "cum_per_symbol": None, "buy_hold": Decimal("12.34")})
    assert row == "20/50/10   거래     0 | 승률     - | 평균      - | 보유      - | 종목당 누적     - | 단순 보유  +12.3%"


def test_years_ago_handles_leap_day():
    """n년 전 같은 날짜, 2월 29일은 28일로."""
    assert swing_backtest.years_ago(date(2026, 9, 18), 10) == date(2016, 9, 18)
    assert swing_backtest.years_ago(date(2028, 2, 29), 1) == date(2027, 2, 28)


class FakeClient:
    """TossClient 대역: 순위·종목 정보·일봉 이력."""

    def __init__(self, fail=None):
        """fail[symbol]이 예외면 일봉 조회에서 던진다."""
        self.fail = fail or {}

    def rankings(self):
        """A, ETF, B, 우선주, C 순위."""
        return [{"rank": i + 1, "symbol": s, "last_price": Decimal("1")} for i, s in enumerate(["A", "E", "B", "P", "C"])]

    def stocks(self, symbols):
        """E는 ETF, P는 우선주."""
        base = {"name": "x", "security_type": "STOCK", "common": True, "active": True, "suspended": False}
        info = {s: dict(base) for s in symbols}
        info["E"]["security_type"] = "ETF"
        info["P"]["common"] = False
        return info

    def fetch_daily_history(self, symbol, since):
        """since부터 3일치 일봉."""
        if symbol in self.fail:
            raise self.fail[symbol]
        return flat(since, ["100", "101", "102"])


def test_universe_keeps_active_common_stocks_in_rank_order():
    """보통주·상장 중 종목만 순위순으로."""
    assert swing_backtest.universe(FakeClient()) == ["A", "B", "C"]


def test_collect_saves_and_skips_failed_symbol(conn):
    """종목별 오류는 건너뛰고 나머지를 저장하며, 인증 오류는 올린다."""
    ok, failed = swing_backtest.collect(conn, FakeClient({"B": TossError("HTTP500")}), ["A", "B", "C"], date(2026, 9, 1))
    assert (ok, failed) == (2, ["B"])
    assert sorted(store.load_daily_bars(conn, None, date(2026, 9, 1), date(2026, 9, 30))) == ["A", "C"]
    with pytest.raises(TossAuthError):
        swing_backtest.collect(conn, FakeClient({"A": TossAuthError("access_denied")}), ["A"], date(2026, 9, 1))


@pytest.fixture
def db_env(conn, monkeypatch):
    """main이 테스트 DB를 쓰고 실제 .env를 읽지 않게 한다."""
    monkeypatch.setattr(swing_backtest, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    return conn


def seed(conn):
    """A: 개발 구간에 돌파 매수(105) 후 110 유지, 90으로 이탈 매도. B: 검증 구간에 평탄한 100."""
    a = ["100"] * 60 + ["105"] + ["110"] * 9 + ["90"] * 10
    store.save_daily_bars(conn, "A", flat(date(2023, 1, 1), a))
    store.save_daily_bars(conn, "B", flat(date(2024, 1, 1), ["100"] * 30))


def test_main_development_grid(db_env, capsys):
    """기본 실행은 분할일 전 개발 구간으로 8개 조합을 출력한다. 추세 100일 조합은 데이터가 모자라 거래가 없다."""
    seed(db_env)
    assert swing_backtest.main([], today=TODAY) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "[스윙 백테스트] 개발 구간 2016-09-18~2023-09-17 1종목 (현재 거래대금 상위 종목 기준, 생존 편향 있음)"
    rows = lines[1:]
    assert [r.split()[0] for r in rows] == ["20/50/10", "20/50/20", "20/100/10", "20/100/20",
                                            "55/50/10", "55/50/20", "55/100/10", "55/100/20"]
    assert ["거래     1" in r for r in rows] == [True, True, False, False, True, True, False, False]


def test_main_validate_runs_one_combo_on_validation_window(db_env, capsys):
    """--validate는 분할일부터 어제까지 조합 하나만 출력한다."""
    seed(db_env)
    assert swing_backtest.main(["--validate", "20:50:10"], today=TODAY) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "[스윙 백테스트] 검증 구간 2023-09-18~2026-09-17 1종목 (현재 거래대금 상위 종목 기준, 생존 편향 있음)"
    assert len(lines) == 2 and lines[1].startswith("20/50/10   거래     0")


def test_main_collect_uses_universe_and_reports(db_env, monkeypatch, capsys):
    """--collect는 대상 종목의 10년치 일봉을 받아 저장하고 결과를 한 줄로 알린다."""
    monkeypatch.setenv("TOSS_CLIENT_ID", "CID-X")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "SECRET-X")
    calls = []
    fake = FakeClient({"B": TossError("HTTP500")})
    monkeypatch.setattr(swing_backtest, "TossClient", lambda *args: calls.append(args[:4]) or fake)
    assert swing_backtest.main(["--collect"], today=TODAY) == 0
    assert capsys.readouterr().out.strip() == "일봉 수집 2종목, 실패 1종목 B"
    assert calls == [("CID-X", "SECRET-X", "https://openapi.tossinvest.com", 15.0)]
    assert sorted(store.load_daily_bars(db_env, None, date(2016, 9, 18), TODAY)) == ["A", "C"]


@pytest.mark.parametrize("argv", [["--validate", "20:50"], ["--validate", "0:50:10"], ["--years", "0"],
                                  ["--split", "2030-01-01"]])
def test_main_bad_args_return_2(argv, capsys):
    """--validate 형식·값, 기간, 분할일이 잘못되면 2를 반환한다."""
    assert swing_backtest.main(argv, today=TODAY) == 2
    assert capsys.readouterr().out.startswith("인자 오류:")
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests/test_swing_backtest.py -v`
Expected: 수집 단계 ERROR `ModuleNotFoundError: No module named 'swing_backtest'`

- [ ] **Step 3: 구현**

`trader/swing_backtest.py`:

```python
"""일봉 스윙 전략 백테스트 CLI: 일봉 수집(--collect), 개발 구간 조합표, 검증 구간 실행(--validate)."""
import argparse
import logging
import os
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from itertools import product
from pathlib import Path

import psycopg
from dotenv import load_dotenv

import store
from bars import KST
from paper import COSTS
from swing import BreakoutTrend, run_symbol
from toss import TossAuthError, TossClient, TossError

ROOT = Path(__file__).resolve().parent
GRID = {"entry_days": (20, 55), "trend_days": (50, 100), "exit_days": (10, 20)}
VALIDATION_YEARS = 3
NOTE = "(현재 거래대금 상위 종목 기준, 생존 편향 있음)"

log = logging.getLogger("swing_backtest")


class UsageError(Exception):
    """잘못된 실행 인자."""


def years_ago(day, n):
    """day의 n년 전 같은 날짜. 없는 날(2월 29일)은 28일로 바꾼다."""
    try:
        return day.replace(year=day.year - n)
    except ValueError:
        return day.replace(year=day.year - n, day=28)


def parse_args(argv):
    """인자를 해석하고 --validate를 파라미터 dict로 바꾼다. 잘못되면 UsageError."""
    p = argparse.ArgumentParser(description="일봉 스윙 전략 백테스트")
    p.add_argument("--collect", action="store_true", help="대상 종목의 일봉을 받아 저장만 한다")
    p.add_argument("--split", type=date.fromisoformat, help="개발·검증 구간 분할일(기본 3년 전)")
    p.add_argument("--years", type=int, default=10, help="전체 기간(년)")
    p.add_argument("--validate", help="검증 구간에서 돌릴 조합 entry:trend:exit")
    args = p.parse_args(argv)
    if args.years <= 0:
        raise UsageError("--years는 1 이상")
    if args.validate:
        try:
            e, t, x = (int(v) for v in args.validate.split(":"))
            args.validate = {"entry_days": e, "trend_days": t, "exit_days": x}
            BreakoutTrend(args.validate)
        except ValueError:
            raise UsageError("--validate 형식은 entry:trend:exit 양의 정수") from None
    return args


def label(params):
    """조합 이름 entry/trend/exit."""
    return f"{params['entry_days']}/{params['trend_days']}/{params['exit_days']}"


def grid():
    """조합표 파라미터 목록(entry → trend → exit 순)."""
    return [dict(zip(GRID, combo)) for combo in product(*GRID.values())]


def universe(client):
    """거래대금 1년 상위 100 중 상장 중인 보통주 종목코드를 순위순으로 반환한다."""
    rankings = client.rankings()
    stocks = client.stocks([r["symbol"] for r in rankings])
    keep = []
    for r in rankings:
        s = stocks.get(r["symbol"])
        if s and s["security_type"] == "STOCK" and s["common"] and s["active"]:
            keep.append(r["symbol"])
    return keep


def collect(conn, client, symbols, since):
    """종목마다 since 이후 일봉을 받아 저장하고 (성공 종목 수, 실패 종목 목록)을 반환한다. 인증 오류는 올린다."""
    ok, failed = 0, []
    for symbol in symbols:
        try:
            bars = client.fetch_daily_history(symbol, since)
        except TossAuthError:
            raise
        except TossError as e:
            log.error("%s 일봉 수집 실패 %s", symbol, e.code)
            failed.append(symbol)
            continue
        store.save_daily_bars(conn, symbol, bars)
        ok += 1
        log.info("%s 일봉 %d개", symbol, len(bars))
    return ok, failed


def backtest(bars_by_symbol, params):
    """종목마다 새 전략으로 run_symbol을 돌려 전체 거래 목록을 반환한다."""
    trades = []
    for symbol, bars in bars_by_symbol.items():
        trades += run_symbol(symbol, bars, BreakoutTrend(params), COSTS)
    return trades


def summarize_swing(trades, bars_by_symbol):
    """거래 수·승률(%)·평균 수익률(%)·평균 보유일·종목당 누적 수익(%)·종목당 단순 보유 수익(%)을 계산한다."""
    held = {s: bars for s, bars in bars_by_symbol.items() if bars}
    per_symbol = dict.fromkeys(held, Decimal(0))
    for t in trades:
        per_symbol[t.symbol] = per_symbol.get(t.symbol, Decimal(0)) + t.return_pct
    n = len(trades)
    return {
        "trades": n,
        "win_rate": Decimal(sum(t.return_pct > 0 for t in trades)) * 100 / n if n else None,
        "avg": sum((t.return_pct for t in trades), Decimal(0)) / n if n else None,
        "hold_days": Decimal(sum((t.exit_ts.date() - t.entry_ts.date()).days for t in trades)) / n if n else None,
        "cum_per_symbol": sum(per_symbol[s] for s in held) / len(held) if held and n else None,
        "buy_hold": sum((b[-1].close / b[0].open - 1) * 100 for b in held.values()) / len(held) if held else None,
    }


def _fmt(value, spec, suffix=""):
    """값이 있으면 spec으로, 없으면 '-'를 같은 폭으로 쓴다."""
    text = "-" if value is None else f"{value:{spec}}{suffix}"
    width = len(f"{Decimal(0):{spec}}{suffix}")
    return text.rjust(width)


def format_row(name, s):
    """조합 한 줄."""
    return (f"{name:<10} 거래 {s['trades']:5d} | 승률 {_fmt(s['win_rate'], '4.1f', '%')} | "
            f"평균 {_fmt(s['avg'], '+.2f', '%')} | 보유 {_fmt(s['hold_days'], '5.1f', '일')} | "
            f"종목당 누적 {_fmt(s['cum_per_symbol'], '+.1f', '%')} | 단순 보유 {_fmt(s['buy_hold'], '+6.1f', '%')}")


def main(argv=None, today=None):
    """일봉 수집 또는 백테스트 1회. 성공 0, 실행 실패·데이터 없음 1, 잘못된 인자 2를 반환한다."""
    try:
        args = parse_args(sys.argv[1:] if argv is None else argv)
    except UsageError as e:
        print(f"인자 오류: {e}")
        return 2
    today = today or datetime.now(KST).date()
    since = years_ago(today, args.years)
    split = args.split or years_ago(today, VALIDATION_YEARS)
    if not since < split < today:
        print("인자 오류: --split은 시작일과 오늘 사이여야 함")
        return 2
    load_dotenv(ROOT / ".env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
            if args.collect:
                client = TossClient(
                    os.environ["TOSS_CLIENT_ID"], os.environ["TOSS_CLIENT_SECRET"],
                    os.environ.get("TOSS_BASE_URL") or "https://openapi.tossinvest.com",
                    float(os.environ.get("TOSS_RPS") or "15"), ROOT / ".token.json",
                )
                ok, failed = collect(conn, client, universe(client), since)
                print(f"일봉 수집 {ok}종목, 실패 {len(failed)}종목 {' '.join(failed)}".rstrip())
                return 0
            if args.validate:
                title, start, end, combos = "검증 구간", split, today - timedelta(days=1), [args.validate]
            else:
                title, start, end, combos = "개발 구간", since, split - timedelta(days=1), grid()
            bars = store.load_daily_bars(conn, None, start, end)
    except Exception as e:
        # 예외 문자열에 접속 문자열이 섞일 수 있어 종류와 토스 오류 코드만 남긴다
        print(f"실행 실패: {type(e).__name__} {e.code if isinstance(e, TossError) else ''}".rstrip())
        return 1
    if not bars:
        print("일봉 데이터 없음: 먼저 --collect로 수집하세요")
        return 1
    print(f"[스윙 백테스트] {title} {start}~{end} {len(bars)}종목 {NOTE}")
    for params in combos:
        print(format_row(label(params), summarize_swing(backtest(bars, params), bars)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

`trader/README.md`: 1행 제목 끝에 `, 스윙 백테스트`를 더하고, 4행 설계 목록 끝에 `, \`docs/superpowers/specs/2026-09-18-swing-backtest-design.md\``를 더한 뒤 "첫 실행 수동 검증 (1회)" 절 바로 앞에 넣는다:

````markdown
## 일봉 스윙 백테스트

당일 청산 orb는 거래 1건 비용(약 0.33%)을 넘지 못해, 며칠~몇 주 보유하는 일봉 전략을 따로 검증한다.

- 전략 `breakout_trend`: 종가가 직전 entry일 최고 종가를 넘고 trend일 평균 위면 매수, 직전 exit일 최저 종가 아래면 매도. 신호는 그날 종가로 내고 다음 날 시가에 체결(수정주가 일봉)
- 비용: 모의투자와 같음(수수료 0.015% 양쪽, 매도세 0.20%, 슬리피지 0.05% 양쪽)
- 대상: 오늘 기준 거래대금 1년 상위 100 중 상장 중인 보통주. 현재 종목으로 과거를 보는 생존 편향이 있다
- 기간: 최근 10년, 분할일(기본 3년 전) 전은 개발 구간, 이후는 검증 구간. 검증 구간은 조합을 고른 뒤 한 번만 본다

```powershell
.\.venv\Scripts\python swing_backtest.py --collect             # 대상 종목 10년치 일봉 수집(daily_bars 덮어쓰기), 1~2분
.\.venv\Scripts\python swing_backtest.py                       # 개발 구간 조합표(entry 20/55 × trend 50/100 × exit 10/20)
.\.venv\Scripts\python swing_backtest.py --validate 20:50:10   # 고른 조합 하나를 검증 구간에서
```

표 항목: 거래 수, 승률, 거래당 평균 수익률, 평균 보유일, 종목당 누적 수익(종목별 거래 수익률 합의 평균), 종목당 단순 보유 수익(구간 첫 시가 대비 마지막 종가). 처음 실행 전 `schema.sql`을 적용한다(`daily_bars` 테이블). 수집은 20:30 수집기 시각을 피한다.
````

- [ ] **Step 4: 테스트 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests -v`
Expected: 224 passed (212 + 12)

- [ ] **Step 5: 커밋**

```powershell
git add swing_backtest.py README.md tests/test_swing_backtest.py
git commit -m @'
일봉 스윙 백테스트 실행 CLI와 사용법 추가

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
'@
```

---

### Task 4: 실제 데이터로 검증 (코드 변경 없음)

사용자 PC에서 운영 DB·실제 토스 키로 실행한다. `.env` 값은 출력하지 않는다. 20:30 수집기 시각을 피한다.

- [ ] **Step 1: 운영 DB 스키마 적용**

```powershell
.\.venv\Scripts\python -c "import os, psycopg; from dotenv import load_dotenv; load_dotenv('.env'); c = psycopg.connect(os.environ['DATABASE_URL'], autocommit=True); c.execute(open('schema.sql', encoding='utf-8').read()); print(c.execute(\"select to_regclass('daily_bars')\").fetchone())"
```

Expected: `('daily_bars',)`

- [ ] **Step 2: 일봉 수집**

```powershell
.\.venv\Scripts\python swing_backtest.py --collect
```

Expected: `일봉 수집 N종목, 실패 0종목` (N은 약 79), 1~2분

- [ ] **Step 3: 개발 구간 조합표**

```powershell
.\.venv\Scripts\python swing_backtest.py
```

Expected: 8개 조합 행. 거래당 평균 수익률과 종목당 누적 수익이 단순 보유보다 나은 조합이 있는지 본다

- [ ] **Step 4: 검증 구간 1회**

개발 구간에서 거래당 평균 수익률이 가장 높은 조합(같으면 거래 수가 많은 쪽)을 고른다:

```powershell
.\.venv\Scripts\python swing_backtest.py --validate E:T:X
```

- [ ] **Step 5: 사용자 보고**

두 표와 판단(검증 구간에서도 비용 반영 평균이 플러스인지, 단순 보유 대비 어떤지, 생존 편향 한계)을 보고하고, 모의투자·종목 선정기 연동 설계로 갈지 사용자에게 묻는다.
