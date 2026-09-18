from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest

import engine
import store
from bars import KST, Bar, DailyBar

TODAY = date(2026, 9, 14)
SCHEMA = (Path(__file__).resolve().parents[1] / "schema.sql").read_text(encoding="utf-8")


def bar(hour, minute, close="70000"):
    """테스트용 1분봉을 만든다."""
    p = Decimal(close)
    return Bar(datetime(2026, 9, 14, hour, minute, tzinfo=KST), p, p, p, p, 100)


def test_schema_creates_tables(conn):
    """스키마 적용 후 두 테이블이 존재하는지 확인한다."""
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    ).fetchall()
    assert {"minute_bars", "collect_runs"} <= {r[0] for r in rows}


def test_save_bars_round_trip(conn):
    """저장한 봉을 같은 값으로 읽을 수 있다."""
    store.save_bars(conn, "005930", [bar(9, 0, "70100")])
    row = conn.execute("SELECT symbol, ts, open, close, volume FROM minute_bars").fetchone()
    assert row == ("005930", datetime(2026, 9, 14, 9, 0, tzinfo=KST),
                   Decimal("70100"), Decimal("70100"), 100)


def test_save_bars_is_idempotent(conn):
    """같은 봉을 두 번 저장해도 행 수가 늘지 않는다."""
    bars = [bar(9, 0), bar(9, 1)]
    store.save_bars(conn, "005930", bars)
    store.save_bars(conn, "005930", bars)
    assert conn.execute("SELECT count(*) FROM minute_bars").fetchone()[0] == 2


def test_record_run_overwrites_same_day(conn):
    """같은 종목·날짜 결과는 최신 기록으로 덮어쓴다."""
    store.record_run(conn, "005930", TODAY, 0, "error", "EGW00201 오류")
    store.record_run(conn, "005930", TODAY, 382, "ok")
    rows = conn.execute("SELECT bar_count, status, error FROM collect_runs").fetchall()
    assert rows == [(382, "ok", None)]


def test_done_days_includes_ok_and_empty_only(conn):
    """완료 날짜는 해당 종목의 ok·empty만이고 error와 다른 종목은 제외한다."""
    store.record_run(conn, "A", TODAY, 720, "ok")
    store.record_run(conn, "A", date(2026, 9, 11), 0, "empty")
    store.record_run(conn, "A", date(2026, 9, 10), 0, "error", "x")
    store.record_run(conn, "B", date(2026, 9, 9), 720, "ok")
    assert store.done_days(conn, "A") == {TODAY, date(2026, 9, 11)}


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
    store.save_bars(conn, "005930", [b], source="toss")
    rows = conn.execute("SELECT source FROM minute_bars ORDER BY source").fetchall()
    assert rows == [("kis",), ("toss",)]


def test_load_bars_filters_source_dates_symbols(conn):
    """source·KST 날짜 경계·종목으로 걸러 종목별 시각 오름차순으로 돌려준다."""
    inside = [datetime(2026, 9, 11, 0, 30, tzinfo=KST), datetime(2026, 9, 11, 9, 0, tzinfo=KST)]
    store.save_bars(conn, "A", [bar_ts(t) for t in reversed(inside)], source="toss")
    store.save_bars(conn, "A", [bar_ts(datetime(2026, 9, 12, 0, 0, tzinfo=KST))], source="toss")
    store.save_bars(conn, "B", [bar_ts(inside[1])], source="toss")
    store.save_bars(conn, "C", [bar_ts(inside[1])], source="kis")

    got = store.load_bars(conn, "toss", date(2026, 9, 11), date(2026, 9, 11))
    assert {s: [b.ts for b in bars] for s, bars in got.items()} == {"A": inside, "B": [inside[1]]}
    assert got["A"][0].ts.utcoffset() == KST.utcoffset(None)
    assert list(store.load_bars(conn, "toss", date(2026, 9, 11), date(2026, 9, 11), ["B"])) == ["B"]


def run_info():
    """save_run에 넘길 실행 정보 예시."""
    return {"strategy": "orb", "params": {"range_end": "09:30"}, "source": "toss",
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


def candidate(symbol, status, reason=None, **metrics):
    """선정 결과 행 dict를 만든다."""
    return {"symbol": symbol, "name": f"종목{symbol}", "rank": 3, "status": status, "reason": reason,
            "metrics": metrics}


def test_save_candidates_replaces_same_day_and_keeps_other_days(conn):
    """같은 날 결과는 지우고 다시 쓰고, 다른 날 결과는 그대로 두며 Decimal·date는 문자열로 저장한다."""
    day, prev = date(2026, 9, 18), date(2026, 9, 17)
    store.save_candidates(conn, prev, [candidate("X", "selected")])
    store.save_candidates(conn, day, [candidate("A", "rejected", "공매도"), candidate("B", "passed")])
    store.save_candidates(conn, day, [candidate("A", "selected", close=Decimal("50000"), day=date(2026, 9, 17))])
    [row] = store.load_candidates(conn, day)
    assert (row["symbol"], row["name"], row["rank"], row["status"], row["reason"]) == ("A", "종목A", 3, "selected", None)
    assert row["metrics"] == {"close": "50000", "day": "2026-09-17"}
    assert [r["symbol"] for r in store.load_candidates(conn, prev)] == ["X"]


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


def test_bar_tables_are_hypertables_with_minute_compression(conn):
    """minute_bars·daily_bars는 하이퍼테이블이고, 30일 압축 정책은 minute_bars에만 있다."""
    tables = conn.execute(
        "SELECT hypertable_name, compression_enabled FROM timescaledb_information.hypertables "
        "WHERE hypertable_name IN ('minute_bars', 'daily_bars') ORDER BY 1"
    ).fetchall()
    assert tables == [("daily_bars", False), ("minute_bars", True)]
    policies = conn.execute(
        "SELECT hypertable_name, config->>'compress_after' FROM timescaledb_information.jobs "
        "WHERE proc_name = 'policy_compression'"
    ).fetchall()
    assert policies == [("minute_bars", "30 days")]
