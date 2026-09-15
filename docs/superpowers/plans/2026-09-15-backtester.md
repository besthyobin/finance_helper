# 백테스터 (2a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `minute_bars`의 1분봉(KIS 또는 임시 Yahoo 데이터)으로 등록된 전략들을 골라 백테스트하고, 실행·거래 결과를 PostgreSQL에 저장하며 콘솔에 요약한다.

**Architecture:** `trader/`에 파일 4개를 추가하고 2개를 수정한다. `engine.py`(DB 모르는 체결 규칙), `strategies.py`(전략 클래스·등록 dict), `load_yahoo.py`(Yahoo 적재 CLI), `backtest.py`(실행 CLI). `store.py`·`schema.sql`에 source 구분·봉 조회·결과 저장을 더한다. 엔진은 종목·KST 날짜마다 새 전략 인스턴스에 봉을 하나씩 넘기고 신호를 다음 봉 시가에 체결한다.

**Tech Stack:** Python 3.11, requests, psycopg 3, python-dotenv, pytest, PostgreSQL 15 (수집기와 동일, 신규 의존성 없음)

**Spec:** `docs/superpowers/specs/2026-09-15-backtester-design.md`

### 스펙과 다르게 구체화한 점

- `engine.run_day(strategy, bars, costs)` → `run_day(symbol, strategy, bars, costs)`: `Trade`에 종목코드가 필요해 인자로 받는다.
- `load_yahoo.py` 종목 결과 출력은 `005930 yahoo(.KS) 2520봉` 형식 그대로. 테스트용으로 `main(argv, send, today)`에 대역을 주입한다.
- `backtest_runs.costs`의 fee·tax·slippage는 JSON에서 소수 정밀도를 잃지 않도록 문자열(`"0.00015"`)로 저장한다.
- `backtest.main(argv)`는 argparse 자체 오류(필수 인자 누락, 날짜 형식 오류)에서 argparse 기본 동작대로 `SystemExit(2)`를 낸다. 스펙 3.6의 검증 항목(전략·기간·파라미터)은 `2`를 반환한다.

## Global Constraints

- Python 3.11, 가상환경 `trader/.venv` (이미 있음). 의존성은 `requests`, `psycopg[binary]`, `python-dotenv`, `pytest` 4개만. pandas·ORM·모킹 라이브러리 금지
- DB: `trader/.env`의 `DATABASE_URL`(운영), `TEST_DATABASE_URL`(테스트). 둘 다 이미 설정되어 있음
- 시간대: `kis.KST` (UTC+9 고정). `zoneinfo` 사용 금지
- 함수/메서드마다 무엇을 하는지 한국어 한 줄 docstring
- 커밋 메시지는 한국어로 간결하게. 커밋에는 해당 태스크 파일만 `git add <경로>`로 추가 (작업 트리에 무관한 FMP 수정 파일이 있음, `git add -A` 금지)
- 작업 브랜치 `feat/backtester`에서 작업 (`main`에서 바로 커밋 금지)
- 모든 명령은 PowerShell, 작업 디렉터리 `D:\dev\antigravity_workspace\finance_helper\trader`
- 수집기 `collector.py`·`kis.py`·`notify.py`는 수정하지 않는다. 기존 테스트(31개)는 매 태스크 끝에 계속 통과해야 한다
- 비용 기본값: `fee=0.00015`, `tax=0.002`(실제 매도 거래세율 확인 필요), `slippage=0.0005`, `exit_at=15:15`

---

### Task 1: minute_bars source 구분과 봉 조회

**Files:**
- Modify: `trader/schema.sql` (minute_bars 블록 교체 + 이전용 DO 블록)
- Modify: `trader/store.py` (import 추가, `save_bars` source 인자, `load_bars` 추가)
- Test: `trader/tests/test_store.py` (테스트 3개 추가)

**Interfaces:**
- Consumes: `kis.KST`, `kis.Bar(ts, open, high, low, close, volume)`, 픽스처 `conn`(스키마 적용·테이블 비움, autocommit)
- Produces (`store.py`):
  - `save_bars(conn, symbol: str, bars: list[Bar], source: str = "kis") -> None`
  - `load_bars(conn, source: str, date_from: date, date_to: date, symbols: list[str] | None = None) -> dict[str, list[Bar]]` — 종목별 시각 오름차순, `Bar.ts`는 KST, 날짜는 KST 기준 양끝 포함
  - `minute_bars` 기본키 `(source, symbol, ts)`

- [ ] **Step 1: 브랜치 생성**

```powershell
git switch -c feat/backtester
```

- [ ] **Step 2: 실패하는 테스트 추가**

`trader/tests/test_store.py` 맨 위의 import 블록과 상수 줄(`TODAY`, 있으면 `SCHEMA`까지)을 다음으로 바꾼다:

```python
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import store
from kis import KST, Bar

TODAY = date(2026, 9, 14)
SCHEMA = (Path(__file__).resolve().parents[1] / "schema.sql").read_text(encoding="utf-8")
```

파일 끝에 추가:

```python
def bar_ts(ts, close="70000"):
    """주어진 시각의 테스트용 봉을 만든다."""
    p = Decimal(close)
    return Bar(ts, p, p, p, p, 100)


def test_schema_migrates_old_minute_bars(conn):
    """source 없는 수집기 버전 테이블에 schema.sql을 두 번 적용해도 데이터가 source='kis'로 보존된다."""
    conn.execute("DROP TABLE minute_bars")
    conn.execute(
        "CREATE TABLE minute_bars (symbol text NOT NULL, ts timestamptz NOT NULL, "
        "open numeric NOT NULL, high numeric NOT NULL, low numeric NOT NULL, "
        "close numeric NOT NULL, volume bigint NOT NULL, PRIMARY KEY (symbol, ts))"
    )
    conn.execute("INSERT INTO minute_bars VALUES ('005930', '2026-09-11 09:00+09', 1, 1, 1, 1, 1)")
    conn.execute(SCHEMA)
    conn.execute(SCHEMA)
    assert conn.execute("SELECT source, symbol FROM minute_bars").fetchall() == [("kis", "005930")]
    pk = conn.execute(
        "SELECT a.attname FROM pg_index i JOIN pg_attribute a "
        "ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
        "WHERE i.indrelid = 'minute_bars'::regclass AND i.indisprimary"
    ).fetchall()
    assert {r[0] for r in pk} == {"source", "symbol", "ts"}


def test_save_bars_separates_sources(conn):
    """같은 종목·시각이라도 source가 다르면 따로 저장된다."""
    b = bar_ts(datetime(2026, 9, 11, 9, 0, tzinfo=KST))
    store.save_bars(conn, "005930", [b])
    store.save_bars(conn, "005930", [b], source="yahoo")
    rows = conn.execute("SELECT source FROM minute_bars ORDER BY source").fetchall()
    assert rows == [("kis",), ("yahoo",)]


def test_load_bars_filters_source_dates_symbols(conn):
    """source·KST 날짜 경계·종목으로 걸러 종목별 시각 오름차순으로 돌려준다."""
    inside = [datetime(2026, 9, 11, 0, 30, tzinfo=KST), datetime(2026, 9, 11, 9, 0, tzinfo=KST)]
    store.save_bars(conn, "A", [bar_ts(t) for t in reversed(inside)], source="yahoo")
    store.save_bars(conn, "A", [bar_ts(datetime(2026, 9, 12, 0, 0, tzinfo=KST))], source="yahoo")
    store.save_bars(conn, "B", [bar_ts(inside[1])], source="yahoo")
    store.save_bars(conn, "C", [bar_ts(inside[1])], source="kis")

    got = store.load_bars(conn, "yahoo", date(2026, 9, 11), date(2026, 9, 11))
    assert {s: [b.ts for b in bars] for s, bars in got.items()} == {"A": inside, "B": [inside[1]]}
    assert got["A"][0].ts.utcoffset() == KST.utcoffset(None)
    assert list(store.load_bars(conn, "yahoo", date(2026, 9, 11), date(2026, 9, 11), ["B"])) == ["B"]
```

