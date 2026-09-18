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
