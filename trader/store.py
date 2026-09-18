"""minute_bars·daily_bars·collect_runs·backtest·paper·selection 테이블 저장과 조회."""
import json
from datetime import datetime, time, timedelta
from functools import partial

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from bars import KST, Bar, DailyBar


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


def done_days(conn, symbol):
    """해당 종목에서 ok 또는 empty로 끝난 날짜 집합을 반환한다."""
    rows = conn.execute(
        "SELECT trade_date FROM collect_runs WHERE symbol = %s AND status IN ('ok', 'empty')",
        (symbol,),
    ).fetchall()
    return {r[0] for r in rows}


def save_paper_status(conn, row):
    """종목별 모의투자 상태를 symbol 기준으로 덮어쓴다. row는 updated_at을 뺀 paper_status 컬럼 dict."""
    conn.execute(
        "INSERT INTO paper_status (symbol, trade_date, last_bar_ts, last_close, qty, entry_ts, entry_price) "
        "VALUES (%(symbol)s, %(trade_date)s, %(last_bar_ts)s, %(last_close)s, %(qty)s, %(entry_ts)s, "
        "%(entry_price)s) "
        "ON CONFLICT (symbol) DO UPDATE SET trade_date = EXCLUDED.trade_date, "
        "last_bar_ts = EXCLUDED.last_bar_ts, last_close = EXCLUDED.last_close, qty = EXCLUDED.qty, "
        "entry_ts = EXCLUDED.entry_ts, entry_price = EXCLUDED.entry_price, updated_at = now()",
        row,
    )


def save_paper_trade(conn, strategy, qty, trade, pnl_krw):
    """완결된 모의 거래를 저장하고, 같은 (종목, 진입 시각)이 있으면 덮어쓴다."""
    conn.execute(
        "INSERT INTO paper_trades (symbol, strategy, entry_ts, qty, entry_price, exit_ts, exit_price, "
        "pnl_krw, return_pct, exit_reason) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (symbol, entry_ts) DO UPDATE SET strategy = EXCLUDED.strategy, qty = EXCLUDED.qty, "
        "entry_price = EXCLUDED.entry_price, exit_ts = EXCLUDED.exit_ts, exit_price = EXCLUDED.exit_price, "
        "pnl_krw = EXCLUDED.pnl_krw, return_pct = EXCLUDED.return_pct, exit_reason = EXCLUDED.exit_reason",
        (trade.symbol, strategy, trade.entry_ts, qty, trade.entry_price, trade.exit_ts, trade.exit_price,
         pnl_krw, trade.return_pct, trade.exit_reason),
    )


def load_paper_status(conn):
    """모의투자 종목별 상태 전체를 symbol 오름차순 dict 목록으로 반환한다."""
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "SELECT symbol, trade_date, last_bar_ts, last_close, qty, entry_ts, entry_price, updated_at "
            "FROM paper_status ORDER BY symbol"
        ).fetchall()


def load_paper_trades(conn, day):
    """청산 시각의 KST 날짜가 day인 모의 거래를 청산 시각 오름차순 dict 목록으로 반환한다."""
    start = datetime.combine(day, time(0), KST)
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "SELECT symbol, strategy, qty, entry_ts, entry_price, exit_ts, exit_price, pnl_krw, return_pct, "
            "exit_reason FROM paper_trades WHERE exit_ts >= %s AND exit_ts < %s ORDER BY exit_ts, symbol",
            (start, start + timedelta(days=1)),
        ).fetchall()


def load_paper_daily(conn):
    """KST 청산일별 거래 수·수익 거래 수·원화 손익 합계를 최신 날짜부터 반환한다."""
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "SELECT (exit_ts AT TIME ZONE 'Asia/Seoul')::date AS day, count(*)::int AS trades, "
            "(count(*) FILTER (WHERE pnl_krw > 0))::int AS wins, sum(pnl_krw) AS pnl_krw "
            "FROM paper_trades GROUP BY 1 ORDER BY 1 DESC"
        ).fetchall()


_dumps = partial(json.dumps, default=str, ensure_ascii=False)


def save_candidates(conn, run_date, rows):
    """그날 종목 선정 결과를 한 트랜잭션으로 지우고 다시 저장한다. metrics의 Decimal·date는 문자열로 저장한다."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM selection_candidates WHERE run_date = %s", (run_date,))
        cur.executemany(
            "INSERT INTO selection_candidates (run_date, symbol, name, rank, status, reason, metrics) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [(run_date, r["symbol"], r["name"], r["rank"], r["status"], r["reason"], Jsonb(r["metrics"], dumps=_dumps))
             for r in rows],
        )


def load_candidates(conn, run_date):
    """그날 종목 선정 결과를 symbol 오름차순 dict 목록으로 반환한다."""
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "SELECT symbol, name, rank, status, reason, metrics FROM selection_candidates "
            "WHERE run_date = %s ORDER BY symbol", (run_date,),
        ).fetchall()


def save_daily_bars(conn, symbol, bars):
    """일봉을 한 트랜잭션으로 저장한다. 같은 (symbol, day)는 새 값으로 덮어쓴다(수정주가 갱신)."""
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO daily_bars (symbol, day, open, high, low, close, volume) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (symbol, day) DO UPDATE SET open = EXCLUDED.open, high = EXCLUDED.high, "
            "low = EXCLUDED.low, close = EXCLUDED.close, volume = EXCLUDED.volume",
            [(symbol, b.date, b.open, b.high, b.low, b.close, b.volume) for b in bars],
        )


def load_daily_bars(conn, symbols, date_from, date_to):
    """종목·기간(양끝 포함) 일봉을 {symbol: 날짜 오름차순 DailyBar}로 반환한다. symbols가 None이면 전 종목."""
    sql = ("SELECT symbol, day, open, high, low, close, volume FROM daily_bars "
           "WHERE day >= %s AND day <= %s")
    args = [date_from, date_to]
    if symbols is not None:
        sql += " AND symbol = ANY(%s)"
        args.append(list(symbols))
    out = {}
    for symbol, day, o, h, l, c, v in conn.execute(sql + " ORDER BY symbol, day", args):
        out.setdefault(symbol, []).append(DailyBar(day, o, h, l, c, v))
    return out
