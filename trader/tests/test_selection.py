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
