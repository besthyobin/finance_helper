"""백테스트 체결 규칙: 종목·날짜별 봉 루프, 다음 봉 시가 체결, 강제 청산, 비용 반영."""
from dataclasses import dataclass
from datetime import datetime, time
from decimal import Decimal
from itertools import groupby


@dataclass(frozen=True)
class Costs:
    """거래 비용과 강제 청산 시각. fee·tax·slippage는 비율(0.0005 = 0.05%)."""
    fee: Decimal
    tax: Decimal
    slippage: Decimal
    exit_at: time


@dataclass(frozen=True)
class Trade:
    """완결된 거래 1건. 가격은 슬리피지 반영가, return_pct는 비용 반영 수익률(%)."""
    symbol: str
    entry_ts: datetime
    entry_price: Decimal
    exit_ts: datetime
    exit_price: Decimal
    return_pct: Decimal
    exit_reason: str


def make_trade(symbol, entry_ts, entry_open, exit_ts, exit_base, reason, costs):
    """진입 시가·청산 기준가에 슬리피지를, 수익률에 수수료(양쪽)·매도세를 반영한 Trade를 만든다."""
    buy = entry_open * (1 + costs.slippage)
    sell = exit_base * (1 - costs.slippage)
    ret = (sell * (1 - costs.fee - costs.tax) / (buy * (1 + costs.fee)) - 1) * 100
    return Trade(symbol, entry_ts, buy, exit_ts, sell, ret, reason)


def _close(symbol, entry_bar, exit_ts, exit_base, reason, costs):
    """진입 봉과 청산 기준가로 슬리피지·수수료·세금을 반영한 Trade를 만든다."""
    return make_trade(symbol, entry_bar.ts, entry_bar.open, exit_ts, exit_base, reason, costs)


class DayRunner:
    """한 종목 하루의 체결 규칙을 봉 하나씩 적용한다. run_day와 paper.py가 함께 쓴다."""

    def __init__(self, symbol, strategy, costs):
        """종목·전략 인스턴스·비용을 받고 미보유, 대기 신호 없음 상태로 시작한다."""
        self.symbol = symbol
        self.strategy = strategy
        self.costs = costs
        self.holding = None   # 보유 중이면 체결 봉
        self.pending = None   # 이번 봉 시가에 체결할 신호
        self.last_bar = None
        self.done = False

    def step(self, bar):
        """봉 하나를 처리하고 이 봉에서 일어난 체결 이벤트 목록을 반환한다. ("buy", 체결 봉) 또는 ("sell", Trade)."""
        self.last_bar = bar
        if self.done:
            return []
        c = self.costs
        if bar.ts.time() >= c.exit_at:
            self.done = True
            if self.holding:
                trade = _close(self.symbol, self.holding, bar.ts, bar.open, "close_time", c)
                self.holding = None
                return [("sell", trade)]
            return []
        events = []
        if self.pending == "buy":
            self.holding = bar
            events.append(("buy", bar))
        elif self.pending == "sell":
            events.append(("sell", _close(self.symbol, self.holding, bar.ts, bar.open, "signal", c)))
            self.holding = None
        signal = self.strategy.on_bar(bar, self.holding.open if self.holding else None)
        # 미보유 중 buy, 보유 중 sell만 다음 봉에 체결한다
        if (signal == "buy" and self.holding is None) or (signal == "sell" and self.holding is not None):
            self.pending = signal
        else:
            self.pending = None
        return events

    def finish(self):
        """데이터가 끝났을 때 보유 중이면 마지막 봉 종가로 day_end 청산 Trade를, 아니면 None을 반환한다."""
        if not self.holding:
            return None
        last = self.last_bar
        trade = _close(self.symbol, self.holding, last.ts, last.close, "day_end", self.costs)
        self.holding = None
        self.done = True
        return trade


def run_day(symbol, strategy, bars, costs):
    """한 종목 하루 봉(시각 오름차순)을 전략에 넘기고 체결 규칙에 따라 거래 목록을 만든다."""
    runner = DayRunner(symbol, strategy, costs)
    trades = []
    for bar in bars:
        trades += [item for kind, item in runner.step(bar) if kind == "sell"]
    last = runner.finish()
    return trades + [last] if last else trades


def run(strategy_cls, params, bars_by_symbol, costs):
    """종목별·KST 날짜별로 새 전략 인스턴스를 만들어 run_day를 돌리고 전체 거래를 반환한다."""
    trades = []
    for symbol, bars in bars_by_symbol.items():
        for _, day in groupby(bars, key=lambda b: b.ts.date()):
            trades += run_day(symbol, strategy_cls(params), list(day), costs)
    return trades