- [ ] **Step 3: 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests\test_store.py -v`
Expected: `test_schema_migrates_old_minute_bars` FAIL (`source` 컬럼 없음), `test_save_bars_separates_sources` FAIL (`unexpected keyword argument 'source'`), `test_load_bars_filters_source_dates_symbols` FAIL (`no attribute 'load_bars'`). 기존 6개는 PASS

- [ ] **Step 4: 스키마 수정**

`trader/schema.sql`의 `CREATE TABLE IF NOT EXISTS minute_bars (...);` 블록 전체를 다음으로 바꾼다 (`collect_runs` 블록은 그대로):

```sql
CREATE TABLE IF NOT EXISTS minute_bars (
    source  text        NOT NULL DEFAULT 'kis',
    symbol  text        NOT NULL,
    ts      timestamptz NOT NULL,
    open    numeric     NOT NULL,
    high    numeric     NOT NULL,
    low     numeric     NOT NULL,
    close   numeric     NOT NULL,
    volume  bigint      NOT NULL,
    PRIMARY KEY (source, symbol, ts)
);

-- 수집기 초기 버전 테이블(source 없음)을 데이터 보존하며 이전한다
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'minute_bars' AND column_name = 'source'
    ) THEN
        ALTER TABLE minute_bars ADD COLUMN source text NOT NULL DEFAULT 'kis';
        ALTER TABLE minute_bars DROP CONSTRAINT minute_bars_pkey;
        ALTER TABLE minute_bars ADD PRIMARY KEY (source, symbol, ts);
    END IF;
END $$;
```

- [ ] **Step 5: store 수정**

`trader/store.py` 맨 위 docstring과 import를 다음으로 바꾼다:

```python
"""minute_bars·collect_runs·backtest 테이블 저장과 조회."""
from datetime import datetime, time, timedelta

from kis import KST, Bar
```

기존 `save_bars` 함수를 다음으로 바꾸고, 바로 아래에 `load_bars`를 추가한다:

```python
def save_bars(conn, symbol, bars, source="kis"):
    """봉 목록을 한 트랜잭션으로 저장한다. 이미 있는 (source, symbol, ts)는 건너뛴다."""
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO minute_bars (source, symbol, ts, open, high, low, close, volume) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (source, symbol, ts) DO NOTHING",
            [(source, symbol, b.ts, b.open, b.high, b.low, b.close, b.volume) for b in bars],
        )


def load_bars(conn, source, date_from, date_to, symbols=None):
    """source·KST 기간(양끝 포함)·종목으로 봉을 조회해 {종목: 시각 오름차순 Bar 목록}으로 반환한다."""
    sql = ("SELECT symbol, ts, open, high, low, close, volume FROM minute_bars "
           "WHERE source = %s AND ts >= %s AND ts < %s")
    args = [source, datetime.combine(date_from, time(0), KST),
            datetime.combine(date_to + timedelta(days=1), time(0), KST)]
    if symbols:
        sql += " AND symbol = ANY(%s)"
        args.append(list(symbols))
    bars = {}
    for symbol, ts, o, h, l, c, v in conn.execute(sql + " ORDER BY symbol, ts", args):
        bars.setdefault(symbol, []).append(Bar(ts.astimezone(KST), o, h, l, c, v))
    return bars
```

`record_run`, `done_symbols`, `failed_runs`는 그대로 둔다.

- [ ] **Step 6: 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests -v`
Expected: `34 passed` (기존 31 + 3)

- [ ] **Step 7: 커밋**

```powershell
git add schema.sql store.py tests/test_store.py
git commit -m "분봉 source 구분과 기간별 봉 조회 추가"
```

---

### Task 2: 백테스트 엔진

**Files:**
- Create: `trader/engine.py`
- Test: `trader/tests/test_engine.py`

**Interfaces:**
- Consumes: `kis.Bar`, `kis.KST`
- Produces (`engine.py`):
  - `@dataclass(frozen=True) Costs(fee: Decimal, tax: Decimal, slippage: Decimal, exit_at: time)`
  - `@dataclass(frozen=True) Trade(symbol: str, entry_ts: datetime, entry_price: Decimal, exit_ts: datetime, exit_price: Decimal, return_pct: Decimal, exit_reason: str)` — `exit_reason`는 `'signal' | 'close_time' | 'day_end'`
  - `run_day(symbol: str, strategy, bars: list[Bar], costs: Costs) -> list[Trade]` — `strategy`는 `on_bar(bar, entry_price) -> "buy" | "sell" | None`만 있으면 된다
  - `run(strategy_cls, params: dict, bars_by_symbol: dict[str, list[Bar]], costs: Costs) -> list[Trade]` — `strategy_cls(params)`로 종목·날짜마다 인스턴스 생성

- [ ] **Step 1: 실패하는 테스트 작성**

`trader/tests/test_engine.py`:

```python
from datetime import datetime, time, timedelta
from decimal import Decimal

import pytest

import engine
from kis import KST, Bar

NO_COST = engine.Costs(Decimal(0), Decimal(0), Decimal(0), time(15, 15))


def bars_from(start, prices, day=11):
    """start(HH:MM)부터 1분 간격으로 (시가, 종가) 목록의 봉을 만든다. 고가·저가는 둘의 최대·최소."""
    t = datetime(2026, 9, day, *map(int, start.split(":")), tzinfo=KST)
    out = []
    for i, (o, c) in enumerate(prices):
        o, c = Decimal(o), Decimal(c)
        out.append(Bar(t + timedelta(minutes=i), o, max(o, c), min(o, c), c, 100))
    return out


class Script:
    """봉 순번별로 정해진 신호를 내고, 받은 entry_price를 기록하는 테스트용 전략."""

    def __init__(self, signals):
        self.signals = signals
        self.i = 0
        self.seen = []

    def on_bar(self, bar, entry_price):
        """기록 후 현재 순번의 신호를 반환한다."""
        self.seen.append(entry_price)
        signal = self.signals.get(self.i)
        self.i += 1
        return signal


def test_signal_fills_at_next_bar_open():
    """t봉 신호는 t+1봉 시가에 체결된다."""
    bars = bars_from("10:00", [("100", "101"), ("102", "103"), ("104", "105"), ("106", "107")])
    [t] = engine.run_day("A", Script({0: "buy", 2: "sell"}), bars, NO_COST)
    assert (t.entry_ts, t.entry_price) == (bars[1].ts, Decimal("102"))
    assert (t.exit_ts, t.exit_price, t.exit_reason) == (bars[3].ts, Decimal("106"), "signal")


def test_entry_price_passed_from_fill_bar():
    """전략은 체결 봉의 on_bar부터 체결 시가를 entry_price로 받는다."""
    bars = bars_from("10:00", [("100", "100"), ("102", "102"), ("104", "104")])
    strategy = Script({0: "buy"})
    engine.run_day("A", strategy, bars, NO_COST)
    assert strategy.seen == [None, Decimal("102"), Decimal("102")]


def test_last_bar_signal_is_dropped():
    """마지막 봉의 신호는 체결할 다음 봉이 없어 버린다."""
    bars = bars_from("10:00", [("100", "100"), ("101", "101")])
    assert engine.run_day("A", Script({1: "buy"}), bars, NO_COST) == []


def test_ignores_buy_while_holding_and_sell_while_flat():
    """보유 중 buy와 미보유 중 sell은 무시한다."""
    bars = bars_from("10:00", [("100", "100")] * 5)
    trades = engine.run_day("A", Script({0: "sell", 1: "buy", 2: "buy", 3: "sell"}), bars, NO_COST)
    assert [(t.entry_ts, t.exit_ts) for t in trades] == [(bars[2].ts, bars[4].ts)]


def test_close_time_exit_before_signal_and_no_entry_after():
    """exit_at 이후 첫 봉 시가에 강제 청산하고(같은 봉 신호보다 우선) 그 뒤로는 진입하지 않는다."""
    bars = bars_from("15:13", [("100", "100"), ("101", "101"), ("102", "102"), ("103", "103")])
    [t] = engine.run_day("A", Script({0: "buy", 1: "sell", 2: "buy"}), bars, NO_COST)
    assert (t.exit_ts, t.exit_price, t.exit_reason) == (bars[2].ts, Decimal("102"), "close_time")


def test_no_buy_fill_at_or_after_exit_at():
    """체결 봉이 exit_at 이상이면 매수하지 않는다."""
    bars = bars_from("15:14", [("100", "100"), ("101", "101"), ("102", "102")])
    assert engine.run_day("A", Script({0: "buy"}), bars, NO_COST) == []


def test_day_end_exit_at_last_close_when_data_ends_early():
    """exit_at 전에 데이터가 끝나면(Yahoo 14:59) 마지막 봉 종가에 청산한다."""
    bars = bars_from("14:57", [("100", "100"), ("101", "101"), ("102", "105")])
    [t] = engine.run_day("A", Script({0: "buy"}), bars, NO_COST)
    assert (t.exit_ts, t.exit_price, t.exit_reason) == (bars[2].ts, Decimal("105"), "day_end")


def test_return_pct_applies_slippage_fee_and_tax():
    """매수가·매도가에 슬리피지를, 수익률에 수수료(양쪽)와 매도세를 반영한다."""
    costs = engine.Costs(Decimal("0.001"), Decimal("0.002"), Decimal("0.001"), time(15, 15))
    bars = bars_from("10:00", [("10000", "10000"), ("10000", "10000"), ("10100", "10100"), ("10100", "10100")])
    [t] = engine.run_day("A", Script({0: "buy", 2: "sell"}), bars, costs)
    assert t.entry_price == Decimal("10010")
    assert t.exit_price == Decimal("10089.9")
    # (10089.9 × 0.997 ÷ (10010 × 1.001) − 1) × 100 = 0.3954117...
    assert float(t.return_pct) == pytest.approx(0.395412, abs=1e-6)


def test_run_splits_days_and_uses_fresh_strategy():
    """run은 날짜별로 새 전략을 만들어 전날 포지션이 다음 날로 이어지지 않는다."""
    class BuyFirst(Script):
        def __init__(self, params):
            super().__init__({0: "buy"})

    day1 = bars_from("14:58", [("100", "100"), ("101", "101")], day=10)
    day2 = bars_from("09:00", [("200", "200"), ("201", "202")], day=11)
    trades = engine.run(BuyFirst, {}, {"A": day1 + day2}, NO_COST)
    assert [(t.entry_ts, t.exit_ts, t.exit_reason) for t in trades] == [
        (day1[1].ts, day1[1].ts, "day_end"), (day2[1].ts, day2[1].ts, "day_end")]
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests\test_engine.py -v`
Expected: ERROR `ModuleNotFoundError: No module named 'engine'`

- [ ] **Step 3: 구현**

`trader/engine.py`:

```python
"""백테스트 체결 규칙: 종목·날짜별 봉 루프, 다음 봉 시가 체결, 강제 청산, 비용 반영."""
from dataclasses import dataclass
from datetime import datetime, time
from decimal import Decimal
from itertools import groupby


@dataclass(frozen=True)
class Costs:
    """거래 비용과 강제 청산 시각. fee·tax·slippage는 비율(0.0005 = 0.05%)."""
    fee: Decimal
    tax: Decimal
    slippage: Decimal
    exit_at: time


@dataclass(frozen=True)
class Trade:
    """완결된 거래 1건. 가격은 슬리피지 반영가, return_pct는 비용 반영 수익률(%)."""
    symbol: str
    entry_ts: datetime
    entry_price: Decimal
    exit_ts: datetime
    exit_price: Decimal
    return_pct: Decimal
    exit_reason: str


def _close(symbol, entry_bar, exit_ts, exit_base, reason, costs):
    """진입 봉과 청산 기준가로 슬리피지·수수료·세금을 반영한 Trade를 만든다."""
    buy = entry_bar.open * (1 + costs.slippage)
    sell = exit_base * (1 - costs.slippage)
    ret = (sell * (1 - costs.fee - costs.tax) / (buy * (1 + costs.fee)) - 1) * 100
    return Trade(symbol, entry_bar.ts, buy, exit_ts, sell, ret, reason)


def run_day(symbol, strategy, bars, costs):
    """한 종목 하루 봉(시각 오름차순)을 전략에 넘기고 체결 규칙에 따라 거래 목록을 만든다."""
    trades = []
    entry = None     # 보유 중이면 체결 봉
    pending = None   # 이번 봉 시가에 체결할 신호
    for bar in bars:
        if bar.ts.time() >= costs.exit_at:
            if entry:
                trades.append(_close(symbol, entry, bar.ts, bar.open, "close_time", costs))
            return trades
        if pending == "buy":
            entry = bar
        elif pending == "sell":
            trades.append(_close(symbol, entry, bar.ts, bar.open, "signal", costs))
            entry = None
        signal = strategy.on_bar(bar, entry.open if entry else None)
        # 미보유 중 buy, 보유 중 sell만 다음 봉에 체결한다
        if (signal == "buy" and entry is None) or (signal == "sell" and entry is not None):
            pending = signal
        else:
            pending = None
    if entry:
        last = bars[-1]
        trades.append(_close(symbol, entry, last.ts, last.close, "day_end", costs))
    return trades


def run(strategy_cls, params, bars_by_symbol, costs):
    """종목별·KST 날짜별로 새 전략 인스턴스를 만들어 run_day를 돌리고 전체 거래를 반환한다."""
    trades = []
    for symbol, bars in bars_by_symbol.items():
        for _, day in groupby(bars, key=lambda b: b.ts.date()):
            trades += run_day(symbol, strategy_cls(params), list(day), costs)
    return trades
```

`exit_at` 이상 봉에서 바로 `return`하는 이유: 그 뒤로는 매수가 금지되고 보유분은 방금 청산했으므로 더 볼 봉이 없다. 대기 중인 buy/sell 신호도 여기서 버려진다(sell은 `close_time` 청산으로 대체).

