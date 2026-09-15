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


def _close(symbol, entry_bar, exit_ts, exit_base, reason, costs):
    """진입 봉과 청산 기준가로 슬리피지·수수료·세금을 반영한 Trade를 만든다."""
    buy = entry_bar.open * (1 + costs.slippage)
    sell = exit_base * (1 - costs.slippage)
    ret = (sell * (1 - costs.fee - costs.tax) / (buy * (1 + costs.fee)) - 1) * 100
    return Trade(symbol, entry_bar.ts, buy, exit_ts, sell, ret, reason)


def run_day(symbol, strategy, bars, costs):
    """한 종목 하루 봉(시각 오름차순)을 전략에 넘기고 체결 규칙에 따라 거래 목록을 만든다."""
    trades = []
    entry = None     # 보유 중이면 체결 봉
    pending = None   # 이번 봉 시가에 체결할 신호
    for bar in bars:
        if bar.ts.time() >= costs.exit_at:
            if entry:
                trades.append(_close(symbol, entry, bar.ts, bar.open, "close_time", costs))
            return trades
        if pending == "buy":
            entry = bar
        elif pending == "sell":
            trades.append(_close(symbol, entry, bar.ts, bar.open, "signal", costs))
            entry = None
        signal = strategy.on_bar(bar, entry.open if entry else None)
        # 미보유 중 buy, 보유 중 sell만 다음 봉에 체결한다
        if (signal == "buy" and entry is None) or (signal == "sell" and entry is not None):
            pending = signal
        else:
            pending = None
    if entry:
        last = bars[-1]
        trades.append(_close(symbol, entry, last.ts, last.close, "day_end", costs))
    return trades


def run(strategy_cls, params, bars_by_symbol, costs):
    """종목별·KST 날짜별로 새 전략 인스턴스를 만들어 run_day를 돌리고 전체 거래를 반환한다."""
    trades = []
    for symbol, bars in bars_by_symbol.items():
        for _, day in groupby(bars, key=lambda b: b.ts.date()):
            trades += run_day(symbol, strategy_cls(params), list(day), costs)
    return trades
