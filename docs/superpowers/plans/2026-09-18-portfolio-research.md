# 포트폴리오 전략 연구 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 미리 정한 통과 기준으로 전략 A(추세 돌파+시장 필터)·B(월간 모멘텀+시장 필터)를 편향 없는 월별 대상 종목·10만 원 포트폴리오로 검증하고 판정표를 낸다.

**Architecture:** `universe.py`가 전체 상장 보통주 목록과 월별 거래대금 상위 100(DB 윈도 쿼리)을 만들고, `portfolio.py`가 float 기반 포트폴리오 시뮬레이터(현금·정수 주·동시 보유 N·다음 날 시가 체결·시장 필터)와 전략 A·B·비교 기준을 제공한다. `metrics.py`는 지표와 통과 판정, `research.py`는 수집 모드와 실험·보고서를 맡는다. 기존 운영 코드(수집기·모의투자·선정기·화면)는 바꾸지 않는다.

**Tech Stack:** Python 3.11, psycopg 3, python-dotenv, pytest, PostgreSQL 15 + TimescaleDB 2.15

**Spec:** `docs/superpowers/specs/2026-09-18-portfolio-research-design.md`

## Global Constraints

- 작업 폴더는 `trader/`. 테스트: `.\.venv\Scripts\python -m pytest tests -v` (테스트 DB는 `.env`의 `TEST_DATABASE_URL`, TimescaleDB 확장 있음)
- 새 의존성 금지: `requests`, `psycopg[binary]`, `python-dotenv`, `pytest`만 사용
- 모든 함수·메서드에 무엇을 하는지 한국어 docstring (사용자 규칙)
- SQL은 테이블 인덱스를 확인해 쓰고, 운영 데이터로 `EXPLAIN ANALYZE`를 확인한다 (사용자 규칙). `daily_bars` 기본키는 `(symbol, day)`, `day` 기준 하이퍼테이블(1년 청크)
- 출력에 토큰·secret·접속 문자열을 남기지 않는다. 예외는 종류와 `TossError.code`만
- 연구 시뮬레이션은 `float`. 비용 `fee=0.00015`(양쪽), `tax=0.002`(매도), `slippage=0.0005`(양쪽)
- 기준 지수 `069500`(KODEX 200), 시장 필터는 200일 평균, 대상 종목은 매월 첫 거래일 전날까지 250거래일 거래대금 상위 100
- 전략 A 조합 `(20,50,20)`, `(20,100,20)`, `(55,50,20)`, `(55,100,20)` × 동시 보유 3·5. 전략 B 조합 6·12개월 × 3·5종목
- 통과 기준: 검증 CAGR > 0 이고 개발 CAGR(> 0)의 50% 이상 / 검증 샤프 > KODEX 200 보유·정기 매수 샤프 / 개발·검증 MDD ≤ 20% / 완전한 연도 70% 이상 플러스 / 검증 청산 거래 100건 이상
- 기존 운영 코드 동작 불변. 커밋 메시지는 한국어로 간결하게, 마지막 줄에 `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`
- 시작 기준선: 기존 테스트 239개 통과

## 스펙 대비 세부 결정

- 수집 기간은 11년(개발 10년 + 지표 준비 1년). 개발 구간 시작 시점에도 250거래일 대상 종목·200일 필터·12개월 모멘텀을 계산할 수 있게 한다
- 매수 수량은 수수료까지 포함한 단가로 나눈다: `floor(min(평가액 ÷ N, 현금) ÷ (시가 × (1+슬리피지) × (1+수수료)))`. 현금이 음수가 되지 않게 한다
- 대상 종목의 "전날"은 기준 지수의 전 거래일이다. 그날 거래가 없던 종목(거래정지)은 그 달 대상에서 빠진다
- 연구용 일봉은 `store.load_daily_bars`(Decimal) 대신 SQL에서 `float8`로 바로 읽어 메모리를 줄인다
- 전체 수집 후에는 `daily_bars`에 전 종목이 있으므로 `swing_backtest.py`도 전 종목을 대상으로 돈다(README에 적는다)

---

### Task 1: 전체 종목 목록과 월별 대상 종목 `universe.py`

**Files:**
- Modify: `trader/toss.py` (`stocks_all` 추가)
- Create: `trader/universe.py`
- Test: `trader/tests/test_toss.py` (끝에 추가), `trader/tests/test_universe.py` (신규)

**Interfaces:**
- Consumes: `TossClient._get`, `toss._parsed`, `toss.STOCKS_PATH`, `store.save_daily_bars`, `bars.DailyBar`
- Produces:
  - `TossClient.stocks_all(market) -> [symbol]`
  - `universe.BENCHMARK = "069500"`
  - `universe.all_common_stocks(client) -> [symbol]` (KOSPI 다음 KOSDAQ)
  - `universe.month_starts(conn, date_from, date_to) -> [date]` 오름차순
  - `universe.monthly_universe(conn, dates, size=100, lookback=250) -> {date: [symbol 순위순]}`

- [ ] **Step 1: 실패하는 테스트 작성**

`trader/tests/test_toss.py` 끝에 추가:

```python
def test_stocks_all_requests_common_stocks_of_market(tmp_path):
    """마켓별 상장 보통주 목록을 요청해 종목코드만 순서대로 돌려준다."""
    fake = serve({"result": [{"symbol": "000020", "name": "동화약품", "securityType": "STOCK", "isCommonShare": True},
                             {"symbol": "0001A0", "name": "덕양에너젠", "securityType": "STOCK", "isCommonShare": True}]})
    assert make_client(tmp_path, fake).stocks_all("KOSPI") == ["000020", "0001A0"]
    _, url, kwargs = fake.gets()[0]
    assert url == "https://toss.test/api/v1/stocks/all"
    assert kwargs["params"] == {"market": "KOSPI", "securityType": "STOCK", "commonShare": "true"}


def test_stocks_all_rejects_bad_body(tmp_path):
    """result가 없으면 BAD_RESPONSE."""
    with pytest.raises(TossError) as e:
        make_client(tmp_path, serve({})).stocks_all("KOSDAQ")
    assert e.value.code == "BAD_RESPONSE"
```

`trader/tests/test_universe.py`:

```python
from datetime import date, timedelta
from decimal import Decimal

import store
import universe
from bars import DailyBar

START = date(2024, 1, 1)


def daily(days, close, volume):
    """주어진 날짜들에 시가=고가=저가=종가=close, 거래량 volume인 일봉."""
    p = Decimal(close)
    return [DailyBar(d, p, p, p, p, volume) for d in days]


def seed(conn):
    """기준 지수·A(거래대금 1,000)·B(2,000)는 2024-01-01부터 70일, C(100,000)는 1월 29~31일 3일만."""
    days = [START + timedelta(days=i) for i in range(70)]
    store.save_daily_bars(conn, universe.BENCHMARK, daily(days, "100", 1))
    store.save_daily_bars(conn, "A", daily(days, "10", 100))
    store.save_daily_bars(conn, "B", daily(days, "20", 100))
    store.save_daily_bars(conn, "C", daily([date(2024, 1, 29), date(2024, 1, 30), date(2024, 1, 31)], "1000", 100))


def test_month_starts_are_first_benchmark_days_of_each_month(conn):
    """기준 지수 거래일 중 매월 첫날만 오름차순으로."""
    seed(conn)
    assert universe.month_starts(conn, date(2024, 1, 1), date(2024, 3, 31)) == [
        date(2024, 1, 1), date(2024, 2, 1), date(2024, 3, 1)]
    assert universe.month_starts(conn, date(2024, 1, 15), date(2024, 2, 20)) == [date(2024, 2, 1)]


def test_monthly_universe_ranks_trading_value_before_each_date(conn):
    """전 거래일까지 lookback일 거래대금 합 순으로 고르고, 데이터가 lookback일 미만인 종목·기준 지수는 뺀다."""
    seed(conn)
    got = universe.monthly_universe(conn, [date(2024, 2, 1), date(2024, 3, 1)], size=5, lookback=5)
    assert got == {date(2024, 2, 1): ["B", "A"], date(2024, 3, 1): ["B", "A"]}
    assert universe.monthly_universe(conn, [date(2024, 2, 1)], size=1, lookback=5) == {date(2024, 2, 1): ["B"]}


def test_monthly_universe_without_prior_benchmark_day_is_empty(conn):
    """기준 지수 전 거래일이 없는 날짜는 빈 목록, 날짜가 없으면 빈 dict."""
    seed(conn)
    assert universe.monthly_universe(conn, [date(2023, 12, 1)], lookback=5) == {date(2023, 12, 1): []}
    assert universe.monthly_universe(conn, [], lookback=5) == {}


class FakeClient:
    """stocks_all만 흉내 낸다."""

    def stocks_all(self, market):
        """마켓별 고정 목록."""
        return {"KOSPI": ["005930"], "KOSDAQ": ["247540"]}[market]


def test_all_common_stocks_joins_kospi_and_kosdaq():
    """KOSPI 다음 KOSDAQ 순서로 합친다."""
    assert universe.all_common_stocks(FakeClient()) == ["005930", "247540"]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests/test_toss.py tests/test_universe.py -v`
