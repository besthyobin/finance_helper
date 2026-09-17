"""장중 모의투자 진입점. 작업 스케줄러가 평일 08:55에 실행하고, 매분 끝난 1분봉으로 orb 전략을 가상 체결한다."""
import logging
import os
import sys
import time as _time
from datetime import datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

import psycopg
from dotenv import load_dotenv

import engine
import notify
import store
from backtest import REGULAR_CLOSE, REGULAR_OPEN
from bars import KST
from collector import read_symbols
from strategies import Orb
from toss import TossAuthError, TossClient, TossError

ROOT = Path(__file__).resolve().parent
REQUIRED_ENV = ("TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET", "DATABASE_URL", "PAPER_CAPITAL")

COSTS = engine.Costs(Decimal("0.00015"), Decimal("0.002"), Decimal("0.0005"), time(15, 15))
FETCH_SECOND = 15
RECENT_COUNT = 5
LOOP_END = time(15, 31)
ALERT_AFTER_FAILS = 5

log = logging.getLogger("paper")


def pnl_krw(qty, trade, costs):
    """수량과 거래로 수수료·세금을 반영한 원화 손익을 계산한다."""
    return qty * (trade.exit_price * (1 - costs.fee - costs.tax) - trade.entry_price * (1 + costs.fee))


def regular(bars, now):
    """정규장(09:00~15:29 시작) 봉 중 now 이전에 끝난 봉만 남긴다."""
    return [b for b in bars
            if REGULAR_OPEN <= b.ts.time() < REGULAR_CLOSE and b.ts + timedelta(minutes=1) <= now]


def next_fetch_at(now):
    """now보다 늦은 가장 가까운 매분 FETCH_SECOND초 시각을 반환한다."""
    at = now.replace(second=FETCH_SECOND, microsecond=0)
    return at if at > now else at + timedelta(minutes=1)


def trade_key(trade):
    """마감 비교에 쓰는 거래 값(진입·청산 시각과 가격, 청산 사유)."""
    return (trade.entry_ts, trade.entry_price, trade.exit_ts, trade.exit_price, trade.exit_reason)


