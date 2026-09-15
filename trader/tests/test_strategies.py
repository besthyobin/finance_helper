from datetime import datetime, timedelta
from decimal import Decimal

import pytest

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


def test_ma_cross_rejects_short_not_less_than_long():
    """short가 0 이하이거나 long 이상이면 ValueError를 낸다."""
    for params in ({"short": 20, "long": 20}, {"short": 0}):
        with pytest.raises(ValueError):
            MaCross(params)
