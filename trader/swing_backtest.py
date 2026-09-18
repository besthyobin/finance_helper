"""일봉 스윙 전략 백테스트 CLI: 일봉 수집(--collect), 개발 구간 조합표, 검증 구간 실행(--validate)."""
import argparse
import logging
import os
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from itertools import product
from pathlib import Path

import psycopg
from dotenv import load_dotenv

import store
from bars import KST
from paper import COSTS
from swing import BreakoutTrend, run_symbol
from toss import TossAuthError, TossClient, TossError

ROOT = Path(__file__).resolve().parent
GRID = {"entry_days": (20, 55), "trend_days": (50, 100), "exit_days": (10, 20)}
VALIDATION_YEARS = 3
NOTE = "(현재 거래대금 상위 종목 기준, 생존 편향 있음)"

log = logging.getLogger("swing_backtest")


class UsageError(Exception):
    """잘못된 실행 인자."""


def years_ago(day, n):
    """day의 n년 전 같은 날짜. 없는 날(2월 29일)은 28일로 바꾼다."""
    try:
        return day.replace(year=day.year - n)
    except ValueError:
        return day.replace(year=day.year - n, day=28)


def parse_args(argv):
    """인자를 해석하고 --validate를 파라미터 dict로 바꾼다. 잘못되면 UsageError."""
    p = argparse.ArgumentParser(description="일봉 스윙 전략 백테스트")
    p.add_argument("--collect", action="store_true", help="대상 종목의 일봉을 받아 저장만 한다")
    p.add_argument("--split", type=date.fromisoformat, help="개발·검증 구간 분할일(기본 3년 전)")
    p.add_argument("--years", type=int, default=10, help="전체 기간(년)")
    p.add_argument("--validate", help="검증 구간에서 돌릴 조합 entry:trend:exit")
    args = p.parse_args(argv)
    if args.years <= 0:
        raise UsageError("--years는 1 이상")
    if args.validate:
        try:
            e, t, x = (int(v) for v in args.validate.split(":"))
            args.validate = {"entry_days": e, "trend_days": t, "exit_days": x}
            BreakoutTrend(args.validate)
        except ValueError:
            raise UsageError("--validate 형식은 entry:trend:exit 양의 정수") from None
    return args


def label(params):
    """조합 이름 entry/trend/exit."""
    return f"{params['entry_days']}/{params['trend_days']}/{params['exit_days']}"


def grid():
    """조합표 파라미터 목록(entry → trend → exit 순)."""
    return [dict(zip(GRID, combo)) for combo in product(*GRID.values())]


def universe(client):
    """거래대금 1년 상위 100 중 상장 중인 보통주 종목코드를 순위순으로 반환한다."""
    rankings = client.rankings()
    stocks = client.stocks([r["symbol"] for r in rankings])
    keep = []
    for r in rankings:
        s = stocks.get(r["symbol"])
        if s and s["security_type"] == "STOCK" and s["common"] and s["active"]:
            keep.append(r["symbol"])
    return keep


def collect(conn, client, symbols, since):
    """종목마다 since 이후 일봉을 받아 저장하고 (성공 종목 수, 실패 종목 목록)을 반환한다. 인증 오류는 올린다."""
    ok, failed = 0, []
    for symbol in symbols:
        try:
            bars = client.fetch_daily_history(symbol, since)
        except TossAuthError:
            raise
        except TossError as e:
            log.error("%s 일봉 수집 실패 %s", symbol, e.code)
            failed.append(symbol)
            continue
        store.save_daily_bars(conn, symbol, bars)
        ok += 1
        log.info("%s 일봉 %d개", symbol, len(bars))
    return ok, failed


def backtest(bars_by_symbol, params):
    """종목마다 새 전략으로 run_symbol을 돌려 전체 거래 목록을 반환한다."""
    trades = []
    for symbol, bars in bars_by_symbol.items():
        trades += run_symbol(symbol, bars, BreakoutTrend(params), COSTS)
    return trades