class Paper:
    """종목별 DayRunner와 보유 수량을 들고, 봉을 처리해 거래·상태를 저장하고 체결을 알린다."""

    def __init__(self, conn, symbols, capital, today):
        """종목마다 새 orb 전략의 DayRunner를 만들고 종목당 금액을 정한다."""
        self.conn = conn
        self.today = today
        self.budget = capital / len(symbols)
        self.runners = {s: engine.DayRunner(s, Orb({}), COSTS) for s in symbols}
        self.qty = dict.fromkeys(symbols, 0)
        self.trades = {s: [] for s in symbols}  # 전략 기준 거래 전체(0주 포함), 마감 비교용
        self.saved = []                           # 저장한 거래의 (종목, 원화 손익)
        self.fails = dict.fromkeys(symbols, 0)

    def poll(self, client, symbol, now, catch_up=False):
        """끝난 봉을 받아 처리한다. 따라잡기거나 빈 분이 있으면 하루치를 받는다.
        인증 오류는 올리고, 토스 오류는 로그만 남기며 연속 5회째에 한 번 알린다.
        그 밖의 오류(예: DB 저장 실패)는 재시작 따라잡기로 복구되지만, 그때까지 거래가 비므로 즉시 알린다."""
        try:
            bars = [] if catch_up else regular(client.fetch_recent(symbol, RECENT_COUNT), now)
            if catch_up or self._has_gap(symbol, bars):
                bars = regular(client.fetch_day(symbol, self.today), now)
            self.process(symbol, bars, alert=not catch_up)
            self.fails[symbol] = 0
        except TossAuthError:
            raise
        except TossError as e:
            self.fails[symbol] += 1
            log.error("%s 조회·처리 실패 %d회: %s %s", symbol, self.fails[symbol], type(e).__name__, e.code)
            if self.fails[symbol] == ALERT_AFTER_FAILS:
                notify.send_telegram(f"[모의투자] {symbol} 연속 {ALERT_AFTER_FAILS}분 실패: {type(e).__name__}")
        except Exception as e:
            # ponytail: 처리 중 DB 저장 등이 실패하면 runner는 앞서 나가고 그 거래는 저장되지 않는다.
            # 재시작 따라잡기로 복구되니, 알림을 보고 재시작할 것
            log.exception("%s 처리 실패: %s", symbol, type(e).__name__)
            notify.send_telegram(f"[모의투자] {symbol} 처리 실패: {type(e).__name__}, DB·코드 확인 후 재시작 필요")

    def _new(self, symbol, bars):
        """마지막으로 받은 봉 이후의 봉만 반환한다."""
        last = self.runners[symbol].last_bar
        return [b for b in bars if last is None or b.ts > last.ts]

    def _has_gap(self, symbol, bars):
        """새 봉의 첫 봉이 마지막 봉 바로 다음(받은 봉이 없으면 09:00)이 아니면 True."""
        new = self._new(symbol, bars)
        if not new:
            return False
        last = self.runners[symbol].last_bar
        expected = last.ts + timedelta(minutes=1) if last else datetime.combine(self.today, REGULAR_OPEN, KST)
        return new[0].ts != expected

    def process(self, symbol, bars, alert=True):
        """새 봉을 DayRunner에 넣어 체결을 처리하고, 새 봉이 있었으면 상태를 저장한다."""
        new = self._new(symbol, bars)
        for bar in new:
            for kind, item in self.runners[symbol].step(bar):
                if kind == "buy":
                    self._buy(symbol, item, alert)
                else:
                    self._sell(symbol, item, alert)
        if new:
            store.save_paper_status(self.conn, self.status_row(symbol))

    def _buy(self, symbol, bar, alert):
        """진입 체결: 종목당 금액으로 살 수 있는 정수 주를 정한다. 0주면 거래하지 않는다."""
        price = bar.open * (1 + COSTS.slippage)
        qty = int(self.budget // price)
        self.qty[symbol] = qty
        if qty == 0:
            log.warning("%s %s 금액 부족으로 매수 안 함 (매수가 %s, 종목당 %s)", symbol, bar.ts, price, self.budget)
            return
        log.info("%s %s 매수 %d주 @ %s", symbol, bar.ts, qty, price)
        if alert:
            notify.send_telegram(f"[모의투자] 매수 {symbol} {qty}주 @ {price:,.0f}원 ({bar.ts:%H:%M})")

    def _sell(self, symbol, trade, alert):
        """청산 체결: 비교용 거래에 더하고, 수량이 있으면 원화 손익과 함께 저장·알린다."""
        self.trades[symbol].append(trade)
        qty, self.qty[symbol] = self.qty[symbol], 0
        if qty == 0:
            return
        pnl = pnl_krw(qty, trade, COSTS)
        store.save_paper_trade(self.conn, Orb.name, qty, trade, pnl)
        self.saved.append((symbol, pnl))
        log.info("%s %s 매도 %d주 @ %s 손익 %s", symbol, trade.exit_ts, qty, trade.exit_price, pnl)
        if alert:
            notify.send_telegram(
                f"[모의투자] 매도 {symbol} {qty}주 @ {trade.exit_price:,.0f}원 ({trade.exit_ts:%H:%M}) "
                f"손익 {pnl:+,.0f}원 ({trade.return_pct:+.2f}%)")

    def status_row(self, symbol):
        """paper_status에 저장할 현재 상태 dict를 만든다. 0주 진입은 미보유로 본다."""
        runner = self.runners[symbol]
        last, holding, qty = runner.last_bar, runner.holding, self.qty[symbol]
        held = holding is not None and qty > 0
        return {
            "symbol": symbol, "trade_date": self.today,
            "last_bar_ts": last.ts if last else None, "last_close": last.close if last else None,
            "qty": qty if held else 0,
            "entry_ts": holding.ts if held else None,
            "entry_price": holding.open * (1 + COSTS.slippage) if held else None,
        }

    def close(self, client, now):
        """보유 중이면 청산하고, 오늘 봉 전체의 백테스트 거래와 실시간 거래를 비교한 요약 문구를 반환한다."""
        diffs = []
        for symbol, runner in self.runners.items():
            trade = runner.finish()
            if trade:
                self._sell(symbol, trade, alert=True)
                store.save_paper_status(self.conn, self.status_row(symbol))
            try:
                bars = regular(client.fetch_day(symbol, self.today), now)
            except TossError as e:
                diffs.append(f"{symbol} 비교 실패 {e.code}")
                continue
            expected = [trade_key(t) for t in engine.run_day(symbol, Orb({}), bars, COSTS)]
            live = [trade_key(t) for t in self.trades[symbol]]
            if live != expected:
                diffs.append(f"{symbol} 불일치: 실시간 {len(live)}건 / 마감 {len(expected)}건")
                log.warning("%s 불일치 실시간 %s / 마감 %s", symbol, live, expected)
        return self.summary(diffs)

    def summary(self, diffs):
        """일일 요약 문구: 전체·종목별 거래 수와 원화 손익, 마감 비교 결과."""
        wins = sum(1 for _, p in self.saved if p > 0)
        total = sum((p for _, p in self.saved), Decimal(0))
        lines = [f"[모의투자] {self.today} 요약",
                 f"거래 {len(self.saved)}건 (승 {wins} / 패 {len(self.saved) - wins}) 손익 {total:+,.0f}원"]
        for symbol in self.runners:
            pnls = [p for s, p in self.saved if s == symbol]
            if pnls:
                lines.append(f"{symbol} {len(pnls)}건 {sum(pnls):+,.0f}원")
        lines.append("마감 비교: " + (", ".join(diffs) if diffs else "일치"))
        return "\n".join(lines)


def run(client, conn, symbols, capital, now=lambda: datetime.now(KST), sleep=_time.sleep):
    """모의투자 하루 실행: 장 시간 확인 → 따라잡기 → 매분 조회 → 15:31 마감 비교·요약. 종료 코드를 반환한다."""
    today = now().date()
    hours = client.market_hours(today)
    if hours is None:
        log.info("%s 휴장", today)
        return 0
    start, end = hours
    if (start.time(), end.time()) != (REGULAR_OPEN, REGULAR_CLOSE):
        log.warning("%s 정규장 시간 변경일 %s~%s", today, start, end)
        notify.send_telegram(f"[모의투자] {today} 정규장 시간 변경일({start:%H:%M}~{end:%H:%M}), 모의투자 안 함")
        return 0

    paper = Paper(conn, symbols, capital, today)
    for symbol in symbols:
        paper.poll(client, symbol, now(), catch_up=True)
    held = sum(1 for s in symbols if paper.status_row(s)["qty"])
    notify.send_telegram(f"[모의투자] {today} 시작(따라잡기 완료): {len(symbols)}종목, "
                         f"보유 {held}종목, 거래 {len(paper.saved)}건")

    while True:
        at = next_fetch_at(now())
        if at.time() >= LOOP_END:
            break
        sleep(max(0, (at - now()).total_seconds()))
        for symbol in symbols:
            paper.poll(client, symbol, now())

    loop_end = datetime.combine(today, LOOP_END, KST)
    if now() < loop_end:
        sleep(max(0, (loop_end - now()).total_seconds()))
    notify.send_telegram(paper.close(client, now()))
    return 0


def setup_logging(today):
    """콘솔과 logs/paper-YYYY-MM-DD.log에 로그를 남기도록 설정한다."""
    (ROOT / "logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler(ROOT / "logs" / f"paper-{today}.log", encoding="utf-8")],
    )


def main(now=None):
    """모의투자 1일 실행. 정상·휴장이면 0, 설정·DB·인증 등 실행 전체 실패면 1을 반환한다."""
    now = now or datetime.now(KST)
    load_dotenv(ROOT / ".env")
    setup_logging(now.date())
    try:
        missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
        if missing:
            log.error(".env 누락: %s", ", ".join(missing))
            raise RuntimeError("missing env")
        capital = Decimal(os.environ["PAPER_CAPITAL"])
        symbols = read_symbols(ROOT / "paper_symbols.txt")
        client = TossClient(
            os.environ["TOSS_CLIENT_ID"], os.environ["TOSS_CLIENT_SECRET"],
            os.environ.get("TOSS_BASE_URL") or "https://openapi.tossinvest.com",
            float(os.environ.get("TOSS_RPS") or "15"), ROOT / ".token.json",
        )
        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
            return run(client, conn, symbols, capital)
    except Exception as e:
        # 예외 문자열에 접속 문자열 등이 섞일 수 있어 종류와 토스 오류 코드만 남긴다
        log.error("실행 실패: %s %s", type(e).__name__, e.code if isinstance(e, TossError) else "")
        notify.send_telegram(f"[모의투자] {now.date()} 실행 실패: {type(e).__name__}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