Expected: toss 새 테스트 `AttributeError: ... 'stocks_all'`, `test_universe.py` 수집 ERROR `ModuleNotFoundError: No module named 'universe'`

- [ ] **Step 3: 구현**

`trader/toss.py`의 `TossClient` 끝(`fetch_daily_history` 아래)에 추가:

```python
    def stocks_all(self, market):
        """market(KOSPI·KOSDAQ 등)의 상장 보통주 종목코드 목록을 반환한다."""
        body = self._get(f"{STOCKS_PATH}/all", {"market": market, "securityType": "STOCK", "commonShare": "true"})
        return _parsed(lambda b: [s["symbol"] for s in b["result"]], body)
```

`trader/universe.py`:

```python
"""연구용 종목 유니버스: 전체 상장 보통주 목록과 매월 첫 거래일 기준 거래대금 상위 종목."""
from datetime import timedelta

BENCHMARK = "069500"  # KODEX 200


def all_common_stocks(client):
    """KOSPI·KOSDAQ 상장 보통주 종목코드를 KOSPI 먼저 반환한다."""
    return client.stocks_all("KOSPI") + client.stocks_all("KOSDAQ")


def month_starts(conn, date_from, date_to):
    """기준 지수의 매월 첫 거래일 중 date_from~date_to(양끝 포함)에 드는 날을 오름차순으로 반환한다.
    달 중간에서 시작하는 기간이어도 그 달 첫 거래일이 기간 밖이면 넣지 않는다."""
    rows = conn.execute(
        "SELECT d FROM (SELECT min(day) AS d FROM daily_bars WHERE symbol = %s "
        "GROUP BY date_trunc('month', day)) m WHERE d BETWEEN %s AND %s ORDER BY d",
        (BENCHMARK, date_from, date_to),
    ).fetchall()
    return [r[0] for r in rows]


def monthly_universe(conn, dates, size=100, lookback=250):
    """각 날짜 d의 기준 지수 전 거래일까지 lookback거래일 거래대금(종가×거래량) 합 상위 size 종목을
    {d: [종목 순위순]}으로 반환한다. lookback일 미만 종목과 기준 지수는 뺀다."""
    prev = {}
    for d in dates:
        day = conn.execute("SELECT max(day) FROM daily_bars WHERE symbol = %s AND day < %s",
                           (BENCHMARK, d)).fetchone()[0]
        if day is not None:
            prev[day] = d
    out = {d: [] for d in dates}
    if not prev:
        return out
    # 창 크기는 SQL 상수여야 해서 정수로 넣는다. 기간을 잘라 필요한 청크만 읽는다
    rows = conn.execute(
        f"""
        WITH tv AS (
            SELECT symbol, day,
                   sum(close * volume) OVER w AS value,
                   count(*) OVER w AS n
            FROM daily_bars
            WHERE symbol <> %(bench)s AND day >= %(start)s AND day <= %(end)s
            WINDOW w AS (PARTITION BY symbol ORDER BY day ROWS BETWEEN {int(lookback) - 1} PRECEDING AND CURRENT ROW)
        ), ranked AS (
            SELECT day, symbol, row_number() OVER (PARTITION BY day ORDER BY value DESC, symbol) AS rk
            FROM tv WHERE day = ANY(%(days)s) AND n = %(lookback)s
        )
        SELECT day, symbol FROM ranked WHERE rk <= %(size)s ORDER BY day, rk
        """,
        {"bench": BENCHMARK, "start": min(prev) - timedelta(days=int(lookback) * 2), "end": max(prev),
         "days": list(prev), "lookback": int(lookback), "size": int(size)},
    ).fetchall()
    for day, symbol in rows:
        out[prev[day]].append(symbol)
    return out
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests -v`
Expected: 245 passed (239 + 2 + 4)

- [ ] **Step 5: 커밋**

```powershell
git add toss.py universe.py tests/test_toss.py tests/test_universe.py
git commit -m @'
연구용 전체 종목 목록과 월별 거래대금 상위 종목 추가

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
'@
```

---

### Task 2: 지표와 통과 판정 `metrics.py`

**Files:**
- Create: `trader/metrics.py`
- Test: `trader/tests/test_metrics.py`

**Interfaces:**
- Consumes: 거래 객체는 `return_pct`(%), `entry_day`, `exit_day` 속성만 쓴다(Task 3의 `portfolio.PTrade`)
- Produces:
  - `metrics.summarize(equity, trades=()) -> dict` — 키 `cagr`, `vol`, `sharpe`, `mdd`, `yearly`(`{연도: 수익률}`), `trades`, `win_rate`(%), `hold_days`. 비율은 소수(0.1 = 10%)
  - `metrics.verdict(dev, val, hold, dca, full) -> (rows, passed)` — rows는 `[(기준 이름, 설명, 통과 bool)]`, 이름 순서 `검증 수익`, `시장 대비`, `최대 낙폭`, `연도별 안정성`, `거래 수`
  - `metrics.pct(x) -> str`(`+12.3%`/`-`), `metrics.num(x) -> str`(`0.85`/`-`)

- [ ] **Step 1: 실패하는 테스트 작성**

`trader/tests/test_metrics.py`:

