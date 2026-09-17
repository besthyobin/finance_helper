"""매일 종목 선정 진입점. 작업 스케줄러가 평일 07:30에 실행해 모의투자 종목(paper_symbols.txt)을 고르고 메일로 알린다."""
import logging
import os
from collections import Counter
from datetime import datetime, timedelta

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
from toss import TossAuthError, TossError

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
        tech = selection.technicals(daily)
        row["metrics"].update(tech=tech, investor=selection.investor_sums(investor),
                              short_avg=selection.short_average(short),
                              margin=credit[0]["margin_balance_rate"] if credit else None,
                              warnings=[w["type"] for w in warnings])
        reason = selection.risk_reason(today, stocks[symbol], warnings, short, credit, tech)
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

    previous = read_symbols(symbols_path) if symbols_path.exists() else []
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