- [ ] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests -v`
Expected: `43 passed`

- [ ] **Step 5: 커밋**

```powershell
git add engine.py tests/test_engine.py
git commit -m "백테스트 엔진 체결 규칙 추가"
```

---

### Task 3: 전략 (ma_cross, orb)

**Files:**
- Create: `trader/strategies.py`
- Test: `trader/tests/test_strategies.py`

**Interfaces:**
- Consumes: `kis.Bar`, `kis.KST`. Task 2 엔진이 호출하는 `strategy_cls(params)`, `on_bar(bar, entry_price)` 규약
- Produces (`strategies.py`):
  - `class Strategy` — 클래스 속성 `name: str`, `defaults: dict`; `__init__(params: dict)`는 `self.p = {**defaults, **params}`; `on_bar(bar, entry_price: Decimal | None) -> "buy" | "sell" | None`
  - `class MaCross(Strategy)` — `defaults = {"short": 5, "long": 20}`
  - `class Orb(Strategy)` — `defaults = {"range_end": "09:30", "stop_pct": -1.0, "target_pct": 2.0}`
  - `STRATEGIES: dict[str, type[Strategy]] = {"ma_cross": MaCross, "orb": Orb}`

- [ ] **Step 1: 실패하는 테스트 작성**

`trader/tests/test_strategies.py`:

```python
from datetime import datetime, timedelta
from decimal import Decimal

from kis import KST, Bar
from strategies import STRATEGIES, MaCross, Orb


def bar_at(minutes_after_nine, close, high=None):
    """09:00부터 minutes_after_nine분 뒤 봉을 만든다. 시가·저가는 종가, 고가는 high 또는 종가."""
    ts = datetime(2026, 9, 11, 9, 0, tzinfo=KST) + timedelta(minutes=minutes_after_nine)
    c = Decimal(close)
    return Bar(ts, c, Decimal(high) if high else c, c, c, 100)


def feed(strategy, closes, entry_price=None):
    """종가 목록을 09:00부터 차례로 넘기고 신호 목록을 반환한다."""
    return [strategy.on_bar(bar_at(i, c), entry_price) for i, c in enumerate(closes)]


def test_registry_has_initial_strategies():
    """등록 dict에 초기 전략 2개가 이름으로 들어 있다."""
    assert STRATEGIES == {"ma_cross": MaCross, "orb": Orb}


def test_params_override_defaults():
    """전달한 파라미터가 기본값을 덮어쓴다."""
    assert MaCross({"short": 2}).p == {"short": 2, "long": 20}


def test_ma_cross_signals_only_on_cross_bars():
    """long개가 쌓이기 전에는 신호가 없고, 상향·하향 교차가 일어난 봉에서만 신호를 낸다."""
    closes = ["10", "10", "10", "13", "13", "7", "7", "7"]
    # short=2, long=3 평균: i2 (10,10) i3 (11.5,11) 상향 i4 (13,12) i5 (10,11) 하향 i6 (7,9) i7 (7,7)
    assert feed(MaCross({"short": 2, "long": 3}), closes) == [
        None, None, None, "buy", None, "sell", None, None]


def test_orb_buys_once_on_breakout_after_range():
    """range_end 전 고가를 종가로 돌파한 첫 봉에서만 buy를 낸다."""
    orb = Orb({"range_end": "09:02"})
    signals = [
        orb.on_bar(bar_at(0, "100", high="105"), None),
        orb.on_bar(bar_at(1, "100"), None),
        orb.on_bar(bar_at(2, "105"), None),   # 고가와 같음: 돌파 아님
        orb.on_bar(bar_at(3, "106"), None),   # 돌파
        orb.on_bar(bar_at(4, "107"), None),   # 이미 신호 냄
    ]
    assert signals == [None, None, None, "buy", None]


def test_orb_no_signal_without_range_bars():
    """range_end 전 봉이 없는 날은 신호를 내지 않는다."""
    orb = Orb({"range_end": "09:00"})
    assert feed(orb, ["100", "200"]) == [None, None]


def test_orb_sells_at_stop_or_target():
    """보유 중 종가 수익률이 stop_pct 이하이거나 target_pct 이상이면 sell을 낸다."""
    orb = Orb({"range_end": "09:00", "stop_pct": -1.0, "target_pct": 2.0})
    entry = Decimal("100")
    assert [orb.on_bar(bar_at(i, c), entry) for i, c in enumerate(["99.5", "99", "101", "102"])] == [
        None, "sell", None, "sell"]
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests\test_strategies.py -v`
Expected: ERROR `ModuleNotFoundError: No module named 'strategies'`

- [ ] **Step 3: 구현**

`trader/strategies.py`:

```python
"""백테스트 전략. 엔진이 종목·날짜마다 인스턴스를 새로 만들고 봉을 시각순으로 하나씩 넘긴다."""
from collections import deque
from datetime import time
from decimal import Decimal


class Strategy:
    """전략 기본형. defaults에 파라미터 기본값을 두고 on_bar에서 신호를 낸다."""
    name = ""
    defaults = {}

    def __init__(self, params):
        """기본값에 전달받은 파라미터를 덮어써 self.p에 둔다."""
        self.p = {**self.defaults, **params}

    def on_bar(self, bar, entry_price):
        """봉 하나를 받아 "buy"/"sell"/None을 반환한다. entry_price는 보유 중 체결 시가, 미보유면 None."""
        raise NotImplementedError


class MaCross(Strategy):
    """단기 종가 이동평균이 장기 이동평균을 상향 돌파하면 매수, 하향 돌파하면 매도."""
    name = "ma_cross"
    defaults = {"short": 5, "long": 20}

    def __init__(self, params):
        """최근 long개 종가와 직전 봉의 (단기, 장기) 평균을 보관한다."""
        super().__init__(params)
        self.closes = deque(maxlen=self.p["long"])
        self.prev = None

    def on_bar(self, bar, entry_price):
        """종가를 누적하고 직전 봉 대비 평균 교차가 일어난 봉에서 신호를 낸다."""
        self.closes.append(bar.close)
        if len(self.closes) < self.p["long"]:
            return None
        closes = list(self.closes)
        cur = (sum(closes[-self.p["short"]:]) / self.p["short"], sum(closes) / self.p["long"])
        prev, self.prev = self.prev, cur
        if prev is None:
            return None
        if prev[0] <= prev[1] and cur[0] > cur[1]:
            return "buy"
        if prev[0] >= prev[1] and cur[0] < cur[1]:
            return "sell"
        return None


class Orb(Strategy):
    """range_end 전 고가를 종가로 돌파하면 하루 1회 매수, 손절·목표 수익률에 닿으면 매도."""
    name = "orb"
    defaults = {"range_end": "09:30", "stop_pct": -1.0, "target_pct": 2.0}

    def __init__(self, params):
        """범위 종료 시각, 손절·목표 비율, 범위 고가, 오늘 매수 신호 여부를 준비한다."""
        super().__init__(params)
        self.range_end = time.fromisoformat(self.p["range_end"])
        self.stop = Decimal(str(self.p["stop_pct"]))
        self.target = Decimal(str(self.p["target_pct"]))
        self.range_high = None
        self.signaled = False

    def on_bar(self, bar, entry_price):
        """범위 시간에는 고가를 누적하고, 이후에는 돌파 매수·손절/목표 매도 신호를 낸다."""
        if bar.ts.time() < self.range_end:
            self.range_high = bar.high if self.range_high is None else max(self.range_high, bar.high)
            return None
        if entry_price is not None:
            change = (bar.close / entry_price - 1) * 100
            return "sell" if change <= self.stop or change >= self.target else None
        if self.range_high is not None and not self.signaled and bar.close > self.range_high:
            self.signaled = True
            return "buy"
        return None


STRATEGIES = {"ma_cross": MaCross, "orb": Orb}
```

- [ ] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests -v`
Expected: `49 passed`

- [ ] **Step 5: 커밋**

```powershell
git add strategies.py tests/test_strategies.py
git commit -m "이동평균 교차·시가범위 돌파 전략 추가"
```

---

### Task 4: 백테스트 결과 테이블과 저장