```python
import math
import statistics
from dataclasses import dataclass
from datetime import date, timedelta

import pytest

import metrics


def eq(pairs):
    """(날짜 문자열, 평가액) 목록을 (date, 평가액)으로."""
    return [(date.fromisoformat(d), v) for d, v in pairs]


@dataclass
class T:
    """거래 대역."""
    entry_day: date
    exit_day: date
    return_pct: float


def test_summarize_cagr_vol_sharpe_mdd_yearly():
    """평가액 수열로 CAGR·연 변동성·샤프·MDD·완전한 연도 수익률을 계산한다."""
    s = metrics.summarize(eq([("2020-01-01", 100), ("2021-01-01", 110), ("2022-01-01", 99), ("2023-01-01", 121)]))
    years = 1096 / 365.25
    assert s["cagr"] == pytest.approx(1.21 ** (1 / years) - 1)
    vol = statistics.stdev([0.1, -0.1, 121 / 99 - 1]) * math.sqrt(252)
    assert s["vol"] == pytest.approx(vol)
    assert s["sharpe"] == pytest.approx(s["cagr"] / vol)
    assert s["mdd"] == pytest.approx(0.1)
    assert s["yearly"] == pytest.approx({2021: 0.1, 2022: -0.1})
    assert (s["trades"], s["win_rate"], s["hold_days"]) == (0, None, None)


def test_summarize_trades_and_single_point():
    """거래 수·승률·평균 보유일을 세고, 평가액이 한 점이면 CAGR·변동성·샤프는 None."""
    d = date(2024, 1, 1)
    s = metrics.summarize(eq([("2024-01-01", 100), ("2024-01-02", 101)]),
                          [T(d, d + timedelta(days=3), 2.0), T(d, d + timedelta(days=1), -1.0)])
    assert (s["trades"], s["win_rate"], s["hold_days"]) == (2, 50.0, 2.0)
    one = metrics.summarize(eq([("2024-01-01", 100)]))
    assert (one["cagr"], one["vol"], one["sharpe"], one["mdd"], one["yearly"]) == (None, None, None, 0.0, {})


def summary(cagr=0.10, sharpe=1.0, mdd=0.10, trades=150, yearly=None):
    """판정용 요약 dict."""
    return {"cagr": cagr, "vol": 0.1, "sharpe": sharpe, "mdd": mdd, "yearly": yearly or {},
            "trades": trades, "win_rate": 50.0, "hold_days": 10.0}


SEVEN_OF_TEN = {2016 + i: (0.1 if i < 7 else -0.1) for i in range(10)}


def boundary():
    """모든 기준을 경계에서 통과하는 입력."""
    return {"dev": summary(cagr=0.10, mdd=0.20), "val": summary(cagr=0.05, sharpe=1.2, mdd=0.20, trades=100),
            "hold": summary(sharpe=0.8), "dca": summary(sharpe=0.9), "full": summary(yearly=SEVEN_OF_TEN)}


def test_verdict_passes_at_boundaries():
    """검증 CAGR = 개발의 50%, MDD 20%, 거래 100건, 7/10년이면 모두 통과."""
    rows, passed = metrics.verdict(**boundary())
    assert [name for name, _, _ in rows] == ["검증 수익", "시장 대비", "최대 낙폭", "연도별 안정성", "거래 수"]
    assert passed and all(ok for _, _, ok in rows)


@pytest.mark.parametrize("key, changes, failed", [
    ("val", {"cagr": 0.049}, "검증 수익"),
    ("dev", {"cagr": -0.01}, "검증 수익"),
    ("val", {"sharpe": 0.85}, "시장 대비"),
    ("val", {"sharpe": None}, "시장 대비"),
    ("dev", {"mdd": 0.21}, "최대 낙폭"),
    ("full", {"yearly": {2016 + i: (0.1 if i < 6 else -0.1) for i in range(10)}}, "연도별 안정성"),
    ("val", {"trades": 99}, "거래 수"),
])
def test_verdict_fails_single_criterion(key, changes, failed):
    """기준 하나만 어기면 그 기준만 불합격이고 종합도 불합격."""
    args = boundary()
    args[key] = {**args[key], **changes}
    rows, passed = metrics.verdict(**args)
    assert [name for name, _, ok in rows if not ok] == [failed]
    assert not passed


def test_pct_and_num():
    """없는 값은 '-'."""
    assert (metrics.pct(0.1234), metrics.pct(-0.05), metrics.pct(None)) == ("+12.3%", "-5.0%", "-")
    assert (metrics.num(0.854), metrics.num(None)) == ("0.85", "-")
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests/test_metrics.py -v`
Expected: 수집 ERROR `ModuleNotFoundError: No module named 'metrics'`

- [ ] **Step 3: 구현**

`trader/metrics.py`:

```python
"""연구 지표와 통과 판정: CAGR·변동성·샤프·MDD·연도별 수익률, 미리 정한 기준 판정."""
import math
import statistics

MIN_RETAIN = 0.5          # 검증 CAGR ≥ 개발 CAGR × 0.5
MAX_DRAWDOWN = 0.20
MIN_POSITIVE_YEARS = 0.7
MIN_TRADES = 100


def summarize(equity, trades=()):
    """날짜별 평가액 [(date, 값)]과 청산 거래로 요약 지표 dict를 만든다. 비율은 소수, 승률만 %."""
    days = [d for d, _ in equity]
    values = [v for _, v in equity]
    years = (days[-1] - days[0]).days / 365.25
    cagr = (values[-1] / values[0]) ** (1 / years) - 1 if years > 0 and values[0] > 0 and values[-1] > 0 else None
    rets = [b / a - 1 for a, b in zip(values, values[1:]) if a > 0]
    vol = statistics.stdev(rets) * math.sqrt(252) if len(rets) > 1 else None
    sharpe = cagr / vol if cagr is not None and vol else None
    peak, mdd = values[0], 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            mdd = max(mdd, 1 - v / peak)
    year_end = {}
    for d, v in equity:
        year_end[d.year] = v
    # 첫해·마지막 해는 1년이 안 돼서 빼고, 전년 말 대비 그해 말 수익률
    yearly = {y: year_end[y] / year_end[y - 1] - 1 for y in range(days[0].year + 1, days[-1].year)
              if y in year_end and y - 1 in year_end}
    n = len(trades)
    return {
        "cagr": cagr, "vol": vol, "sharpe": sharpe, "mdd": mdd, "yearly": yearly, "trades": n,
        "win_rate": sum(t.return_pct > 0 for t in trades) / n * 100 if n else None,
        "hold_days": sum((t.exit_day - t.entry_day).days for t in trades) / n if n else None,
    }


def pct(x):
    """비율을 부호 있는 %로(소수 첫째 자리). 없으면 '-'."""
    return "-" if x is None else f"{x * 100:+.1f}%"


def num(x):
    """숫자를 소수 둘째 자리로. 없으면 '-'."""
    return "-" if x is None else f"{x:.2f}"


def _gt(a, b):
    """둘 다 있고 a > b면 True."""
    return a is not None and b is not None and a > b


def verdict(dev, val, hold, dca, full):
    """통과 기준별 (이름, 설명, 통과)와 종합 통과 여부를 반환한다. 값이 없으면 불합격."""
    years = full["yearly"]
    positive = sum(r > 0 for r in years.values())
    rows = [
        ("검증 수익", f"검증 CAGR {pct(val['cagr'])}, 개발 CAGR {pct(dev['cagr'])} (검증 > 0, 개발의 50% 이상)",
         _gt(val["cagr"], 0) and _gt(dev["cagr"], 0) and val["cagr"] >= MIN_RETAIN * dev["cagr"]),
        ("시장 대비", f"검증 샤프 {num(val['sharpe'])} / KODEX 200 보유 {num(hold['sharpe'])} / 정기 매수 {num(dca['sharpe'])}",
         _gt(val["sharpe"], hold["sharpe"]) and _gt(val["sharpe"], dca["sharpe"])),
        ("최대 낙폭", f"개발 {pct(dev['mdd'])}, 검증 {pct(val['mdd'])} (20% 이내)",
         dev["mdd"] <= MAX_DRAWDOWN and val["mdd"] <= MAX_DRAWDOWN),
        ("연도별 안정성", f"플러스 {positive}/{len(years)}년 (70% 이상)",
         bool(years) and positive / len(years) >= MIN_POSITIVE_YEARS),
        ("거래 수", f"검증 청산 {val['trades']}건 (100건 이상)", val["trades"] >= MIN_TRADES),
    ]
    return rows, all(ok for _, _, ok in rows)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests -v`
Expected: 256 passed (245 + 11)

- [ ] **Step 5: 커밋**

```powershell
git add metrics.py tests/test_metrics.py
git commit -m @'
연구 지표와 통과 판정 추가

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
'@
```

---

### Task 3: 포트폴리오 시뮬레이터와 전략 `portfolio.py`

**Files:**
- Create: `trader/portfolio.py`
- Test: `trader/tests/test_portfolio.py`

