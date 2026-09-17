"""매일 종목 선정 진입점. 작업 스케줄러가 평일 07:30에 실행해 모의투자 종목(paper_symbols.txt)을 고르고 메일로 알린다."""
import argparse
import logging
import os
import sys
from collections import Counter
from datetime import datetime, time, timedelta
from pathlib import Path

import psycopg
from dotenv import load_dotenv

import collector
import engine
import notify
import selection
import store
from backtest import regular_session
from bars import KST
from collector import read_symbols
from paper import COSTS
from strategies import Orb
from toss import TossAuthError, TossClient, TossError

ROOT = Path(__file__).resolve().parent
REQUIRED_ENV = ("TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET", "DATABASE_URL")
DEADLINE = time(8, 45)

HISTORY_DAYS = 365
CONFIRM_DAYS = 91
DAILY_COUNT = 80
TIMEOUT_REASON = "미평가(시간 초과)"

log = logging.getLogger("selector")


def backfill_days(today):
    """어제부터 365일 전까지의 평일을 최신순으로 반환한다."""
    days = (today - timedelta(days=i) for i in range(1, HISTORY_DAYS + 1))
    return [d for d in days if d.weekday() < 5]


def backtest_trades(conn, symbol, today):
    """최근 365일 수집기 봉(toss) 중 정규장 봉으로 모의투자 조건 orb 백테스트를 돌려 거래 목록을 반환한다."""
    bars = store.load_bars(conn, "toss", today - timedelta(days=HISTORY_DAYS), today - timedelta(days=1), [symbol])
    return engine.run(Orb, {}, regular_session(bars), COSTS)


def write_symbols(path, today, selected):
    """선정 종목을 임시 파일에 쓴 뒤 바꿔치기해 모의투자 종목 파일을 갱신한다."""
    lines = [f"# {today} 종목 선정기 자동 생성"] + [f"{s['symbol']}  # {s['name']}" for s in selected]
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def run(client, conn, symbols_path, now=lambda: datetime.now(KST), deadline=None):
    """종목 선정 1회: 휴장 확인 → 후보 → 위험 필터 → 백필 → 두 구간 백테스트 → 저장 → 파일 → 메일. 종료 코드를 반환한다.
    deadline(datetime)을 넘기면 남은 단계를 멈추고 전날 종목을 유지한다. None이면 마감이 없다."""
    today = now().date()
    if client.market_hours(today) is None:
        log.info("%s 휴장", today)
        return 0

    def over():
        """마감을 넘겼으면 True."""
        return deadline is not None and now() >= deadline

    previous = read_symbols(symbols_path) if symbols_path.exists() else []
    if over():
        log.info("%s 마감 초과, 토스 조회 없이 종료", today)
        empty_summary = {"ranked": 0, "dropped": {}, "candidates": 0, "risk": {}, "backtest_rejected": 0, "passed": 0}
        notify.send_mail(selection.format_mail(today, [], empty_summary, kept=previous, reason="시간 초과"))
        return 0

    rankings = client.rankings()
    stocks = client.stocks([r["symbol"] for r in rankings])
    kept, dropped = selection.candidates(rankings, stocks)
    summary = {"ranked": len(rankings), "dropped": dict(Counter(reason for _, reason in dropped)),
               "candidates": len(kept), "risk": Counter(), "backtest_rejected": 0, "passed": 0}
    rows, survivors, passed = {}, [], []
    timed_out = False

    for cand in kept:
        if over():
            timed_out = True
            break
        symbol = cand["symbol"]
        row = rows[symbol] = {"symbol": symbol, "name": cand["name"], "rank": cand["rank"], "status": "rejected",
                              "reason": None, "metrics": {"last_price": cand["last_price"]}}
        try:
            warnings = client.warnings(symbol)
            short = client.short_selling(symbol, 5)
            credit = client.credit_trades(symbol, 1)
            investor = client.investor_trading(symbol, 5)
            daily = [d for d in client.fetch_daily(symbol, DAILY_COUNT) if d.date < today]
        except TossAuthError:
            raise
        except TossError as e:
            row["reason"] = "조회 실패"
            summary["risk"]["조회 실패"] += 1
            log.error("%s 조회 실패 %s", symbol, e.code)
            continue
        try:
            tech = selection.technicals(daily)
            row["metrics"].update(tech=tech, investor=selection.investor_sums(investor),
                                  short_avg=selection.short_average(short),
                                  margin=credit[0]["margin_balance_rate"] if credit else None,
                                  warnings=[w["type"] for w in warnings])
            reason = selection.risk_reason(today, stocks[symbol], warnings, short, credit, tech)
        except TossAuthError:
            raise
        except Exception as e:
            row["reason"] = "계산 실패"
            summary["risk"]["계산 실패"] += 1
            log.error("%s 계산 실패 %s", symbol, type(e).__name__)
            continue
        if reason:
            row["reason"] = reason
            summary["risk"][reason] += 1
        else:
            survivors.append(cand)

    if survivors and not timed_out:
        collector.collect(conn, client, [c["symbol"] for c in survivors], backfill_days(today), stop=over)
        timed_out = over()

    if not timed_out:
        confirm_from = today - timedelta(days=CONFIRM_DAYS)
        for cand in survivors:
            if over():
                timed_out = True
                break
            row = rows[cand["symbol"]]
            result, reason = selection.evaluate(
                *selection.split_trades(backtest_trades(conn, cand["symbol"], today), confirm_from))
            row["metrics"]["backtest"] = result
            if reason:
                row["reason"] = reason
                summary["backtest_rejected"] += 1
            else:
                row["status"] = "passed"
                passed.append({**cand, "backtest": result, "tech": row["metrics"]["tech"],
                               "investor": row["metrics"]["investor"]})

    selected = [] if timed_out else selection.rank_selected(passed)
    for s in selected:
        rows[s["symbol"]]["status"] = "selected"
    for row in rows.values():
        if row["status"] == "rejected" and row["reason"] is None:
            row["reason"] = TIMEOUT_REASON
    summary["passed"] = len(passed)
    store.save_candidates(conn, today, list(rows.values()))

    if timed_out:
        reason = "시간 초과"
    elif not selected:
        reason = "선정 없음"
    else:
        reason = None
        write_symbols(symbols_path, today, selected)
    log.info("%s 선정 %s", today, [s["symbol"] for s in selected] if not reason else reason)
    notify.send_mail(selection.format_mail(today, selected, summary, kept=previous, reason=reason))
    return 0


