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
        """entry_ts가 minute분 뒤, 2분 보유, return_pct가 ret인 거래를 만든다."""
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
    ["--strategy", "ma_cross", "--param", "short=30"],
    ["--strategy", "ma_cross", "--param", "short=0"],
    ["--strategy", "orb", "--param", "range_end=25:99"],
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


def test_main_hides_connection_string_on_db_failure(monkeypatch, capsys):
    """DB 접속 실패 시 예외 종류만 출력하고 접속 문자열(비밀번호)은 출력하지 않는다."""
    monkeypatch.setenv("DATABASE_URL", "SECRETPW")
    assert backtest.main(["--strategy", "orb", *ARGS]) == 1
    out = capsys.readouterr().out
    assert "실행 실패: ProgrammingError" in out
    assert "SECRETPW" not in out


def test_parse_args_rejects_non_numeric_cost():
    """숫자가 아닌 비용 값은 argparse 사용법 오류(종료 코드 2)가 된다."""
    with pytest.raises(SystemExit) as e:
        backtest.parse_args(["--strategy", "orb", "--fee", "abc", *ARGS])
    assert e.value.code == 2