**Interfaces:**
- Consumes: 없음(순수 계산)
- Produces:
  - `portfolio.Costs(fee, tax, slippage)`, `portfolio.COSTS = Costs(0.00015, 0.002, 0.0005)`
  - `portfolio.Series(dates, opens, closes)` — 속성 `dates`, `opens`, `closes`, `index`(`{date: 위치}`)
  - `portfolio.PTrade(symbol, entry_day, exit_day, return_pct, pnl)`
  - `portfolio.Context(day, holdings, universe, series, month_start)`
  - `portfolio.Result(equity, trades)`
  - `portfolio.simulate(days, series, universe_at, strategy, capital, costs, bench, market_ma=200, month_starts=frozenset()) -> Result` — 전략은 `n` 속성과 `orders(ctx) -> (매도 목록, 순위순 매수 목록)`
  - `portfolio.TrendBreakout(entry, trend, exit, n)`, `portfolio.MonthlyMomentum(months, n)`
  - `portfolio.buy_and_hold(days, bench, capital, costs) -> [(date, 값)]`, `portfolio.monthly_dca(days, month_starts, bench, capital, costs) -> [(date, 값)]`

- [ ] **Step 1: 실패하는 테스트 작성**

`trader/tests/test_portfolio.py`:

```python
from datetime import date, timedelta

import pytest

import portfolio

D0 = date(2024, 1, 1)
NO = portfolio.Costs(0, 0, 0)


def ser(prices, start=D0, skip=()):
    """[(시가, 종가)]로 start부터 하루씩 Series를 만든다. skip의 순번 날짜는 뺀다."""
    rows = [(start + timedelta(days=i), o, c) for i, (o, c) in enumerate(prices) if i not in skip]
    return portfolio.Series([d for d, _, _ in rows], [float(o) for _, o, _ in rows], [float(c) for _, _, c in rows])


def flat(n, price=100):
    """시가=종가=price인 n일 Series."""
    return ser([(price, price)] * n)


class Script:
    """거래일 순번별로 정해진 (매도, 매수) 주문을 내는 테스트 전략."""

    def __init__(self, n, plan):
        """동시 보유 수와 순번 → (매도, 매수) 계획."""
        self.n, self.plan, self.i = n, plan, 0

    def orders(self, ctx):
        """이번 순번의 주문을 돌려준다."""
        got = self.plan.get(self.i, ([], []))
        self.i += 1
        return got


def run(series, strategy, days=3, capital=1000, costs=NO, bench=None, ma=1):
    """기준 지수(기본: 평탄) 거래일로 시뮬레이션한다."""
    bench = bench or flat(days)
    return portfolio.simulate(bench.dates, series, lambda d: list(series), strategy, capital, costs, bench, ma)


def values(result):
    """평가액만."""
    return [v for _, v in result.equity]


def test_buy_fills_next_open_with_integer_shares():
    """0일째 매수 주문은 1일째 시가에 min(평가액/N, 현금) 안의 정수 주로 체결된다."""
    r = run({"A": ser([(100, 100), (101, 101), (102, 102)])}, Script(2, {0: ([], ["A"])}))
    assert values(r) == [1000, 1000, 1004]  # 4주 × 101 = 404, 현금 596


def test_holdings_limit_and_rank_order():
    """매수 목록 순서대로 N개까지만 산다."""
    s = {k: flat(3) for k in "ABC"}
    r = run(s, Script(2, {0: ([], ["C", "A", "B"]), 1: (["C", "A", "B"], [])}))
    assert sorted(t.symbol for t in r.trades) == ["A", "C"]


def test_skips_zero_share_buy_and_continues():
    """종목당 금액으로 1주도 못 사면 건너뛰고 다음 순위를 산다."""
    r = run({"A": flat(3, 600), "B": flat(3, 100)}, Script(2, {0: ([], ["A", "B"]), 1: (["A", "B"], [])}))
    assert [t.symbol for t in r.trades] == ["B"]


def test_sells_before_buys_reuse_slot():
    """같은 날 매도가 먼저 체결돼 빈자리에 매수한다."""
    r = run({"A": flat(3), "B": flat(3)}, Script(1, {0: ([], ["A"]), 1: (["A"], ["B"])}))
    assert [(t.symbol, t.entry_day, t.exit_day) for t in r.trades] == [("A", D0 + timedelta(1), D0 + timedelta(2))]
    assert values(r) == [1000, 1000, 1000]


def test_order_cancelled_without_open():
    """체결일에 시가가 없는 종목 주문은 취소된다."""
    r = run({"B": ser([(100, 100)] * 3, skip={1})}, Script(1, {0: ([], ["B"])}))
    assert values(r) == [1000, 1000, 1000] and r.trades == []


def test_market_filter_blocks_buys_not_sells():
    """기준 지수가 평균 아래면 매수 주문만 비우고 매도는 체결한다."""
    bench = ser([(c, c) for c in (100, 110, 120, 50, 40)])
    r = run({"A": flat(5), "B": flat(5)}, Script(1, {1: ([], ["A"]), 3: (["A"], ["B"])}), days=5, bench=bench, ma=2)
    assert [(t.symbol, t.entry_day, t.exit_day) for t in r.trades] == [("A", D0 + timedelta(2), D0 + timedelta(4))]
    assert values(r)[-1] == 1000


def test_costs_in_quantity_and_return():
    """수량은 슬리피지·수수료 포함 단가로 내림하고, 수익률은 매도세·수수료를 뺀다."""
    costs = portfolio.Costs(0.001, 0.002, 0.001)
    s = {"A": ser([(10000, 10000), (10000, 10000), (10100, 10100)])}
    r = run(s, Script(1, {0: ([], ["A"]), 1: (["A"], [])}), capital=100000, costs=costs)
    [t] = r.trades
    cost = 9 * 10000 * 1.001 * 1.001
    proceeds = 9 * 10100 * 0.999 * 0.997
    assert t.pnl == pytest.approx(proceeds - cost)
    assert t.return_pct == pytest.approx((proceeds / cost - 1) * 100)


def ctx(day_index, holdings, universe, series, month_start=False):
    """전략 입력."""
    return portfolio.Context(D0 + timedelta(days=day_index), set(holdings), universe, series, month_start)


def test_trend_breakout_buys_breakout_above_trend_and_sells_below_low():
    """대상 종목 중 직전 최고 종가 돌파·추세 위면 매수, 보유 종목(대상 밖 포함)은 직전 최저 이탈 시 매도."""
    series = {"A": ser([(c, c) for c in (10, 10, 10, 11)]), "B": flat(4, 10),
              "H": ser([(c, c) for c in (10, 12, 13, 11)])}
    strategy = portfolio.TrendBreakout(2, 2, 2, 1)
    assert strategy.orders(ctx(3, ["H"], ["A", "B"], series)) == (["H"], ["A"])
    assert strategy.orders(ctx(1, [], ["A"], series)) == ([], [])  # 기간 부족


def test_monthly_momentum_rotates_on_month_start():
    """월초에 대상 종목의 최근 21×months거래일 수익률 상위 n개로 교체하고, 월초가 아니면 주문이 없다."""
    series = {"A": ser([(100, 100)] * 21 + [(120, 120)]), "B": ser([(100, 100)] * 21 + [(110, 110)]),
              "C": ser([(100, 100)] * 21 + [(130, 130)])}
    strategy = portfolio.MonthlyMomentum(1, 1)
    assert strategy.orders(ctx(21, ["B"], ["B", "A"], series, month_start=True)) == (["B"], ["A"])
    assert strategy.orders(ctx(21, ["B"], ["B", "A"], series)) == ([], [])


def test_benchmarks_hold_and_monthly_dca():
    """보유는 첫날 시가에 전액 정수 주, 정기 매수는 월초마다 같은 금액을 정수 주로 산다."""
    bench = ser([(100, 100), (100, 110), (120, 120)])
    days = bench.dates
    assert [v for _, v in portfolio.buy_and_hold(days, bench, 1000, NO)] == [1000, 1100, 1200]
    dca = portfolio.monthly_dca(days, {days[0], days[2]}, bench, 1000, NO)
    assert [v for _, v in dca] == [1000, 1050, 1100]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests/test_portfolio.py -v`
