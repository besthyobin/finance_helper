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
import policy
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
LIVE_SOURCE = "toss_live"  # 장중에 받은 봉. 수집기 확정 봉(toss)과 따로 둔다

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


def read_symbol_names(path):
    """종목코드 파일에서 {symbol: name} dict를 읽는다. 주석이 없으면 코드 자체를 이름으로 둔다."""
    if not path.exists():
        return {}
    names = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split("#", 1)
        code = parts[0].strip()
        if code:
            names[code] = parts[1].strip() if len(parts) > 1 and parts[1].strip() else code
    return names


def load_symbol_names(conn, symbols, fallback_names=None):
    """DB selection_candidates 및 fallback에서 {symbol: name} 매핑을 가져온다."""
    names = dict(fallback_names or {})
    missing = [s for s in symbols if s not in names or names[s] == s]
    if missing and conn:
        try:
            with conn.cursor() as cur:
                rows = cur.execute(
                    "SELECT symbol, name FROM selection_candidates WHERE symbol = ANY(%s) ORDER BY run_date DESC",
                    (missing,)
                ).fetchall()
                for sym, name in rows:
                    if sym not in names or names[sym] == sym:
                        names[sym] = name
        except Exception:
            pass
    return {s: names.get(s, s) for s in symbols}


def format_buy_mail(symbol, bar, qty, price, budget, policy_data, name=None):
    """매수 체결 알림: 종목명, 단가, 수량, 총금액, 적용 정책(손절/익절), 배정 예산 현황."""
    display = f"{name}({symbol})" if name and name != symbol else symbol
    total_amount = price * qty
    remaining = budget - total_amount
    stop_pct = policy_data.get("stop_pct", -1.0)
    target_pct = policy_data.get("target_pct", 2.0)
    stop_price = price * (1 + Decimal(str(stop_pct)) / Decimal("100"))
    target_price = price * (1 + Decimal(str(target_pct)) / Decimal("100"))

    lines = [
        f"[모의투자] 매수 체결: {display} {qty:,d}주 @ {price:,.0f}원 ({bar.ts:%H:%M})",
        "",
        f"■ 종목: {display}",
        f"■ 체결 시각: {bar.ts:%Y-%m-%d %H:%M} (KST)",
        f"■ 체결 단가: {price:,.0f}원 (슬리피지 0.05% 반영)",
        f"■ 체결 수량: {qty:,d}주",
        f"■ 매수 총액: {total_amount:,.0f}원",
        "",
        "■ 적용 매매 정책",
        f"- 운용 전략: {policy_data.get('strategy', 'orb').upper()} (시초가 돌파)",
        f"- 손절 기준: {stop_pct}% (예상 손절가: 약 {stop_price:,.0f}원)",
        f"- 익절 기준: +{target_pct}% (예상 익절가: 약 {target_price:,.0f}원)",
        "",
        "■ 계좌 배정 현황",
        f"- 종목 배정예산: {budget:,.0f}원 중 {total_amount:,.0f}원 체결 (잔여: {remaining:,.0f}원)",
    ]
    return "\n".join(lines)


