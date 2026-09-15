"""백테스트 전략. 엔진이 종목·날짜마다 인스턴스를 새로 만들고 봉을 시각순으로 하나씩 넘긴다."""
from collections import deque
from datetime import time
from decimal import Decimal


class Strategy:
    """전략 기본형. defaults에 파라미터 기본값을 두고 on_bar에서 신호를 낸다."""
    name = ""
    defaults = {}

    def __init__(self, params):
        """기본값에 전달받은 파라미터를 덮어써 self.p에 둔다."""
        self.p = {**self.defaults, **params}

    def on_bar(self, bar, entry_price):
        """봉 하나를 받아 "buy"/"sell"/None을 반환한다. entry_price는 보유 중 체결 시가, 미보유면 None."""
        raise NotImplementedError


class MaCross(Strategy):
    """단기 종가 이동평균이 장기 이동평균을 상향 돌파하면 매수, 하향 돌파하면 매도."""
    name = "ma_cross"
    defaults = {"short": 5, "long": 20}

    def __init__(self, params):
        """최근 long개 종가와 직전 봉의 (단기, 장기) 평균을 보관한다."""
        super().__init__(params)
        self.closes = deque(maxlen=self.p["long"])
        self.prev = None

    def on_bar(self, bar, entry_price):
        """종가를 누적하고 직전 봉 대비 평균 교차가 일어난 봉에서 신호를 낸다."""
        self.closes.append(bar.close)
        if len(self.closes) < self.p["long"]:
            return None
        closes = list(self.closes)
        cur = (sum(closes[-self.p["short"]:]) / self.p["short"], sum(closes) / self.p["long"])
        prev, self.prev = self.prev, cur
        if prev is None:
            return None
        if prev[0] <= prev[1] and cur[0] > cur[1]:
            return "buy"
        if prev[0] >= prev[1] and cur[0] < cur[1]:
            return "sell"
        return None


class Orb(Strategy):
    """range_end 전 고가를 종가로 돌파하면 하루 1회 매수, 손절·목표 수익률에 닿으면 매도."""
    name = "orb"
    defaults = {"range_end": "09:30", "stop_pct": -1.0, "target_pct": 2.0}

    def __init__(self, params):
        """범위 종료 시각, 손절·목표 비율, 범위 고가, 오늘 매수 신호 여부를 준비한다."""
        super().__init__(params)
        self.range_end = time.fromisoformat(self.p["range_end"])
        self.stop = Decimal(str(self.p["stop_pct"]))
        self.target = Decimal(str(self.p["target_pct"]))
        self.range_high = None
        self.signaled = False

    def on_bar(self, bar, entry_price):
        """범위 시간에는 고가를 누적하고, 이후에는 돌파 매수·손절/목표 매도 신호를 낸다."""
        if bar.ts.time() < self.range_end:
            self.range_high = bar.high if self.range_high is None else max(self.range_high, bar.high)
            return None
        if entry_price is not None:
            change = (bar.close / entry_price - 1) * 100
            return "sell" if change <= self.stop or change >= self.target else None
        if self.range_high is not None and not self.signaled and bar.close > self.range_high:
            self.signaled = True
            return "buy"
        return None


STRATEGIES = {"ma_cross": MaCross, "orb": Orb}