Expected: 수집 ERROR `ModuleNotFoundError: No module named 'portfolio'`

- [ ] **Step 3: 구현**

`trader/portfolio.py`:

```python
"""연구용 포트폴리오 시뮬레이터: 현금·정수 주·동시 보유 제한·다음 날 시가 체결, 전략 A·B, 비교 기준."""
import math
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Costs:
    """비용 비율: 수수료(양쪽), 매도세, 슬리피지(양쪽)."""
    fee: float
    tax: float
    slippage: float


COSTS = Costs(0.00015, 0.002, 0.0005)


class Series:
    """한 종목의 날짜순 시가·종가(float)와 날짜 → 위치 색인."""

    def __init__(self, dates, opens, closes):
        """날짜·시가·종가 목록을 받아 색인을 만든다."""
        self.dates, self.opens, self.closes = list(dates), list(opens), list(closes)
        self.index = {d: i for i, d in enumerate(self.dates)}


@dataclass(frozen=True)
class PTrade:
    """청산 완료 거래: 종목, 진입일, 청산일, 비용 반영 수익률(%), 원화 손익."""
    symbol: str
    entry_day: date
    exit_day: date
    return_pct: float
    pnl: float


@dataclass
class Context:
    """전략이 오늘 종가 기준으로 주문을 정할 때 보는 상태."""
    day: date
    holdings: set
    universe: list
    series: dict
    month_start: bool


@dataclass
class Result:
    """시뮬레이션 결과: 날짜별 평가액과 청산 거래."""
    equity: list
    trades: list


def market_ok(bench, day, window):
    """기준 지수 종가가 window일 평균 이상이면 True. 그날 데이터가 없거나 모자라면 False."""
    i = bench.index.get(day)
    if i is None or i + 1 < window:
        return False
    return bench.closes[i] >= sum(bench.closes[i - window + 1:i + 1]) / window


def _bar(series, symbol, day):
    """종목의 그날 위치. 없으면 None."""
    s = series.get(symbol)
    return (s, s.index.get(day)) if s else (None, None)


def simulate(days, series, universe_at, strategy, capital, costs, bench, market_ma=200, month_starts=frozenset()):
    """거래일마다 전날 주문을 시가에 체결(매도 먼저)하고, 종가로 평가한 뒤, 전략의 다음 주문을 받는다.
    시장 필터가 꺼진 날은 매수 주문을 비운다. 기간 끝 보유는 평가만 한다."""
    cash, pos, last = capital, {}, {}  # pos: 종목 → [주식 수, 매수 원가, 진입일], last: 종목 → 마지막 가격
    sells, buys = [], []
    equity, trades = [], []
    n = strategy.n
    for day in days:
        for sym in sells:
            s, i = _bar(series, sym, day)
            if sym not in pos or i is None:
                continue
            shares, cost, entry_day = pos.pop(sym)
            proceeds = shares * s.opens[i] * (1 - costs.slippage) * (1 - costs.fee - costs.tax)
            cash += proceeds
            trades.append(PTrade(sym, entry_day, day, (proceeds / cost - 1) * 100, proceeds - cost))
        value = cash + sum(p[0] * last[sym] for sym, p in pos.items())
        for sym in buys:
            if len(pos) >= n:
                break
            s, i = _bar(series, sym, day)
            if sym in pos or i is None:
                continue
            unit = s.opens[i] * (1 + costs.slippage) * (1 + costs.fee)
            shares = math.floor(min(value / n, cash) / unit)
            if shares <= 0:
                continue
            cash -= shares * unit
            pos[sym] = [shares, shares * unit, day]
            last[sym] = s.opens[i]
        for sym in pos:
            s, i = _bar(series, sym, day)
            if i is not None:
                last[sym] = s.closes[i]
        equity.append((day, cash + sum(p[0] * last[sym] for sym, p in pos.items())))
        sells, buys = strategy.orders(Context(day, set(pos), universe_at(day), series, day in month_starts))
        if not market_ok(bench, day, market_ma):
            buys = []
    return Result(equity, trades)


class TrendBreakout:
    """전략 A: 대상 종목 중 오늘 종가가 직전 entry일 최고 종가를 넘고 trend일 평균 위면 매수,
    보유 종목은 오늘 종가가 직전 exit일 최저 종가 아래면 매도."""

    def __init__(self, entry, trend, exit, n):
        """돌파·추세·청산 일수와 동시 보유 수."""
        self.entry, self.trend, self.exit, self.n = entry, trend, exit, n

    def orders(self, ctx):
        """(매도 목록, 대상 종목 순위순 매수 목록)을 반환한다."""
        sells = []
        for sym in sorted(ctx.holdings):
            s, i = _bar(ctx.series, sym, ctx.day)
            if i is not None and i >= self.exit and s.closes[i] < min(s.closes[i - self.exit:i]):
                sells.append(sym)
        buys = []
        for sym in ctx.universe:
            s, i = _bar(ctx.series, sym, ctx.day)
            if sym in ctx.holdings or i is None or i < self.entry or i + 1 < self.trend:
                continue
            c = s.closes
            if c[i] > max(c[i - self.entry:i]) and c[i] > sum(c[i - self.trend + 1:i + 1]) / self.trend:
                buys.append(sym)
        return sells, buys


class MonthlyMomentum:
    """전략 B: 매월 첫 거래일 종가 기준 대상 종목의 최근 21×months거래일 수익률 상위 n개로 교체한다."""

    def __init__(self, months, n):
        """모멘텀 개월 수와 보유 종목 수."""
        self.look, self.n = 21 * months, n

    def orders(self, ctx):
        """월초면 (목표 밖 보유 매도, 목표 중 미보유 매수)를, 아니면 빈 주문을 반환한다."""
        if not ctx.month_start:
            return [], []
        scored = []
        for rank, sym in enumerate(ctx.universe):
            s, i = _bar(ctx.series, sym, ctx.day)
            if i is None or i < self.look:
                continue
            scored.append((-(s.closes[i] / s.closes[i - self.look] - 1), rank, sym))
        target = [sym for _, _, sym in sorted(scored)[:self.n]]
        return sorted(h for h in ctx.holdings if h not in target), [t for t in target if t not in ctx.holdings]


def buy_and_hold(days, bench, capital, costs):
    """첫 거래일 시가에 기준 지수를 전액(정수 주) 사서 보유한 날짜별 평가액."""
    unit = bench.opens[bench.index[days[0]]] * (1 + costs.slippage) * (1 + costs.fee)
    shares = math.floor(capital / unit)
    cash = capital - shares * unit
    return [(d, cash + shares * bench.closes[bench.index[d]]) for d in days]


def monthly_dca(days, month_starts, bench, capital, costs):
    """자금을 구간 월초 수로 똑같이 나눠 매월 첫 거래일 시가에 정수 주로 사고, 남은 돈은 현금으로 둔 날짜별 평가액."""
    starts = [d for d in days if d in month_starts]
    per = capital / len(starts) if starts else 0
    cash, shares, equity = capital, 0, []
    for d in days:
        i = bench.index[d]
        if d in month_starts:
            unit = bench.opens[i] * (1 + costs.slippage) * (1 + costs.fee)
            k = math.floor(min(per, cash) / unit)
            cash -= k * unit
            shares += k
        equity.append((d, cash + shares * bench.closes[i]))
    return equity
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests -v`
Expected: 266 passed (256 + 10)

- [ ] **Step 5: 커밋**

```powershell
git add portfolio.py tests/test_portfolio.py
git commit -m @'
연구용 포트폴리오 시뮬레이터와 전략 A·B 추가

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
'@
```

---

### Task 4: 연구 실행기 `research.py`

**Files:**
- Create: `trader/research.py`
- Modify: `trader/.gitignore` (`reports/` 추가), `trader/README.md`
- Test: `trader/tests/test_research.py`

