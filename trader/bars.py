"""1분봉 공용 타입: KST 시간대와 Bar."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
