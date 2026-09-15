"""백테스트 실행 CLI: 봉 조회 → 전략별 엔진 실행 → 결과 저장 → 콘솔 요약."""
import argparse
import os
import sys
from datetime import date, time
from decimal import Decimal
from pathlib import Path

import psycopg
from dotenv import load_dotenv

import engine
import store
from strategies import STRATEGIES

ROOT = Path(__file__).resolve().parent


class UsageError(Exception):
    """잘못된 실행 인자."""


def parse_args(argv):
    """인자를 해석·검증해 (args, {전략이름: 최종 파라미터})를 반환한다. 잘못되면 UsageError."""
    p = argparse.ArgumentParser(description="분봉 백테스트")
    p.add_argument("--strategy", required=True, help="쉼표 구분 전략 이름: " + ", ".join(STRATEGIES))
    p.add_argument("--source", required=True, help="minute_bars.source (kis, yahoo)")
    p.add_argument("--from", dest="date_from", required=True, type=date.fromisoformat)
    p.add_argument("--to", dest="date_to", required=True, type=date.fromisoformat)
    p.add_argument("--symbols", help="쉼표 구분 종목코드, 없으면 전 종목")
    p.add_argument("--param", action="append", default=[], help="key=value, 여러 번 지정")
    p.add_argument("--fee", type=Decimal, default=Decimal("0.00015"))
    p.add_argument("--tax", type=Decimal, default=Decimal("0.002"))
    p.add_argument("--slippage", type=Decimal, default=Decimal("0.0005"))
    p.add_argument("--exit-at", type=time.fromisoformat, default=time(15, 15))
    args = p.parse_args(argv)

    names = args.strategy.split(",")
    unknown = [n for n in names if n not in STRATEGIES]
    if unknown:
        raise UsageError(f"알 수 없는 전략: {', '.join(unknown)} (가능: {', '.join(STRATEGIES)})")
    if args.date_from > args.date_to:
        raise UsageError("--from이 --to보다 늦음")

    overrides = {}
    for item in args.param:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise UsageError(f"--param 형식은 key=value: {item}")
        overrides[key] = value
    unused = [k for k in overrides if not any(k in STRATEGIES[n].defaults for n in names)]
    if unused:
        raise UsageError(f"선택한 전략에 없는 파라미터: {', '.join(unused)}")

    params = {}
    for name in names:
        defaults = STRATEGIES[name].defaults
        try:
            params[name] = {**defaults, **{k: type(defaults[k])(v)
                                           for k, v in overrides.items() if k in defaults}}
        except ValueError as e:
            raise UsageError(f"파라미터 값 오류: {e}") from None
    return args, params


def summarize(trades):
    """거래 목록의 거래 수·승률·평균/합계 수익률·최대 낙폭(%p)·평균 보유 분을 계산한다."""
    if not trades:
        return {"trades": 0}
    rets = [t.return_pct for t in sorted(trades, key=lambda t: t.exit_ts)]
    cum = peak = mdd = Decimal(0)
    for r in rets:
        cum += r
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    n = len(rets)
    return {
        "trades": n,
        "win_rate": sum(r > 0 for r in rets) / n * 100,
        "avg": sum(rets) / n,
        "sum": sum(rets),
        "mdd": mdd,
        "hold_min": sum((t.exit_ts - t.entry_ts).total_seconds() for t in trades) / 60 / n,
    }


def format_summary(run_id, name, params, symbol_count, args, s):
    """실행 1건의 콘솔 요약 문구를 만든다."""
    head = (f"[run {run_id}] {name} {params} | {args.source} {symbol_count}종목 "
            f"{args.date_from}~{args.date_to}")
    if not s["trades"]:
        return head + "\n  거래 0건"
    return (head + f"\n  거래 {s['trades']}건 | 승률 {s['win_rate']:.1f}% | 평균 {s['avg']:.3f}% "
            f"| 합계 {s['sum']:.2f}% | 최대낙폭 {s['mdd']:.2f}%p | 평균보유 {s['hold_min']:.1f}분")


def main(argv=None):
    """백테스트 1회 실행. 성공 0, 봉 없음·DB 오류 1, 잘못된 인자 2를 반환한다."""
    try:
        args, params = parse_args(sys.argv[1:] if argv is None else argv)
    except UsageError as e:
        print(f"인자 오류: {e}")
        return 2
    load_dotenv(ROOT / ".env")
    costs = engine.Costs(args.fee, args.tax, args.slippage, args.exit_at)
    symbols = args.symbols.split(",") if args.symbols else None
    try:
        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
            bars = store.load_bars(conn, args.source, args.date_from, args.date_to, symbols)
            if not bars:
                print("봉 데이터 없음")
                return 1
            for name, p in params.items():
                trades = engine.run(STRATEGIES[name], p, bars, costs)
                run_id = store.save_run(conn, {
                    "strategy": name, "params": p, "source": args.source,
                    "symbols": sorted(bars), "date_from": args.date_from, "date_to": args.date_to,
                    "costs": {"fee": str(args.fee), "tax": str(args.tax),
                              "slippage": str(args.slippage),
                              "exit_at": args.exit_at.strftime("%H:%M")},
                }, trades)
                print(format_summary(run_id, name, p, len(bars), args, summarize(trades)))
    except Exception as e:
        print(f"실행 실패: {type(e).__name__}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
