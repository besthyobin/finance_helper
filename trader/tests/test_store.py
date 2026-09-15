from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import store
from kis import KST, Bar

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


def test_done_symbols_includes_ok_and_empty_only(conn):
    """완료 종목은 ok·empty만이고 error와 다른 날짜는 제외한다."""
    store.record_run(conn, "A", TODAY, 382, "ok")
    store.record_run(conn, "B", TODAY, 0, "empty")
    store.record_run(conn, "C", TODAY, 0, "error", "x")
    store.record_run(conn, "D", date(2026, 9, 11), 382, "ok")
    assert store.done_symbols(conn, TODAY) == {"A", "B"}


def test_failed_runs_lists_errors_sorted(conn):
    """오늘 error 종목을 종목코드 순으로 오류 메시지와 함께 반환한다."""
    store.record_run(conn, "B", TODAY, 0, "error", "b오류")
    store.record_run(conn, "A", TODAY, 0, "error", "a오류")
    store.record_run(conn, "C", TODAY, 382, "ok")
    assert store.failed_runs(conn, TODAY) == [("A", "a오류"), ("B", "b오류")]


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