def setup_logging(today):
    """콘솔과 logs/selector-YYYY-MM-DD.log에 로그를 남기도록 설정한다."""
    (ROOT / "logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler(ROOT / "logs" / f"selector-{today}.log", encoding="utf-8")],
    )


def main(argv=None, now=None):
    """종목 선정 1회 실행. 정상·휴장·시간 초과면 0, 설정·DB·인증 등 실행 전체 실패면 1을 반환한다."""
    parser = argparse.ArgumentParser(description="매일 모의투자 종목 선정")
    parser.add_argument("--no-deadline", action="store_true", help="08:45 마감 없이 실행 (첫 백필용)")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    now = now or datetime.now(KST)
    load_dotenv(ROOT / ".env")
    setup_logging(now.date())
    try:
        missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
        if missing:
            log.error(".env 누락: %s", ", ".join(missing))
            raise RuntimeError("missing env")
        client = TossClient(
            os.environ["TOSS_CLIENT_ID"], os.environ["TOSS_CLIENT_SECRET"],
            os.environ.get("TOSS_BASE_URL") or "https://openapi.tossinvest.com",
            float(os.environ.get("TOSS_RPS") or "15"), ROOT / ".token.json",
        )
        deadline = None if args.no_deadline else datetime.combine(now.date(), DEADLINE, KST)
        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
            return run(client, conn, ROOT / "paper_symbols.txt", deadline=deadline)
    except Exception as e:
        # 예외 문자열에 접속 문자열 등이 섞일 수 있어 종류와 토스 오류 코드만 남긴다
        log.error("실행 실패: %s %s", type(e).__name__, e.code if isinstance(e, TossError) else "")
        notify.send_mail(f"[종목선정] {now.date()} 실행 실패: {type(e).__name__}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
