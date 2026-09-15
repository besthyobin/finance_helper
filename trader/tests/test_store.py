from datetime import date, datetime
from decimal import Decimal

import store
from kis import KST, Bar

TODAY = date(2026, 9, 14)


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