**Files:**
- Modify: `trader/schema.sql` (파일 끝에 결과 테이블 2개 추가)
- Modify: `trader/tests/conftest.py` (TRUNCATE 대상 추가)
- Modify: `trader/store.py` (import 추가, `save_run` 추가)
- Test: `trader/tests/test_store.py` (테스트 3개 추가)

**Interfaces:**
- Consumes: Task 1 `store.py`·`schema.sql`, Task 2 `engine.Trade`
- Produces:
  - 테이블 `backtest_runs(id, created_at, strategy, params jsonb, source, symbols text[], date_from, date_to, costs jsonb)`, `backtest_trades(run_id, symbol, entry_ts, entry_price, exit_ts, exit_price, return_pct, exit_reason)`
  - `store.save_run(conn, run: dict, trades: list[Trade]) -> int` — `run` 키: `strategy`, `params`(dict), `source`, `symbols`(list[str]), `date_from`, `date_to`(date), `costs`(dict). 한 트랜잭션
  - 픽스처 `conn`이 `backtest_runs`·`backtest_trades`도 비운다

- [ ] **Step 1: 실패하는 테스트 추가**

`trader/tests/test_store.py` 맨 위의 import 블록과 상수 줄(`TODAY`, 있으면 `SCHEMA`까지)을 다음으로 바꾼다:

```python
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest

import engine
import store
from kis import KST, Bar

TODAY = date(2026, 9, 14)
SCHEMA = (Path(__file__).resolve().parents[1] / "schema.sql").read_text(encoding="utf-8")
```

파일 끝에 추가:

```python
def run_info():
    """save_run에 넘길 실행 정보 예시."""
    return {"strategy": "orb", "params": {"range_end": "09:30"}, "source": "yahoo",
            "symbols": ["A"], "date_from": date(2026, 9, 11), "date_to": date(2026, 9, 11),
            "costs": {"fee": "0.00015", "tax": "0.002", "slippage": "0.0005", "exit_at": "15:15"}}


def trade(minute):
    """09:minute에 진입해 1분 뒤 청산한 테스트용 거래."""
    t = datetime(2026, 9, 11, 9, minute, tzinfo=KST)
    return engine.Trade("A", t, Decimal("100.05"), t.replace(minute=minute + 1),
                        Decimal("100.95"), Decimal("0.6789"), "signal")


def test_save_run_stores_run_and_trades(conn):
    """실행과 거래를 저장하고 run id를 반환한다."""
    run_id = store.save_run(conn, run_info(), [trade(0), trade(5)])
    assert conn.execute("SELECT strategy, params, symbols, costs->>'exit_at' FROM backtest_runs WHERE id = %s",
                        (run_id,)).fetchone() == ("orb", {"range_end": "09:30"}, ["A"], "15:15")
    rows = conn.execute("SELECT entry_price, return_pct, exit_reason FROM backtest_trades "
                        "WHERE run_id = %s ORDER BY entry_ts", (run_id,)).fetchall()
    assert rows == [(Decimal("100.05"), Decimal("0.6789"), "signal")] * 2


def test_save_run_without_trades(conn):
    """거래가 없어도 실행은 저장된다."""
    run_id = store.save_run(conn, run_info(), [])
    assert conn.execute("SELECT count(*) FROM backtest_runs WHERE id = %s", (run_id,)).fetchone()[0] == 1


def test_save_run_rolls_back_on_trade_error(conn):
    """거래 저장이 실패하면 실행 행도 남지 않는다."""
    with pytest.raises(psycopg.errors.UniqueViolation):
        store.save_run(conn, run_info(), [trade(0), trade(0)])
    assert conn.execute("SELECT count(*) FROM backtest_runs").fetchone()[0] == 0
```

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests\test_store.py -v`
Expected: 새 테스트 3개 FAIL (`module 'store' has no attribute 'save_run'`), 기존 9개 PASS

- [ ] **Step 3: 결과 테이블 추가**

`trader/schema.sql` 파일 끝에 추가:

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
    costs       jsonb       NOT NULL    -- {"fee":"0.00015","tax":"0.002","slippage":"0.0005","exit_at":"15:15"}
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

- [ ] **Step 4: 픽스처가 결과 테이블도 비우게 수정**

`trader/tests/conftest.py`에서

```python
        c.execute("TRUNCATE minute_bars, collect_runs")
```

를 다음으로 바꾼다:

```python
        c.execute("TRUNCATE minute_bars, collect_runs, backtest_runs CASCADE")
```

- [ ] **Step 5: save_run 구현**

`trader/store.py` import 블록을 다음으로 바꾼다:

```python
"""minute_bars·collect_runs·backtest 테이블 저장과 조회."""
from datetime import datetime, time, timedelta

from psycopg.types.json import Jsonb

