"""일봉 스윙 백테스트: 신호 다음 날 시가에 체결하는 엔진과 신고가 돌파·추세 전략."""
from collections import deque
from datetime import datetime, time

from bars import KST
from engine import make_trade
from strategies import Strategy

OPEN_TIME = time(9, 0)
CLOSE_TIME = time(15, 30)


class BreakoutTrend(Strategy):
    """종가가 직전 entry_days일 최고 종가를 넘고 trend_days일 평균 위면 매수, 직전 exit_days일 최저 종가 아래면 매도."""
    name = "breakout_trend"
    defaults = {"entry_days": 20, "trend_days": 50, "exit_days": 10}

    def __init__(self, params):
        """일수 파라미터를 검증하고 필요한 만큼의 최근 종가를 보관할 준비를 한다."""
        super().__init__(params)
        e, t, x = self.p["entry_days"], self.p["trend_days"], self.p["exit_days"]
        if min(e, t, x) <= 0:
            raise ValueError("breakout_trend는 entry_days·trend_days·exit_days가 모두 0보다 커야 함")
        self.closes = deque(maxlen=max(e, t, x) + 1)

    def on_bar(self, bar, entry_price):
        """오늘 종가를 쌓고, 미보유면 돌파·추세로 매수, 보유면 최저 이탈로 매도 신호를 낸다. 종가가 모자라면 None."""
        self.closes.append(bar.close)
        closes = list(self.closes)
        today, past = closes[-1], closes[:-1]
        if entry_price is None:
            e, t = self.p["entry_days"], self.p["trend_days"]
            if len(past) < e or len(closes) < t:
                return None
            trend = sum(closes[-t:]) / t
            return "buy" if today > max(past[-e:]) and today > trend else None
        x = self.p["exit_days"]
        if len(past) < x:
            return None
        return "sell" if today < min(past[-x:]) else None


def _at(day, at_time):
    """거래일과 시각을 KST datetime으로 합친다."""
    return datetime.combine(day, at_time, KST)


def run_symbol(symbol, bars, strategy, costs):
    """일봉(날짜 오름차순)을 하루씩 전략에 넣어, 신호 다음 날 시가에 체결한 거래 목록을 반환한다.
    데이터가 끝날 때 보유 중이면 마지막 종가로 청산한다."""
    trades = []
    entry = None     # 보유 중이면 진입한 날의 일봉
    pending = None   # 오늘 시가에 체결할 전날 신호
    for bar in bars:
        if pending == "buy" and entry is None:
            entry = bar
        elif pending == "sell" and entry is not None:
            trades.append(make_trade(symbol, _at(entry.date, OPEN_TIME), entry.open,
                                     _at(bar.date, OPEN_TIME), bar.open, "signal", costs))
            entry = None
        pending = strategy.on_bar(bar, entry.open if entry else None)
    if entry is not None:
        last = bars[-1]
        trades.append(make_trade(symbol, _at(entry.date, OPEN_TIME), entry.open,
                                 _at(last.date, CLOSE_TIME), last.close, "day_end", costs))
    return trades
