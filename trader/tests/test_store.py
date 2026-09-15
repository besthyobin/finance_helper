def test_schema_creates_tables(conn):
    """스키마 적용 후 두 테이블이 존재하는지 확인한다."""
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    ).fetchall()
    assert {"minute_bars", "collect_runs"} <= {r[0] for r in rows}