**Interfaces:**
- Consumes: Task 1 `universe.*`, Task 2 `metrics.summarize/verdict/pct/num`, Task 3 `portfolio.*`, 기존 `swing_backtest.collect(conn, client, symbols, since)`, `swing_backtest.years_ago(day, n)`, `TossClient`, `store.save_daily_bars`
- Produces:
  - `research.Lab(bench, series, starts, universe_by_start, market_ma=200)` — `universe_at(day)`, `days(start, end)`, `run(make, start, end, capital) -> 요약`, `raw(make, start, end, capital) -> Result`, `benchmarks(start, end, capital) -> (보유 요약, 정기 매수 요약)`
  - `research.load_series(conn, symbols, date_from, date_to) -> {symbol: portfolio.Series}`
  - `research.load_lab(conn, since, dev_start, end, lookback=250, market_ma=200) -> Lab` (기준 지수가 없으면 `LookupError("기준 지수 데이터 없음")`)
  - `research.strategies() -> [(전략, 조합 이름, 생성 함수)]`, `research.best(rows)`, `research.run_research(lab, dev_start, split, end, combos) -> 보고서 문자열`
  - `research.main(argv=None, today=None) -> int`

- [ ] **Step 1: 실패하는 테스트 작성**

`trader/tests/test_research.py`:

```python
import os
from datetime import date, timedelta
from decimal import Decimal

import pytest

import portfolio
import research
import store
import universe
from bars import DailyBar

D0 = date(2020, 1, 1)


def ser(closes):
    """시가=종가인 D0부터 하루씩 Series."""
    dates = [D0 + timedelta(days=i) for i in range(len(closes))]
    return portfolio.Series(dates, closes, closes)


def lab():
    """기준 지수·A는 우상향, B는 평탄한 120일 합성 데이터로 만든 Lab(대상 종목 A·B, 시장 필터 5일)."""
    bench = ser([100 + i for i in range(120)])
    series = {"A": ser([50 + i * 0.5 + (i % 7) for i in range(120)]), "B": ser([80.0] * 120)}
    starts = [d for d in bench.dates if d.day == 1]
    return research.Lab(bench, series, starts, {d: ["A", "B"] for d in starts}, market_ma=5)


COMBOS = [("A", "A 2/2/2 N1", lambda: portfolio.TrendBreakout(2, 2, 2, 1)),
          ("A", "A 3/3/2 N1", lambda: portfolio.TrendBreakout(3, 3, 2, 1)),
          ("B", "B 1개월 N1", lambda: portfolio.MonthlyMomentum(1, 1))]


def test_best_picks_highest_sharpe_treating_none_lowest():
    """샤프가 가장 높은 행을 고르고, 샤프가 없으면 가장 낮게 본다."""
    rows = [("A", "x", {"sharpe": None}), ("A", "y", {"sharpe": 0.3}), ("A", "z", {"sharpe": 0.5})]
    assert research.best(rows)[1] == "z"
    assert research.best([("A", "x", {"sharpe": None})])[1] == "x"


def test_lab_universe_at_uses_latest_month_start():
    """그날 이전(포함) 가장 최근 월초의 대상 종목, 첫 월초 전이면 빈 목록."""
    lb = research.Lab(ser([1.0] * 40), {}, [date(2020, 1, 10), date(2020, 2, 1)],
                      {date(2020, 1, 10): ["A"], date(2020, 2, 1): ["B"]})
    assert (lb.universe_at(date(2020, 1, 5)), lb.universe_at(date(2020, 1, 10)), lb.universe_at(date(2020, 2, 3))) == (
        [], ["A"], ["B"])


def test_run_research_report_sections():
    """개발 조합표 → 전략별 선택 조합 → 검증·연도별·판정 절이 보고서에 들어간다."""
    text = research.run_research(lab(), D0, date(2020, 3, 1), date(2020, 4, 29), COMBOS)
    assert text.startswith("# 포트폴리오 전략 연구")
    assert "## 개발 구간 2020-01-01 ~ 2020-02-29 (10만 원)" in text
    assert "| KODEX 200 보유 |" in text and "| KODEX 200 정기 매수 |" in text
    assert "## 전략 A: 선택 조합 A " in text and "## 전략 B: 선택 조합 B 1개월 N1" in text
    assert text.count("### 판정: ") == 2
    assert "| 전략 (1,000만 원 참고) |" in text


def daily(days, close, volume):
    """시가=고가=저가=종가=close 일봉."""
    p = Decimal(close)
    return [DailyBar(d, p, p, p, p, volume) for d in days]


def test_load_lab_reads_universe_and_series(conn):
    """DB에서 월초·월별 대상 종목을 계산하고, 대상이 된 종목과 기준 지수만 float Series로 읽는다."""
    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(70)]
    store.save_daily_bars(conn, universe.BENCHMARK, daily(days, "100", 1))
    store.save_daily_bars(conn, "A", daily(days, "10", 100))
    store.save_daily_bars(conn, "B", daily(days, "20", 100))
    lb = research.load_lab(conn, date(2024, 1, 1), date(2024, 2, 1), date(2024, 3, 10), lookback=5, market_ma=5)
    assert lb.starts[-2:] == [date(2024, 2, 1), date(2024, 3, 1)]
    assert lb.universe_at(date(2024, 2, 15)) == ["B", "A"]
    assert sorted(lb.series) == ["A", "B"] and lb.bench.closes[0] == 100.0
    with pytest.raises(LookupError):
        research.load_lab(conn, date(2025, 1, 1), date(2025, 2, 1), date(2025, 3, 10), lookback=5)


@pytest.fixture
def db_env(conn, monkeypatch):
    """main이 테스트 DB를 쓰고 실제 .env를 읽지 않게 한다."""
    monkeypatch.setattr(research, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    return conn


class FakeClient:
    """전체 종목 목록·일봉 이력만 흉내 낸다."""

    def stocks_all(self, market):
        """마켓별 한 종목."""
        return {"KOSPI": ["A"], "KOSDAQ": ["B"]}[market]

    def fetch_daily_history(self, symbol, since):
        """since부터 2일치."""
        return daily([since, since + timedelta(days=1)], "100", 10)


def test_main_collect_saves_all_common_stocks_and_benchmark(db_env, monkeypatch, capsys):
    """--collect는 전체 보통주와 기준 지수의 11년치 일봉을 받아 저장한다."""
    monkeypatch.setenv("TOSS_CLIENT_ID", "CID-X")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "SECRET-X")
    monkeypatch.setattr(research, "TossClient", lambda *args: FakeClient())
    assert research.main(["--collect"], today=date(2026, 9, 18)) == 0
    assert capsys.readouterr().out.strip().endswith("일봉 수집 3종목, 실패 0종목")
    got = store.load_daily_bars(db_env, None, date(2015, 1, 1), date(2026, 9, 18))
    assert sorted(got) == ["069500", "A", "B"] and got["A"][0].date == date(2015, 9, 18)


def test_main_without_benchmark_data_fails(db_env, capsys):
    """기준 지수 일봉이 없으면 1과 안내를 낸다."""
    assert research.main([], today=date(2026, 9, 18)) == 1
    assert capsys.readouterr().out.strip() == "실행 실패: 기준 지수 데이터 없음"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.\.venv\Scripts\python -m pytest tests/test_research.py -v`
Expected: 수집 ERROR `ModuleNotFoundError: No module named 'research'`

- [ ] **Step 3: 구현**

`trader/research.py`:

