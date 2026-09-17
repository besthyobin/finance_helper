from datetime import datetime, time, timedelta
from decimal import Decimal

import pytest

import engine
from bars import KST, Bar

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
        """봉 순번(int) → 신호("buy"/"sell") 매핑을 받아 초기 상태를 둔다."""
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
            """params는 무시하고 항상 첫 봉에서 매수하는 전략을 만든다."""
            super().__init__({0: "buy"})

    day1 = bars_from("14:58", [("100", "100"), ("101", "101")], day=10)
    day2 = bars_from("09:00", [("200", "200"), ("201", "202")], day=11)
    trades = engine.run(BuyFirst, {}, {"A": day1 + day2}, NO_COST)
    assert [(t.entry_ts, t.exit_ts, t.exit_reason) for t in trades] == [
        (day1[1].ts, day1[1].ts, "day_end"), (day2[1].ts, day2[1].ts, "day_end")]


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