from kis import KST, Bar
```

`load_bars` 함수 바로 아래에 추가:

```python
def save_run(conn, run, trades):
    """백테스트 실행 정보와 거래 내역을 한 트랜잭션으로 저장하고 run id를 반환한다."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "INSERT INTO backtest_runs (strategy, params, source, symbols, date_from, date_to, costs) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (run["strategy"], Jsonb(run["params"]), run["source"], run["symbols"],
             run["date_from"], run["date_to"], Jsonb(run["costs"])),
        )
        run_id = cur.fetchone()[0]
        cur.executemany(
            "INSERT INTO backtest_trades (run_id, symbol, entry_ts, entry_price, exit_ts, "
            "exit_price, return_pct, exit_reason) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            [(run_id, t.symbol, t.entry_ts, t.entry_price, t.exit_ts, t.exit_price,
              t.return_pct, t.exit_reason) for t in trades],
        )
    return run_id
```

- [ ] **Step 6: 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests -v`
Expected: `52 passed`

- [ ] **Step 7: 커밋**

```powershell
git add schema.sql store.py tests/conftest.py tests/test_store.py
git commit -m "백테스트 결과 테이블과 저장 추가"
```

---

### Task 5: Yahoo 1분봉 적재

**Files:**
- Create: `trader/load_yahoo.py`
- Create: `trader/tests/fixtures/yahoo_chart.json`
- Test: `trader/tests/test_load_yahoo.py`

**Interfaces:**
- Consumes: Task 1 `store.save_bars(conn, symbol, bars, source)`, `collector.read_symbols(path) -> list[str]`, `kis.KST`, `kis.Bar`, 픽스처 `conn`
- Produces (`load_yahoo.py`):
  - `class YahooError(Exception)`
  - `fetch_chart(ticker: str, send=requests.get) -> dict` — Yahoo `chart.result[0]`
  - `fetch_symbol(code: str, send=requests.get) -> tuple[str, dict]` — (`"005930.KS"` 또는 `".KQ"` 티커, result)
  - `to_bars(result: dict, today: date) -> list[Bar]`
  - `main(argv: list[str] | None = None, send=requests.get, today: date | None = None) -> int`

- [ ] **Step 1: 샘플 응답 파일 작성**

`trader/tests/fixtures/yahoo_chart.json` (2026-09-15 실제 응답 형태 축약. timestamp는 2026-09-11 09:00·09:01·09:02 KST, 2026-09-15 09:00 KST):

```json
{
  "chart": {
    "result": [
      {
        "meta": {"currency": "KRW", "symbol": "005930.KS", "exchangeName": "KSC", "exchangeTimezoneName": "Asia/Seoul", "dataGranularity": "1m", "range": "8d"},
        "timestamp": [1789084800, 1789084860, 1789084920, 1789430400],
        "indicators": {
          "quote": [
            {
              "open":   [271000.0, null, 271500.0, 280000.0],
              "high":   [271500.0, null, 272000.0, 280500.0],
              "low":    [270500.0, null, 271500.0, 279500.0],
              "close":  [271500.0, null, 271500.0, 280000.0],
              "volume": [15234, null, null, 9000]
            }
          ]
        }
      }
    ],
    "error": null
  }
}
```

- [ ] **Step 2: 실패하는 테스트 작성**

`trader/tests/test_load_yahoo.py`:

```python
import json
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import load_yahoo
from kis import KST, Bar

TODAY = date(2026, 9, 15)
CHART = json.loads((Path(__file__).parent / "fixtures" / "yahoo_chart.json").read_text(encoding="utf-8"))
NOT_FOUND = {"chart": {"result": None,
                       "error": {"code": "Not Found", "description": "No data found, symbol may be delisted"}}}


class FakeResponse:
    """requests.Response 대역: status_code와 json()만 흉내 낸다."""

    def __init__(self, body, status=200):
        self.status_code = status
        self._body = body

    def json(self):
        """준비된 본문을 돌려준다."""
        return self._body


def fake_yahoo(ok_tickers):
    """ok_tickers에 든 티커만 샘플 차트를, 나머지는 Not Found를 돌려주는 send 대역을 만든다."""
    calls = []

    def send(url, **kwargs):
        ticker = url.rsplit("/", 1)[1]
        calls.append(ticker)
        assert kwargs["params"] == {"interval": "1m", "range": "8d"}
        return FakeResponse(CHART) if ticker in ok_tickers else FakeResponse(NOT_FOUND, 404)

    send.calls = calls
    return send


def test_to_bars_converts_and_skips_null_and_today():
    """UTC epoch를 KST 봉으로 바꾸고, OHLC가 빈 봉과 오늘 봉은 뺀다. 거래량 null은 0."""
    bars = load_yahoo.to_bars(CHART["chart"]["result"][0], TODAY)
    assert bars == [
        Bar(datetime(2026, 9, 11, 9, 0, tzinfo=KST), Decimal("271000"), Decimal("271500"),
            Decimal("270500"), Decimal("271500"), 15234),
        Bar(datetime(2026, 9, 11, 9, 2, tzinfo=KST), Decimal("271500"), Decimal("272000"),
            Decimal("271500"), Decimal("271500"), 0),
    ]


def test_fetch_symbol_falls_back_to_kosdaq():
    """.KS가 Not Found면 .KQ로 다시 요청한다."""
    send = fake_yahoo({"035720.KQ"})
    ticker, result = load_yahoo.fetch_symbol("035720", send)
    assert ticker == "035720.KQ"
    assert send.calls == ["035720.KS", "035720.KQ"]
    assert result["timestamp"][0] == 1789084800


def test_main_saves_yahoo_source_and_continues_after_failure(conn, monkeypatch, capsys):
    """실패 종목은 출력만 하고 다음 종목을 저장하며, 실패가 있으면 1을 반환한다."""
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    code = load_yahoo.main(["999999", "005930"], send=fake_yahoo({"005930.KS"}), today=TODAY)
    assert code == 1
    assert conn.execute("SELECT source, symbol, count(*) FROM minute_bars GROUP BY 1, 2").fetchall() == [
        ("yahoo", "005930", 2)]
    out = capsys.readouterr().out
    assert "999999 실패: YahooError" in out
    assert "005930 yahoo(.KS) 2봉" in out


def test_main_returns_0_when_all_succeed(conn, monkeypatch):
    """전 종목 성공이면 0을 반환한다."""
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    assert load_yahoo.main(["005930"], send=fake_yahoo({"005930.KS"}), today=TODAY) == 0
```

`monkeypatch.setenv("DATABASE_URL", ...)`가 `main` 안의 `load_dotenv`보다 우선한다(`load_dotenv`는 이미 있는 환경변수를 덮어쓰지 않음). 그래서 `main`이 운영 DB가 아닌 테스트 DB에 쓴다.

- [ ] **Step 3: 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests\test_load_yahoo.py -v`
Expected: ERROR `ModuleNotFoundError: No module named 'load_yahoo'`

- [ ] **Step 4: 구현**

`trader/load_yahoo.py`:

```python
"""Yahoo Finance 1분봉을 minute_bars에 source='yahoo'로 적재하는 일회성 도구."""
import os
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import psycopg
import requests
from dotenv import load_dotenv

import store
from collector import read_symbols
from kis import KST, Bar

ROOT = Path(__file__).resolve().parent
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{}"


class YahooError(Exception):
    """Yahoo 응답이 오류이거나 형식이 다름."""


def fetch_chart(ticker, send=requests.get):
    """Yahoo 차트 API에서 최근 8일 1분봉 응답의 result 객체를 받아온다."""
    resp = send(CHART_URL.format(ticker), params={"interval": "1m", "range": "8d"},
                headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
    try:
        chart = resp.json()["chart"]
    except (ValueError, KeyError, TypeError):
        raise YahooError(f"HTTP{resp.status_code}") from None
    if chart.get("error") or not chart.get("result"):
        raise YahooError(f"{ticker} 데이터 없음")
    return chart["result"][0]


def fetch_symbol(code, send=requests.get):
    """코스피(.KS)로 받아보고 실패하면 코스닥(.KQ)으로 한 번 더 시도해 (티커, result)를 반환한다."""
    try:
        return f"{code}.KS", fetch_chart(f"{code}.KS", send)
    except YahooError:
        return f"{code}.KQ", fetch_chart(f"{code}.KQ", send)


def to_bars(result, today):
    """차트 result를 KST Bar 목록으로 바꾼다. OHLC가 빈 봉과 오늘 날짜 봉은 뺀다."""
    quote = result["indicators"]["quote"][0]
    bars = []
    for i, epoch in enumerate(result.get("timestamp") or []):
        ts = datetime.fromtimestamp(epoch, KST)
        ohlc = [quote[k][i] for k in ("open", "high", "low", "close")]
        if ts.date() >= today or None in ohlc:
            continue
        bars.append(Bar(ts, *(Decimal(str(x)) for x in ohlc), int(quote["volume"][i] or 0)))
    return bars


def main(argv=None, send=requests.get, today=None):
    """종목별로 Yahoo 1분봉을 받아 저장하고 결과를 출력한다. 실패 종목이 있으면 1을 반환한다."""
    load_dotenv(ROOT / ".env")
    argv = sys.argv[1:] if argv is None else argv
    codes = argv or read_symbols(ROOT / "symbols.txt")
    today = today or datetime.now(KST).date()
    failed = 0
    try:
        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
            for code in codes:
                try:
                    ticker, result = fetch_symbol(code, send)
                    bars = to_bars(result, today)
                    store.save_bars(conn, code, bars, source="yahoo")
                    print(f"{code} yahoo({ticker[-3:]}) {len(bars)}봉")
                except Exception as e:
                    failed += 1
                    print(f"{code} 실패: {type(e).__name__}")
    except Exception as e:
        print(f"실행 실패: {type(e).__name__} {e}")
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests -v`
Expected: `56 passed`

- [ ] **Step 6: 커밋**

```powershell
git add load_yahoo.py tests/test_load_yahoo.py tests/fixtures/yahoo_chart.json
git commit -m "Yahoo 1분봉 임시 적재 도구 추가"
```

---

### Task 6: 백테스트 CLI, README, 실데이터 검증

**Files:**
- Create: `trader/backtest.py`
- Modify: `trader/README.md` (섹션 2개 추가)
- Test: `trader/tests/test_backtest.py`

**Interfaces:**
- Consumes: Task 1 `store.load_bars`, `store.save_bars`; Task 2 `engine.Costs`, `engine.Trade`, `engine.run`; Task 3 `strategies.STRATEGIES`(각 클래스의 `defaults`); Task 4 `store.save_run`; Task 5 `load_yahoo.py`(수동 검증)
- Produces (`backtest.py`):
  - `class UsageError(Exception)`
  - `parse_args(argv: list[str]) -> tuple[argparse.Namespace, dict[str, dict]]`
  - `summarize(trades: list[Trade]) -> dict` — 키 `trades`, (거래가 있으면) `win_rate`, `avg`, `sum`, `mdd`, `hold_min`
  - `format_summary(run_id, name, params, symbol_count, args, s) -> str`
  - `main(argv: list[str] | None = None) -> int`

- [ ] **Step 1: 실패하는 테스트 작성**

`trader/tests/test_backtest.py`:

```python
import os
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

import backtest
import store
from kis import KST, Bar

ARGS = ["--source", "yahoo", "--from", "2026-09-11", "--to", "2026-09-11"]


@pytest.fixture
def db_env(conn, monkeypatch):
    """backtest.main이 테스트 DB에 접속하도록 DATABASE_URL을 바꾸고 연결을 넘겨준다."""
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    return conn


def breakout_day():
    """09:00~09:39 봉. 09:30부터 가격이 100에서 110으로 뛰어 두 전략 모두 매수 신호를 낸다."""
    t0 = datetime(2026, 9, 11, 9, 0, tzinfo=KST)
    out = []
    for m in range(40):
        p = Decimal(110 if m >= 30 else 100)
        out.append(Bar(t0 + timedelta(minutes=m), p, p, p, p, 100))
    return out


def test_parse_args_applies_params_to_matching_strategies():
    """--param은 그 키를 가진 전략에만 적용되고 기본값 형태로 변환된다."""
    _, params = backtest.parse_args(["--strategy", "ma_cross,orb", "--param", "short=2",
                                     "--param", "stop_pct=-0.5", *ARGS])
    assert params == {"ma_cross": {"short": 2, "long": 20},
                      "orb": {"range_end": "09:30", "stop_pct": -0.5, "target_pct": 2.0}}


def test_summarize_computes_metrics():
    """승률·평균·합계·최대 낙폭·평균 보유 분을 청산 시각순 누적으로 계산한다."""
    t0 = datetime(2026, 9, 11, 9, 0, tzinfo=KST)

    def tr(minute, ret):
        return backtest.engine.Trade("A", t0 + timedelta(minutes=minute), Decimal(1),
                                     t0 + timedelta(minutes=minute + 2), Decimal(1), Decimal(ret), "signal")

    s = backtest.summarize([tr(10, "-2"), tr(0, "1"), tr(20, "0.5")])
    assert s["trades"] == 3
    assert s["win_rate"] == pytest.approx(66.666, abs=1e-2)
    assert s["sum"] == Decimal("-0.5")
    assert s["mdd"] == Decimal("2")   # 누적 1 → -1 (고점 1 대비 2%p)
    assert s["hold_min"] == 2
    assert backtest.summarize([]) == {"trades": 0}


def test_main_saves_run_per_strategy(db_env, capsys):
    """전략마다 run을 1건씩 저장하고 거래를 기록하며, 다른 source 봉은 쓰지 않는다."""
    store.save_bars(db_env, "A", breakout_day(), source="yahoo")
    store.save_bars(db_env, "B", breakout_day(), source="kis")
    code = backtest.main(["--strategy", "ma_cross,orb", "--param", "short=2", "--param", "long=3", *ARGS])
    assert code == 0
    runs = db_env.execute("SELECT id, strategy, params, symbols FROM backtest_runs ORDER BY id").fetchall()
    assert [(s, p, sym) for _, s, p, sym in runs] == [
        ("ma_cross", {"short": 2, "long": 3}, ["A"]),
        ("orb", {"range_end": "09:30", "stop_pct": -1.0, "target_pct": 2.0}, ["A"]),
    ]
    trades = db_env.execute("SELECT run_id, symbol, entry_ts, exit_reason FROM backtest_trades "
                            "ORDER BY run_id").fetchall()
    entry = datetime(2026, 9, 11, 9, 31, tzinfo=KST)
    assert [(sym, ts, reason) for _, sym, ts, reason in trades] == [("A", entry, "day_end")] * 2
    assert "거래 1건" in capsys.readouterr().out


def test_main_returns_1_without_bars(db_env, capsys):
    """조건에 봉이 없으면 저장 없이 1을 반환한다."""
    assert backtest.main(["--strategy", "orb", *ARGS]) == 1
    assert "봉 데이터 없음" in capsys.readouterr().out
    assert db_env.execute("SELECT count(*) FROM backtest_runs").fetchone()[0] == 0


@pytest.mark.parametrize("extra", [
    ["--strategy", "nope"],
    ["--strategy", "orb", "--param", "short=2"],
    ["--strategy", "ma_cross", "--param", "short=abc"],
    ["--strategy", "orb", "--param", "broken"],
])
def test_main_returns_2_for_bad_args(db_env, extra):
    """없는 전략·전략에 없는 파라미터·잘못된 값·형식 오류는 2를 반환하고 저장하지 않는다."""
    store.save_bars(db_env, "A", breakout_day(), source="yahoo")
    assert backtest.main([*extra, *ARGS]) == 2
    assert db_env.execute("SELECT count(*) FROM backtest_runs").fetchone()[0] == 0


def test_main_returns_2_when_from_after_to(db_env):
    """--from이 --to보다 늦으면 2를 반환한다."""
    assert backtest.main(["--strategy", "orb", "--source", "yahoo",
                          "--from", "2026-09-12", "--to", "2026-09-11"]) == 2
```

`breakout_day`에서 두 전략이 거래 1건씩 내는 이유:
- `ma_cross(short=2, long=3)`: 09:30 종가 [100,100,110] → 단기 105 > 장기 103.3, 직전 봉은 100=100 → buy, 09:31 시가 체결. 이후 평균이 같아 sell 없음 → 09:39 `day_end`
- `orb`: 09:30 전 고가 100, 09:30 종가 110 돌파 → buy, 09:31 체결, 수익률 0%(비용 제외 전)라 stop·target 미도달 → `day_end`

- [ ] **Step 2: 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests\test_backtest.py -v`
Expected: ERROR `ModuleNotFoundError: No module named 'backtest'`

- [ ] **Step 3: 구현**

`trader/backtest.py`:

```python
"""백테스트 실행 CLI: 봉 조회 → 전략별 엔진 실행 → 결과 저장 → 콘솔 요약."""
import argparse
import os
import sys
from datetime import date, time
from decimal import Decimal
from pathlib import Path

import psycopg
from dotenv import load_dotenv

import engine
import store
from strategies import STRATEGIES

ROOT = Path(__file__).resolve().parent


class UsageError(Exception):
    """잘못된 실행 인자."""


def parse_args(argv):
    """인자를 해석·검증해 (args, {전략이름: 최종 파라미터})를 반환한다. 잘못되면 UsageError."""
    p = argparse.ArgumentParser(description="분봉 백테스트")
    p.add_argument("--strategy", required=True, help="쉼표 구분 전략 이름: " + ", ".join(STRATEGIES))
    p.add_argument("--source", required=True, help="minute_bars.source (kis, yahoo)")
    p.add_argument("--from", dest="date_from", required=True, type=date.fromisoformat)
    p.add_argument("--to", dest="date_to", required=True, type=date.fromisoformat)
    p.add_argument("--symbols", help="쉼표 구분 종목코드, 없으면 전 종목")
    p.add_argument("--param", action="append", default=[], help="key=value, 여러 번 지정")
    p.add_argument("--fee", type=Decimal, default=Decimal("0.00015"))
    p.add_argument("--tax", type=Decimal, default=Decimal("0.002"))
    p.add_argument("--slippage", type=Decimal, default=Decimal("0.0005"))
    p.add_argument("--exit-at", type=time.fromisoformat, default=time(15, 15))
    args = p.parse_args(argv)

    names = args.strategy.split(",")
    unknown = [n for n in names if n not in STRATEGIES]
    if unknown:
        raise UsageError(f"알 수 없는 전략: {', '.join(unknown)} (가능: {', '.join(STRATEGIES)})")
    if args.date_from > args.date_to:
        raise UsageError("--from이 --to보다 늦음")

    overrides = {}
    for item in args.param:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise UsageError(f"--param 형식은 key=value: {item}")
        overrides[key] = value
    unused = [k for k in overrides if not any(k in STRATEGIES[n].defaults for n in names)]
    if unused:
        raise UsageError(f"선택한 전략에 없는 파라미터: {', '.join(unused)}")

    params = {}
    for name in names:
        defaults = STRATEGIES[name].defaults
        try:
            params[name] = {**defaults, **{k: type(defaults[k])(v)
                                           for k, v in overrides.items() if k in defaults}}
        except ValueError as e:
            raise UsageError(f"파라미터 값 오류: {e}") from None
    return args, params


def summarize(trades):
    """거래 목록의 거래 수·승률·평균/합계 수익률·최대 낙폭(%p)·평균 보유 분을 계산한다."""
    if not trades:
        return {"trades": 0}
    rets = [t.return_pct for t in sorted(trades, key=lambda t: t.exit_ts)]
    cum = peak = mdd = Decimal(0)
    for r in rets:
        cum += r
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    n = len(rets)
    return {
        "trades": n,
        "win_rate": sum(r > 0 for r in rets) / n * 100,
        "avg": sum(rets) / n,
        "sum": sum(rets),
        "mdd": mdd,
        "hold_min": sum((t.exit_ts - t.entry_ts).total_seconds() for t in trades) / 60 / n,
    }


def format_summary(run_id, name, params, symbol_count, args, s):
    """실행 1건의 콘솔 요약 문구를 만든다."""
    head = (f"[run {run_id}] {name} {params} | {args.source} {symbol_count}종목 "
            f"{args.date_from}~{args.date_to}")
    if not s["trades"]:
        return head + "\n  거래 0건"
    return (head + f"\n  거래 {s['trades']}건 | 승률 {s['win_rate']:.1f}% | 평균 {s['avg']:.3f}% "
            f"| 합계 {s['sum']:.2f}% | 최대낙폭 {s['mdd']:.2f}%p | 평균보유 {s['hold_min']:.1f}분")


def main(argv=None):
    """백테스트 1회 실행. 성공 0, 봉 없음·DB 오류 1, 잘못된 인자 2를 반환한다."""
    try:
        args, params = parse_args(sys.argv[1:] if argv is None else argv)
    except UsageError as e:
        print(f"인자 오류: {e}")
        return 2
    load_dotenv(ROOT / ".env")
    costs = engine.Costs(args.fee, args.tax, args.slippage, args.exit_at)
    symbols = args.symbols.split(",") if args.symbols else None
    try:
        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
            bars = store.load_bars(conn, args.source, args.date_from, args.date_to, symbols)
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
                              "exit_at": args.exit_at.strftime("%H:%M")},
                }, trades)
                print(format_summary(run_id, name, p, len(bars), args, summarize(trades)))
    except Exception as e:
        print(f"실행 실패: {type(e).__name__} {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests -v`
Expected: `65 passed`

- [ ] **Step 5: README에 섹션 추가**

`trader/README.md`의 `## 누락 확인` 섹션 바로 앞에 추가:

````markdown
## 운영 DB 스키마 갱신

백테스터 추가로 `minute_bars`에 `source` 컬럼과 결과 테이블이 생겼다. 기존 데이터는 `source='kis'`로 보존된다.

```powershell
D:\PIE\PostgreSQL_15\bin\psql.exe -h localhost -U trader -d trader -f schema.sql
```

## Yahoo 임시 데이터 적재

KIS 데이터가 쌓이기 전 개발·검증용. 최근 약 7거래일, 하루 360봉(09:00~14:59, 15시 이후 봉 없음). 비공식 API라 언제든 막힐 수 있다.

```powershell
.\.venv\Scripts\python load_yahoo.py              # symbols.txt 전 종목
.\.venv\Scripts\python load_yahoo.py 005930 000660
```

## 백테스트

```powershell
.\.venv\Scripts\python backtest.py --strategy ma_cross,orb --source yahoo --from 2026-09-04 --to 2026-09-14
```

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--strategy` | (필수) | 쉼표 구분. 전략마다 실행 1건 저장 |
| `--source` | (필수) | `kis` 또는 `yahoo` |
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

````

- [ ] **Step 6: 운영 DB 스키마 갱신과 실데이터 수동 검증**

운영 DB(`trader`)에 스키마를 적용한다. `trader` 사용자 비밀번호가 필요하므로 **사용자에게 실행을 요청**한다:

```powershell
D:\PIE\PostgreSQL_15\bin\psql.exe -h localhost -U trader -d trader -f schema.sql
```

그다음 실행:

```powershell
.\.venv\Scripts\python load_yahoo.py 005930 000660
```

Expected: `005930 yahoo(.KS) 2520봉`, `000660 yahoo(.KS) 2520봉` 형태 (봉 수는 실행일에 따라 다를 수 있음, 거래일당 약 360봉)

```powershell
.\.venv\Scripts\python backtest.py --strategy ma_cross,orb --source yahoo --from <실행일 8일 전> --to <실행일 전날>
```

Expected: `[run N] ma_cross ...`, `[run N+1] orb ...` 두 블록과 각 `거래 N건 | 승률 ...` 줄, 종료 코드 0

거래 대조 (psql 또는 Python으로 실행):

```sql
SELECT t.symbol, t.entry_ts, t.entry_price, t.exit_ts, t.exit_price, t.return_pct, t.exit_reason,
       (SELECT open FROM minute_bars b WHERE b.source = 'yahoo' AND b.symbol = t.symbol AND b.ts = t.entry_ts) AS entry_open,
       (SELECT open FROM minute_bars b WHERE b.source = 'yahoo' AND b.symbol = t.symbol AND b.ts = t.exit_ts) AS exit_open,
       (SELECT close FROM minute_bars b WHERE b.source = 'yahoo' AND b.symbol = t.symbol AND b.ts = t.exit_ts) AS exit_close
FROM backtest_trades t
WHERE t.run_id = (SELECT max(id) FROM backtest_runs WHERE strategy = 'orb')
LIMIT 3;
```

확인: `entry_price = entry_open × 1.0005`, `exit_reason='day_end'`이면 `exit_price = exit_close × 0.9995`, 그 외는 `exit_price = exit_open × 0.9995`. README의 일별·시간대별 조회 쿼리가 행을 돌려주는지 확인.

- [ ] **Step 7: 커밋**

```powershell
git add backtest.py tests/test_backtest.py README.md
git commit -m "백테스트 실행 CLI와 사용법 추가"
```

- [ ] **Step 8: 남은 작업 보고**

- 매도 거래세 기본값 0.20%는 실제 세율 확인 필요
- 2b(Next.js 조회 화면: 일별·시간대 조건)는 별도 스펙으로 진행
