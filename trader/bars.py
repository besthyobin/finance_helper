"""봉 공용 타입: KST 시간대, 1분봉 Bar, 일봉 DailyBar."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

KST = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class Bar:
    """1분봉 한 개. ts는 KST 봉 시작 시각."""
    ts: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


@dataclass(frozen=True)
class DailyBar:
    """일봉 한 개. date는 KST 거래일."""
    date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