```python
"""포트폴리오 전략 연구 실행기: 전체 종목 일봉 수집(--collect)과 미리 정한 실험·통과 판정 보고서."""
import argparse
import logging
import os
import sys
from bisect import bisect_right
from datetime import datetime, timedelta
from pathlib import Path

import psycopg
from dotenv import load_dotenv

import metrics
import portfolio
import universe
from bars import KST
from swing_backtest import collect, years_ago
from toss import TossClient, TossError

ROOT = Path(__file__).resolve().parent
REPORT_DIR = ROOT / "reports"
HISTORY_YEARS = 11  # 개발 10년 + 지표 준비 1년
DEV_YEARS = 10
VALIDATION_YEARS = 3
CAPITAL = 100_000
REFERENCE_CAPITAL = 10_000_000
GRID_A = [(20, 50, 20), (20, 100, 20), (55, 50, 20), (55, 100, 20)]
HOLDINGS_A = (3, 5)
GRID_B = [(6, 3), (6, 5), (12, 3), (12, 5)]
LIMITS = ("한계: 상장폐지 종목 없음(생존 편향), 수정주가 일봉, 시가 체결 가정(호가·체결량 제약 무시), "
          "대상 종목은 현재 상장 보통주 중 그 시점 거래대금 상위")
HEADER = "| 조합 | CAGR | 변동성 | 샤프 | MDD | 청산 거래 | 승률 | 평균 보유일 |\n|---|---|---|---|---|---|---|---|"


def strategies():
    """미리 정한 조합 목록 [(전략, 조합 이름, 전략 생성 함수)]."""
    out = []
    for e, t, x in GRID_A:
        for n in HOLDINGS_A:
            out.append(("A", f"A {e}/{t}/{x} N{n}", lambda e=e, t=t, x=x, n=n: portfolio.TrendBreakout(e, t, x, n)))
    for m, n in GRID_B:
        out.append(("B", f"B {m}개월 N{n}", lambda m=m, n=n: portfolio.MonthlyMomentum(m, n)))
    return out


class Lab:
    """연구 데이터(기준 지수, 종목 Series, 월초, 월별 대상 종목)를 들고 구간별 시뮬레이션을 돌린다."""

    def __init__(self, bench, series, starts, universe_by_start, market_ma=200):
        """데이터를 받아 월초 집합을 만든다."""
        self.bench, self.series, self.starts = bench, series, list(starts)
        self.uni, self.market_ma, self.start_set = universe_by_start, market_ma, set(starts)

    def universe_at(self, day):
        """day 이전(포함) 가장 최근 월초의 대상 종목. 없으면 빈 목록."""
        k = bisect_right(self.starts, day)
        return self.uni.get(self.starts[k - 1], []) if k else []

    def days(self, start, end):
        """기준 지수 거래일 중 start~end(양끝 포함)."""
        return [d for d in self.bench.dates if start <= d <= end]

    def raw(self, make, start, end, capital):
        """전략 하나를 구간에서 돌린 시뮬레이션 결과."""
        return portfolio.simulate(self.days(start, end), self.series, self.universe_at, make(), capital,
                                  portfolio.COSTS, self.bench, self.market_ma, self.start_set)

    def run(self, make, start, end, capital):
        """전략 하나를 구간에서 돌린 요약 지표."""
        r = self.raw(make, start, end, capital)
        return metrics.summarize(r.equity, r.trades)

    def benchmarks(self, start, end, capital):
        """같은 구간 KODEX 200 보유·정기 매수 요약 지표."""
        days = self.days(start, end)
        hold = portfolio.buy_and_hold(days, self.bench, capital, portfolio.COSTS)
        dca = portfolio.monthly_dca(days, self.start_set, self.bench, capital, portfolio.COSTS)
        return metrics.summarize(hold), metrics.summarize(dca)


def load_series(conn, symbols, date_from, date_to):
    """종목·기간 일봉 시가·종가를 float로 바로 읽어 {symbol: Series}로 반환한다(기본키 (symbol, day) 사용)."""
    out, rows = {}, {}
    for symbol, day, o, c in conn.execute(
            "SELECT symbol, day, open::float8, close::float8 FROM daily_bars "
            "WHERE symbol = ANY(%s) AND day >= %s AND day <= %s ORDER BY symbol, day",
            (list(symbols), date_from, date_to)):
        rows.setdefault(symbol, ([], [], []))
        rows[symbol][0].append(day)
        rows[symbol][1].append(o)
        rows[symbol][2].append(c)
    for symbol, (days, opens, closes) in rows.items():
        out[symbol] = portfolio.Series(days, opens, closes)
    return out


def load_lab(conn, since, dev_start, end, lookback=250, market_ma=200):
    """월초·월별 대상 종목을 DB에서 계산하고, 대상이 된 적 있는 종목과 기준 지수를 읽어 Lab을 만든다."""
    starts = universe.month_starts(conn, dev_start - timedelta(days=40), end)
    uni = universe.monthly_universe(conn, starts, lookback=lookback)
    symbols = sorted({s for syms in uni.values() for s in syms} | {universe.BENCHMARK})
    series = load_series(conn, symbols, since, end)
    if universe.BENCHMARK not in series:
        raise LookupError("기준 지수 데이터 없음")
    bench = series.pop(universe.BENCHMARK)
    return Lab(bench, series, starts, uni, market_ma)


def best(rows):
    """(전략, 조합 이름, 요약) 중 샤프가 가장 높은 행. 샤프가 없으면 가장 낮게 본다."""
    return max(rows, key=lambda r: r[2]["sharpe"] if r[2]["sharpe"] is not None else float("-inf"))


def _row(name, s):
    """표 한 줄."""
    win = "-" if s["win_rate"] is None else f"{s['win_rate']:.1f}%"
    hold = "-" if s["hold_days"] is None else f"{s['hold_days']:.1f}"
    return (f"| {name} | {metrics.pct(s['cagr'])} | {metrics.pct(s['vol'])} | {metrics.num(s['sharpe'])} | "
            f"{metrics.pct(s['mdd'])} | {s['trades']} | {win} | {hold} |")


def run_research(lab, dev_start, split, end, combos):
    """개발 조합표 → 전략별 샤프 최고 조합 → 검증(10만 원 판정, 1,000만 원 참고)·연도별 → 판정표 보고서를 만든다."""
    dev_end = split - timedelta(days=1)
    lines = [f"# 포트폴리오 전략 연구 (개발 {dev_start} ~ {dev_end}, 검증 {split} ~ {end})", "", LIMITS, "",
             f"## 개발 구간 {dev_start} ~ {dev_end} (10만 원)", "", HEADER]
    dev_rows = [(kind, name, lab.run(make, dev_start, dev_end, CAPITAL)) for kind, name, make in combos]
    lines += [_row(name, s) for _, name, s in dev_rows]
    dev_hold, dev_dca = lab.benchmarks(dev_start, dev_end, CAPITAL)
    lines += [_row("KODEX 200 보유", dev_hold), _row("KODEX 200 정기 매수", dev_dca)]
    val_hold, val_dca = lab.benchmarks(split, end, CAPITAL)
    makes = {name: make for _, name, make in combos}
    for kind in sorted({k for k, _, _ in combos}):
        _, name, dev = best([r for r in dev_rows if r[0] == kind])
        val = lab.run(makes[name], split, end, CAPITAL)
        ref = lab.run(makes[name], split, end, REFERENCE_CAPITAL)
        full = lab.run(makes[name], dev_start, end, CAPITAL)
        rows, passed = metrics.verdict(dev, val, val_hold, val_dca, full)
        failed = [n for n, _, ok in rows if not ok]
        lines += ["", f"## 전략 {kind}: 선택 조합 {name}", "", f"### 검증 구간 {split} ~ {end}", "", HEADER,
                  _row("전략 (10만 원)", val), _row("전략 (1,000만 원 참고)", ref),
                  _row("KODEX 200 보유", val_hold), _row("KODEX 200 정기 매수", val_dca),
                  "", "### 연도별 수익률 (10년 연속, 10만 원)", "", "| 연도 | 수익률 |", "|---|---|"]
        lines += [f"| {y} | {metrics.pct(r)} |" for y, r in sorted(full["yearly"].items())]
        lines += ["", f"### 판정: {'모의투자 진행 가능' if passed else '불합격 (' + ', '.join(failed) + ')'}", "",
                  "| 기준 | 값 | 통과 |", "|---|---|---|"]
        lines += [f"| {n} | {detail} | {'O' if ok else 'X'} |" for n, detail, ok in rows]
    return "\n".join(lines) + "\n"


def main(argv=None, today=None):
    """전체 종목 일봉 수집(--collect) 또는 연구 실행 1회. 성공 0, 실행 실패 1."""
    parser = argparse.ArgumentParser(description="포트폴리오 전략 연구")
    parser.add_argument("--collect", action="store_true", help="현재 상장 보통주 전체와 KODEX 200의 11년치 일봉 수집")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    today = today or datetime.now(KST).date()
    end, split = today - timedelta(days=1), years_ago(today, VALIDATION_YEARS)
    dev_start, since = years_ago(today, DEV_YEARS), years_ago(today, HISTORY_YEARS)
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
                symbols = universe.all_common_stocks(client) + [universe.BENCHMARK]
                ok, failed = collect(conn, client, symbols, since)
                print(f"일봉 수집 {ok}종목, 실패 {len(failed)}종목")
                return 0
            lab = load_lab(conn, since, dev_start, end)
    except LookupError as e:
        print(f"실행 실패: {e}")
        return 1
    except Exception as e:
        # 예외 문자열에 접속 문자열이 섞일 수 있어 종류와 토스 오류 코드만 남긴다
        print(f"실행 실패: {type(e).__name__} {e.code if isinstance(e, TossError) else ''}".rstrip())
        return 1
    text = run_research(lab, dev_start, split, end, strategies())
    print(text)
    REPORT_DIR.mkdir(exist_ok=True)
    path = REPORT_DIR / f"research-{today}.md"
    path.write_text(text, encoding="utf-8")
    print(f"보고서: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

`trader/.gitignore` 끝에 `reports/` 한 줄을 추가한다.

`trader/README.md`: 1행 제목 끝에 `, 포트폴리오 전략 연구`를 더하고, 4행 설계 목록 끝에 `, \`docs/superpowers/specs/2026-09-18-portfolio-research-design.md\``를 더한 뒤 "첫 실행 수동 검증 (1회)" 절 바로 앞에 넣는다:

````markdown
## 포트폴리오 전략 연구

실제 투자로 가기 전에 미리 정한 통과 기준을 모두 넘는 전략이 있는지 판정한다(설계 문서 2절 기준).

- 대상 종목: 매월 첫 거래일, 전날까지 250거래일 거래대금 상위 100(현재 상장 보통주 전체에서)
- 포트폴리오: 10만 원(판정), 1,000만 원(참고). 정수 주, 동시 보유 N, 신호 다음 날 시가 체결, 비용 반영, KODEX 200이 200일 평균 아래면 신규 매수 안 함
- 전략 A 추세 돌파(4 조합 × N 3·5), 전략 B 월간 모멘텀(6·12개월 × 3·5종목). 개발 구간(10년 전 ~ 3년 전) 샤프 최고 조합을 전략마다 고르고 검증 구간(최근 3년)에서 한 번만 판정
- 비교 기준: KODEX 200 보유, KODEX 200 매월 정기 매수

```powershell
.\.venv\Scripts\python research.py --collect   # 현재 상장 보통주 전체 + KODEX 200, 11년치 일봉(약 1시간, 장중·20:30 피해서)
.\.venv\Scripts\python research.py             # 실험 실행, 보고서 출력 + reports/research-날짜.md 저장
```

전체 수집 후에는 `daily_bars`에 전 종목이 있어 `swing_backtest.py`도 전 종목을 대상으로 돈다.
````

- [ ] **Step 4: 테스트 통과 확인**

Run: `.\.venv\Scripts\python -m pytest tests -v`
Expected: 272 passed (266 + 6)

- [ ] **Step 5: 커밋**

```powershell
git add research.py .gitignore README.md tests/test_research.py
git commit -m @'
포트폴리오 전략 연구 실행기와 사용법 추가

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
'@
```

---

### Task 5: 운영 전환·수집·연구 실행 (코드 변경 없음)

사용자 PC에서 운영 DB로 실행한다. `.env` 값은 출력하지 않는다. 모의투자(08:55~15:31)·수집기(20:30)와 겹치지 않는 시간에 한다.

- [ ] **Step 1: 하이퍼테이블 운영 전환**

```powershell
$env:PGPASSWORD = $null
$url = (Select-String -Path .env -Pattern '^DATABASE_URL=(.*)$').Matches[0].Groups[1].Value
D:\PIE\PostgreSQL_15\bin\pg_dump.exe $url -t minute_bars -t daily_bars -Fc -f "$env:TEMP\trader-bars-backup.dump"
.\.venv\Scripts\python -c "import os, psycopg; from dotenv import load_dotenv; load_dotenv('.env'); c = psycopg.connect(os.environ['DATABASE_URL'], autocommit=True); b = {t: c.execute(f'select count(*) from {t}').fetchone()[0] for t in ('minute_bars', 'daily_bars')}; c.execute(open('schema.sql', encoding='utf-8').read()); a = {t: c.execute(f'select count(*) from {t}').fetchone()[0] for t in ('minute_bars', 'daily_bars')}; print(b, a, c.execute('select hypertable_name, compression_enabled from timescaledb_information.hypertables').fetchall())"
```

Expected: 전환 전후 행 수 같음, 두 테이블 하이퍼테이블, `minute_bars` 압축 켜짐. 이어서 30일 지난 청크 압축과 용량 확인:

```powershell
.\.venv\Scripts\python -c "import os, psycopg; from dotenv import load_dotenv; load_dotenv('.env'); c = psycopg.connect(os.environ['DATABASE_URL'], autocommit=True); print(c.execute('select count(compress_chunk(ch, if_not_compressed => true)) from show_chunks(''minute_bars'', older_than => interval ''30 days'') ch').fetchone()); print(c.execute('select * from hypertable_compression_stats(''minute_bars'')').fetchall())"
```

- [ ] **Step 2: 전체 종목 일봉 수집**

```powershell
.\.venv\Scripts\python research.py --collect
```

Expected: `일봉 수집 약 2600종목, 실패 N종목`(N은 신규 상장 등 소수), 1시간 안팎

- [ ] **Step 3: 월별 대상 종목 쿼리 실행 계획 확인 (사용자 규칙)**

```powershell
.\.venv\Scripts\python -c "import os, psycopg; from dotenv import load_dotenv; load_dotenv('.env'); c = psycopg.connect(os.environ['DATABASE_URL']); [print(r[0]) for r in c.execute(\"EXPLAIN ANALYZE SELECT symbol, day, sum(close*volume) OVER (PARTITION BY symbol ORDER BY day ROWS BETWEEN 249 PRECEDING AND CURRENT ROW) FROM daily_bars WHERE symbol <> '069500' AND day >= '2014-01-01' AND day <= '2026-09-17'\")]"
```

Expected: 청크 단위 스캔과 윈도 정렬. 이 쿼리는 기간 전체 종목의 창 합계를 계산해야 해서 구간 내 전체 읽기가 필요하다(날짜 조건으로 청크 제외만 가능). 실행 시간을 보고하고, 수 분 이상이면 사용자와 상의한다. `month_starts`·전 거래일 조회는 `(symbol, day)` 기본키로 인덱스 스캔이어야 한다(`EXPLAIN ANALYZE SELECT max(day) FROM daily_bars WHERE symbol = '069500' AND day < '2026-09-01'`로 확인)

- [ ] **Step 4: 연구 실행**

```powershell
.\.venv\Scripts\python research.py
```

Expected: 보고서 출력과 `reports/research-YYYY-MM-DD.md` 저장

- [ ] **Step 5: 사용자 보고**

전략별 선택 조합, 검증 결과, 판정표, 연도별 수익률, 한계를 보고한다. 통과 전략이 있으면 모의투자 연결 설계를, 없으면 대안(KODEX 200 정기 매수 등)을 제안한다.