def summarize_swing(trades, bars_by_symbol):
    """거래 수·승률(%)·평균 수익률(%)·평균 보유일·종목당 누적 수익(%)·종목당 단순 보유 수익(%)을 계산한다."""
    held = {s: bars for s, bars in bars_by_symbol.items() if bars}
    per_symbol = dict.fromkeys(held, Decimal(0))
    for t in trades:
        per_symbol[t.symbol] = per_symbol.get(t.symbol, Decimal(0)) + t.return_pct
    n = len(trades)
    return {
        "trades": n,
        "win_rate": Decimal(sum(t.return_pct > 0 for t in trades)) * 100 / n if n else None,
        "avg": sum((t.return_pct for t in trades), Decimal(0)) / n if n else None,
        "hold_days": Decimal(sum((t.exit_ts.date() - t.entry_ts.date()).days for t in trades)) / n if n else None,
        "cum_per_symbol": sum(per_symbol[s] for s in held) / len(held) if held and n else None,
        "buy_hold": sum((b[-1].close / b[0].open - 1) * 100 for b in held.values()) / len(held) if held else None,
    }


def _fmt(value, spec, suffix=""):
    """값이 있으면 spec으로, 없으면 '-'를 같은 폭으로 쓴다."""
    text = "-" if value is None else f"{value:{spec}}{suffix}"
    width = len(f"{Decimal(0):{spec}}{suffix}")
    return text.rjust(width)


def format_row(name, s):
    """조합 한 줄."""
    return (f"{name:<10} 거래 {s['trades']:5d} | 승률 {_fmt(s['win_rate'], '4.1f', '%')} | "
            f"평균 {_fmt(s['avg'], '+.2f', '%')} | 보유 {_fmt(s['hold_days'], '5.1f', '일')} | "
            f"종목당 누적 {_fmt(s['cum_per_symbol'], '+.1f', '%')} | 단순 보유 {_fmt(s['buy_hold'], '+6.1f', '%')}")


def main(argv=None, today=None):
    """일봉 수집 또는 백테스트 1회. 성공 0, 실행 실패·데이터 없음 1, 잘못된 인자 2를 반환한다."""
    try:
        args = parse_args(sys.argv[1:] if argv is None else argv)
    except UsageError as e:
        print(f"인자 오류: {e}")
        return 2
    today = today or datetime.now(KST).date()
    since = years_ago(today, args.years)
    split = args.split or years_ago(today, VALIDATION_YEARS)
    if not since < split < today:
        print("인자 오류: --split은 시작일과 오늘 사이여야 함")
        return 2
    load_dotenv(ROOT / ".env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
            if args.collect:
                client = TossClient(
                    os.environ["TOSS_CLIENT_ID"], os.environ["TOSS_CLIENT_SECRET"],
                    os.environ.get("TOSS_BASE_URL") or "https://openapi.tossinvest.com",
                    float(os.environ.get("TOSS_RPS") or "15"), ROOT / ".token.json",
                )
                ok, failed = collect(conn, client, universe(client), since)
                print(f"일봉 수집 {ok}종목, 실패 {len(failed)}종목 {' '.join(failed)}".rstrip())
                return 0
            if args.validate:
                title, start, end, combos = "검증 구간", split, today - timedelta(days=1), [args.validate]
            else:
                title, start, end, combos = "개발 구간", since, split - timedelta(days=1), grid()
            bars = store.load_daily_bars(conn, None, start, end)
    except Exception as e:
        # 예외 문자열에 접속 문자열이 섞일 수 있어 종류와 토스 오류 코드만 남긴다
        print(f"실행 실패: {type(e).__name__} {e.code if isinstance(e, TossError) else ''}".rstrip())
        return 1
    if not bars:
        print("일봉 데이터 없음: 먼저 --collect로 수집하세요")
        return 1
    print(f"[스윙 백테스트] {title} {start}~{end} {len(bars)}종목 {NOTE}")
    for params in combos:
        print(format_row(label(params), summarize_swing(backtest(bars, params), bars)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
