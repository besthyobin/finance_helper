"""Yahoo Finance 1분봉을 minute_bars에 source='yahoo'로 적재하는 일회성 도구."""
import os
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import psycopg
import requests
from dotenv import load_dotenv

import store
from collector import read_symbols
from bars import KST, Bar

ROOT = Path(__file__).resolve().parent
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{}"


class YahooError(Exception):
    """Yahoo 응답이 오류이거나 형식이 다름."""


def fetch_chart(ticker, send=requests.get):
    """Yahoo 차트 API에서 최근 8일 1분봉 응답의 result 객체를 받아온다."""
    resp = send(CHART_URL.format(ticker), params={"interval": "1m", "range": "8d"},
                headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
    try:
        chart = resp.json()["chart"]
    except (ValueError, KeyError, TypeError):
        raise YahooError(f"HTTP{resp.status_code}") from None
    if chart.get("error") or not chart.get("result"):
        raise YahooError(f"{ticker} 데이터 없음")
    return chart["result"][0]


def fetch_symbol(code, send=requests.get):
    """코스피(.KS)로 받아보고 실패하면 코스닥(.KQ)으로 한 번 더 시도해 (티커, result)를 반환한다."""
    try:
        return f"{code}.KS", fetch_chart(f"{code}.KS", send)
    except YahooError:
        return f"{code}.KQ", fetch_chart(f"{code}.KQ", send)


def to_bars(result, today):
    """차트 result를 KST Bar 목록으로 바꾼다. OHLC가 빈 봉과 오늘 날짜 봉은 뺀다."""
    quote = result["indicators"]["quote"][0]
    bars = []
    for i, epoch in enumerate(result.get("timestamp") or []):
        ts = datetime.fromtimestamp(epoch, KST)
        ohlc = [quote[k][i] for k in ("open", "high", "low", "close")]
        if ts.date() >= today or None in ohlc:
            continue
        bars.append(Bar(ts, *(Decimal(str(x)) for x in ohlc), int(quote["volume"][i] or 0)))
    return bars


def main(argv=None, send=requests.get, today=None):
    """종목별로 Yahoo 1분봉을 받아 저장하고 결과를 출력한다. 실패 종목이 있으면 1을 반환한다."""
    load_dotenv(ROOT / ".env")
    argv = sys.argv[1:] if argv is None else argv
    codes = argv or read_symbols(ROOT / "symbols.txt")
    today = today or datetime.now(KST).date()
    failed = 0
    try:
        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
            for code in codes:
                try:
                    ticker, result = fetch_symbol(code, send)
                    bars = to_bars(result, today)
                    store.save_bars(conn, code, bars, source="yahoo")
                    print(f"{code} yahoo({ticker[-3:]}) {len(bars)}봉")
                except Exception as e:
                    failed += 1
                    print(f"{code} 실패: {type(e).__name__}")
    except Exception as e:
        print(f"실행 실패: {type(e).__name__}")
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