def format_sell_mail(symbol, trade, qty, pnl, saved, costs, name=None):
    """매도/청산 체결 알림: 종목명, 청산사유, 진입/청산 상세, 실현손익, 보유시간, 당일 누적 거래 현황."""
    display = f"{name}({symbol})" if name and name != symbol else symbol
    hold_minutes = int((trade.exit_ts - trade.entry_ts).total_seconds() // 60)
    entry_total = trade.entry_price * qty
    exit_total = trade.exit_price * qty

    reason_map = {
        "signal": "목표 익절 또는 손절 도달",
        "close_time": f"{costs.exit_at.strftime('%H:%M')} 당일 마감 강제 청산",
        "day_end": "장 마감 청산",
    }
    reason_kr = reason_map.get(trade.exit_reason, trade.exit_reason)

    total_trades = len(saved)
    wins = sum(1 for _, p in saved if p > 0)
    total_pnl = sum((p for _, p in saved), Decimal(0))
    win_rate = (wins / total_trades * 100) if total_trades else 0.0

    lines = [
        f"[모의투자] 매도 체결: {display} {qty:,d}주 @ {trade.exit_price:,.0f}원 ({trade.exit_ts:%H:%M}) 손익 {pnl:+,.0f}원 ({trade.return_pct:+.2f}%)",
        "",
        f"■ 종목: {display}",
        f"■ 청산 사유: {reason_kr}",
        "",
        "■ 체결 상세",
        f"- 매수 진입: {trade.entry_ts:%H:%M} @ {trade.entry_price:,.0f}원 ({qty:,d}주, 총 {entry_total:,.0f}원)",
        f"- 매도 청산: {trade.exit_ts:%H:%M} @ {trade.exit_price:,.0f}원 ({qty:,d}주, 총 {exit_total:,.0f}원)",
        f"- 보유 시간: {hold_minutes}분",
        "",
        "■ 실현 손익 결과",
        f"- 실현 손익: {pnl:+,.0f}원",
        f"- 실현 수익률: {trade.return_pct:+.2f}% (수수료·세금·슬리피지 비용 차감 반영)",
        "",
        "■ 오늘 누적 거래 현황",
        f"- 당일 거래: 총 {total_trades}건 (승 {wins} / 패 {total_trades - wins}, 승률 {win_rate:.1f}%)",
        f"- 누적 실현손익: {total_pnl:+,.0f}원",
    ]
    return "\n".join(lines)


def format_close_mail(today, capital, runners, saved, trades_by_symbol, diffs, names=None, policy_data=None):
    """일일 마감 요약: 자본금 대비 마감 총 자산 및 일일 수익률, 전체 통계, 종목별 체결 리스트, 마감 정합성 비교."""
    names = names or {}
    total_pnl = sum((p for _, p in saved), Decimal(0))
    wins = sum(1 for _, p in saved if p > 0)
    total_trades = len(saved)
    win_rate = (wins / total_trades * 100) if total_trades else 0.0
    final_asset = capital + total_pnl
    ret_pct = (total_pnl / capital * 100) if capital else Decimal(0)

    lines = [
        f"[모의투자] {today} 마감 요약: 거래 {total_trades}건, 실현손익 {total_pnl:+,.0f}원 ({ret_pct:+.2f}%)",
        "",
        "■ 계좌 총 자산 현황",
        f"- 시작 자본금: {capital:,.0f}원",
        f"- 마감 총 자산: {final_asset:,.0f}원",
        f"- 당일 총 손익: {total_pnl:+,.0f}원 (수익률 {ret_pct:+.2f}%)",
        "",
        "■ 거래 종합 성과",
        f"- 총 거래: {total_trades}건 (승 {wins} / 패 {total_trades - wins}, 승률 {win_rate:.1f}%)",
        f"- 총 실현손익: {total_pnl:+,.0f}원",
        "",
        f"■ 종목별 거래 상세 ({len(runners)}종목)",
    ]

    for symbol in runners:
        name = names.get(symbol, symbol)
        display = f"{name}({symbol})" if name != symbol else symbol
        sym_saved = [p for s, p in saved if s == symbol]
        sym_trades = trades_by_symbol.get(symbol, [])
        if sym_saved:
            sym_total = sum(sym_saved)
            lines.append(f"▶ {display}: {len(sym_saved)}건 거래, 손익 {sym_total:+,.0f}원")
            for t in sym_trades:
                lines.append(
                    f"  - 진입 {t.entry_ts:%H:%M} @ {t.entry_price:,.0f}원 → "
                    f"청산 {t.exit_ts:%H:%M} @ {t.exit_price:,.0f}원 | "
                    f"손익 {t.return_pct:+.2f}% ({t.exit_reason})"
                )
        else:
            lines.append(f"▶ {display}: 거래 없음 (대기)")

    lines.append("")
    lines.append("■ 시스템 검증: " + (", ".join(diffs) if diffs else "실시간 체결과 백테스트 완전 일치"))
    return "\n".join(lines)


class Paper:
    """종목별 DayRunner와 보유 수량을 들고, 봉을 처리해 거래·상태를 저장하고 체결을 알린다."""

    def __init__(self, conn, symbols, capital, today, policy_data=None, names=None):
        """종목마다 새 orb 전략의 DayRunner를 만들고 종목당 금액을 정한다."""
        self.conn = conn
        self.today = today
        self.policy = policy_data or policy.load_policy()
        self.capital = capital
        self.budget = capital / len(symbols)
        self.names = names or {}
        self.runners = {s: engine.DayRunner(s, Orb(self.policy), COSTS) for s in symbols}
        self.qty = dict.fromkeys(symbols, 0)
        self.trades = {s: [] for s in symbols}  # 전략 기준 거래 전체(0주 포함), 마감 비교용
        self.saved = []                           # 저장한 거래의 (종목, 원화 손익)
        self.fails = dict.fromkeys(symbols, 0)
        self.errors = dict.fromkeys(symbols, 0)

    def sync_policy(self):
        """policy.json 파일이 변경되었으면 실시간으로 각 종목 DayRunner 전략에 반영한다."""
        new_policy = policy.load_policy()
        if new_policy != self.policy:
            old_stop = self.policy.get("stop_pct")
            new_stop = new_policy.get("stop_pct")
            old_target = self.policy.get("target_pct")
            new_target = new_policy.get("target_pct")
            log.info("매매 정책 실시간 반영: 손절 %s%% -> %s%%, 익절 %s%% -> %s%%",
                     old_stop, new_stop, old_target, new_target)
            self.policy = new_policy
            for s, runner in self.runners.items():
                runner.strategy.stop = Decimal(str(new_policy["stop_pct"]))
                runner.strategy.target = Decimal(str(new_policy["target_pct"]))

    def poll(self, client, symbol, now, catch_up=False):
        """끝난 봉을 받아 처리한다. 따라잡기거나 빈 분이 있으면 하루치를 받는다.
        인증 오류는 올리고, 토스 오류는 로그만 남기며 연속 5회째에 한 번 알린다.
        그 밖의 오류(예: DB 저장 실패)는 재시작 따라잡기로 복구되지만, 그때까지 거래가 비므로
        실패마다 로그를 남기되 알림은 연속 실패 시작(1회째)에만 보낸다."""
        try:
            bars = [] if catch_up else regular(client.fetch_recent(symbol, RECENT_COUNT), now)
            if catch_up or self._has_gap(symbol, bars):
                bars = regular(client.fetch_day(symbol, self.today), now)
            self.process(symbol, bars, alert=not catch_up)
            self.fails[symbol] = 0
            self.errors[symbol] = 0
        except TossAuthError:
            raise
        except TossError as e:
            self.fails[symbol] += 1
            log.error("%s 조회·처리 실패 %d회: %s %s", symbol, self.fails[symbol], type(e).__name__, e.code)
            if self.fails[symbol] == ALERT_AFTER_FAILS:
                notify.send_mail(f"[모의투자] {symbol} 연속 {ALERT_AFTER_FAILS}분 실패: {type(e).__name__}")
        except Exception as e:
            # ponytail: 처리 중 DB 저장 등이 실패하면 runner는 앞서 나가고 그 거래는 저장되지 않는다.
            # 재시작 따라잡기로 복구되니, 알림을 보고 재시작할 것. 알림은 연속 실패가 계속되는 동안
            # 반복하지 않도록 스트릭이 1이 될 때(처음 실패했을 때)만 보낸다
            self.errors[symbol] += 1
            log.exception("%s 처리 실패: %s", symbol, type(e).__name__)
            if self.errors[symbol] == 1:
                notify.send_mail(f"[모의투자] {symbol} 처리 실패: {type(e).__name__}, DB·코드 확인 후 재시작 필요")

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
        """새 봉을 차트용으로 저장하고 DayRunner에 넣어 체결을 처리한 뒤, 새 봉이 있었으면 상태를 저장한다."""
        new = self._new(symbol, bars)
        if new:
            store.save_bars(self.conn, symbol, new, source=LIVE_SOURCE)
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
            name = self.names.get(symbol, symbol)
            notify.send_mail(format_buy_mail(symbol, bar, qty, price, self.budget, self.policy, name=name))

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
            name = self.names.get(symbol, symbol)
            notify.send_mail(format_sell_mail(symbol, trade, qty, pnl, self.saved, COSTS, name=name))

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
            expected = [trade_key(t) for t in engine.run_day(symbol, Orb(self.policy), bars, COSTS)]
            live = [trade_key(t) for t in self.trades[symbol]]
            if live != expected:
                diffs.append(f"{symbol} 불일치: 실시간 {len(live)}건 / 마감 {len(expected)}건")
                log.warning("%s 불일치 실시간 %s / 마감 %s", symbol, live, expected)
            else:
                self.trades[symbol] = [t for t in engine.run_day(symbol, Orb(self.policy), bars, COSTS)]
        return self.summary(diffs)

    def summary(self, diffs):
        """일일 요약 문구: 자본금 대비 마감 총 자산, 수익률, 전체·종목별 체결 상세, 마감 비교 결과."""
        return format_close_mail(
            self.today, self.capital, self.runners, self.saved,
            self.trades, diffs, names=self.names, policy_data=self.policy
        )

    def start_summary(self, names=None):
        """시작(따라잡기 완료) 알림 문구: 자본금, 보유 종목 상세(수량·매수가·평가손익), 대기 종목, 완결 거래."""
        names = names or {}
        held_symbols = [s for s in self.runners if self.qty[s] > 0]
        total_capital = self.budget * len(self.runners)
        lines = [
            f"[모의투자] {self.today} 시작(따라잡기 완료): {len(self.runners)}종목, "
            f"보유 {len(held_symbols)}종목, 거래 {len(self.saved)}건",
            "",
            f"■ 자본금: {total_capital:,.0f}원 (종목당 {self.budget:,.0f}원)",
            f"■ 매매 정책: {self.policy.get('strategy', 'orb').upper()} (손절 {self.policy.get('stop_pct')}%, 익절 +{self.policy.get('target_pct')}%)",
        ]

        if held_symbols:
            lines.append(f"\n■ 보유 종목 ({len(held_symbols)}건)")
            for s in held_symbols:
                name = names.get(s, s)
                display_name = f"{name}({s})" if name != s else s
                row = self.status_row(s)
                qty = row["qty"]
                entry_price = row["entry_price"]
                entry_ts = row["entry_ts"]
                last_close = row["last_close"]
                total_bought = entry_price * qty

                time_str = f"{entry_ts:%H:%M}" if entry_ts else ""
                lines.append(f"- {display_name}: {qty:,d}주 @ {entry_price:,.0f}원 (총 {total_bought:,.0f}원, 진입 {time_str})")
                if last_close is not None:
                    eval_price = last_close * (1 - COSTS.slippage)
                    eval_pnl = qty * (eval_price * (1 - COSTS.fee - COSTS.tax) - entry_price * (1 + COSTS.fee))
                    ret_pct = (eval_pnl / total_bought) * 100 if total_bought else Decimal(0)
                    lines.append(f"  현재가 {last_close:,.0f}원 | 평가손익 {eval_pnl:+,.0f}원 ({ret_pct:+.2f}%)")

        waiting_symbols = [s for s in self.runners if self.qty[s] == 0]
        if waiting_symbols:
            header = "■ 대기 종목" if held_symbols else "■ 대상 종목"
            lines.append(f"\n{header} ({len(waiting_symbols)}건)")
            for s in waiting_symbols:
                name = names.get(s, s)
                display_name = f"{name}({s})" if name != s else s
                row = self.status_row(s)
                last_close = row["last_close"]
                if last_close is not None:
                    lines.append(f"- {display_name}: 미보유 (현재가 {last_close:,.0f}원)")
                else:
                    lines.append(f"- {display_name}: 미보유 (대기 중)")

        if self.saved:
            lines.append(f"\n■ 완결 거래 ({len(self.saved)}건)")
            for s, pnl in self.saved:
                name = names.get(s, s)
                display_name = f"{name}({s})" if name != s else s
                lines.append(f"- {display_name}: 손익 {pnl:+,.0f}원")

        return "\n".join(lines)


def run(client, conn, symbols, capital, now=lambda: datetime.now(KST), sleep=_time.sleep, names=None):
    """모의투자 하루 실행: 장 시간 확인 → 따라잡기 → 매분 조회 → 15:31 마감 비교·요약. 종료 코드를 반환한다."""
    today = now().date()
    hours = client.market_hours(today)
    if hours is None:
        log.info("%s 휴장", today)
        return 0
    start, end = hours
    if (start.time(), end.time()) != (REGULAR_OPEN, REGULAR_CLOSE):
        log.warning("%s 정규장 시간 변경일 %s~%s", today, start, end)
        notify.send_mail(f"[모의투자] {today} 정규장 시간 변경일({start:%H:%M}~{end:%H:%M}), 모의투자 안 함")
        return 0

    if names is None:
        symbols_path = ROOT / "paper_symbols.txt"
        fallback = read_symbol_names(symbols_path) if symbols_path.exists() else {}
        names = load_symbol_names(conn, symbols, fallback)

    paper = Paper(conn, symbols, capital, today, names=names)
    for symbol in symbols:
        # 아직 바뀔 수 있는 진행 중 봉을 따라잡기가 가져가지 않도록 FETCH_SECOND초 이전 시각을 쓴다
        paper.poll(client, symbol, now() - timedelta(seconds=FETCH_SECOND), catch_up=True)
    notify.send_mail(paper.start_summary(names))

    while True:
        at = next_fetch_at(now())
        if at.time() >= LOOP_END:
            break
        sleep(max(0, (at - now()).total_seconds()))
        paper.sync_policy()
        for symbol in symbols:
            paper.poll(client, symbol, now())

    loop_end = datetime.combine(today, LOOP_END, KST)
    if now() < loop_end:
        sleep(max(0, (loop_end - now()).total_seconds()))
    notify.send_mail(paper.close(client, now()))
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
        symbols_path = ROOT / "paper_symbols.txt"
        symbols = read_symbols(symbols_path)
        client = TossClient(
            os.environ["TOSS_CLIENT_ID"], os.environ["TOSS_CLIENT_SECRET"],
            os.environ.get("TOSS_BASE_URL") or "https://openapi.tossinvest.com",
            float(os.environ.get("TOSS_RPS") or "15"), ROOT / ".token.json",
        )
        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
            names = load_symbol_names(conn, symbols, read_symbol_names(symbols_path))
            return run(client, conn, symbols, capital, names=names)
    except Exception as e:
        # 예외 문자열에 접속 문자열 등이 섞일 수 있어 종류와 토스 오류 코드만 남긴다
        log.error("실행 실패: %s %s", type(e).__name__, e.code if isinstance(e, TossError) else "")
        notify.send_mail(f"[모의투자] {now.date()} 실행 실패: {type(e).__name__}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
