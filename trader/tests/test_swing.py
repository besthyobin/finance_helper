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
