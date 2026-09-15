"""minute_bars·collect_runs·backtest 테이블 저장과 조회."""
from datetime import datetime, time, timedelta

from psycopg.types.json import Jsonb

from kis import KST, Bar


def save_bars(conn, symbol, bars, source="kis"):
    """봉 목록을 한 트랜잭션으로 저장한다. 이미 있는 (source, symbol, ts)는 건너뛴다."""
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO minute_bars (source, symbol, ts, open, high, low, close, volume) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (source, symbol, ts) DO NOTHING",
            [(source, symbol, b.ts, b.open, b.high, b.low, b.close, b.volume) for b in bars],
        )


def load_bars(conn, source, date_from, date_to, symbols=None):
    """source·KST 기간(양끝 포함)·종목으로 봉을 조회해 {종목: 시각 오름차순 Bar 목록}으로 반환한다."""
    sql = ("SELECT symbol, ts, open, high, low, close, volume FROM minute_bars "
           "WHERE source = %s AND ts >= %s AND ts < %s")
    args = [source, datetime.combine(date_from, time(0), KST),
            datetime.combine(date_to + timedelta(days=1), time(0), KST)]
    if symbols:
        sql += " AND symbol = ANY(%s)"
        args.append(list(symbols))
    bars = {}
    for symbol, ts, o, h, l, c, v in conn.execute(sql + " ORDER BY symbol, ts", args):
        bars.setdefault(symbol, []).append(Bar(ts.astimezone(KST), o, h, l, c, v))
    return bars


def save_run(conn, run, trades):
    """백테스트 실행 정보와 거래 내역을 한 트랜잭션으로 저장하고 run id를 반환한다."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "INSERT INTO backtest_runs (strategy, params, source, symbols, date_from, date_to, costs) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (run["strategy"], Jsonb(run["params"]), run["source"], run["symbols"],
             run["date_from"], run["date_to"], Jsonb(run["costs"])),
        )
        run_id = cur.fetchone()[0]
        cur.executemany(
            "INSERT INTO backtest_trades (run_id, symbol, entry_ts, entry_price, exit_ts, "
            "exit_price, return_pct, exit_reason) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            [(run_id, t.symbol, t.entry_ts, t.entry_price, t.exit_ts, t.exit_price,
              t.return_pct, t.exit_reason) for t in trades],
        )
    return run_id


def record_run(conn, symbol, trade_date, bar_count, status, error=None):
    """종목·날짜별 수집 결과를 기록하고, 이미 있으면 최신 결과로 덮어쓴다."""
    conn.execute(
        "INSERT INTO collect_runs (symbol, trade_date, bar_count, status, error) "
        "VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (symbol, trade_date) DO UPDATE SET bar_count = EXCLUDED.bar_count, "
        "status = EXCLUDED.status, error = EXCLUDED.error, collected_at = now()",
        (symbol, trade_date, bar_count, status, error),
    )


def done_symbols(conn, trade_date):
    """해당 날짜에 ok 또는 empty로 끝난 종목 집합을 반환한다."""
    rows = conn.execute(
        "SELECT symbol FROM collect_runs WHERE trade_date = %s AND status IN ('ok', 'empty')",
        (trade_date,),
    ).fetchall()
    return {r[0] for r in rows}


def failed_runs(conn, trade_date):
    """해당 날짜 error 종목을 (symbol, error) 목록으로 종목코드 순으로 반환한다."""
    return conn.execute(
        "SELECT symbol, error FROM collect_runs "
        "WHERE trade_date = %s AND status = 'error' ORDER BY symbol",
        (trade_date,),
    ).fetchall()
