import json
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import load_yahoo
from kis import KST, Bar

TODAY = date(2026, 9, 15)
CHART = json.loads((Path(__file__).parent / "fixtures" / "yahoo_chart.json").read_text(encoding="utf-8"))
NOT_FOUND = {"chart": {"result": None,
                       "error": {"code": "Not Found", "description": "No data found, symbol may be delisted"}}}


class FakeResponse:
    """requests.Response 대역: status_code와 json()만 흉내 낸다."""

    def __init__(self, body, status=200):
        """돌려줄 JSON 본문과 상태 코드를 저장한다."""
        self.status_code = status
        self._body = body

    def json(self):
        """준비된 본문을 돌려준다."""
        return self._body


def fake_yahoo(ok_tickers):
    """ok_tickers에 든 티커만 샘플 차트를, 나머지는 Not Found를 돌려주는 send 대역을 만든다."""
    calls = []

    def send(url, **kwargs):
        """URL 끝 티커가 ok_tickers에 있으면 샘플 응답을, 없으면 404 Not Found를 돌려준다."""
        ticker = url.rsplit("/", 1)[1]
        calls.append(ticker)
        assert kwargs["params"] == {"interval": "1m", "range": "8d"}
        return FakeResponse(CHART) if ticker in ok_tickers else FakeResponse(NOT_FOUND, 404)

    send.calls = calls
    return send


def test_to_bars_converts_and_skips_null_and_today():
    """UTC epoch를 KST 봉으로 바꾸고, OHLC가 빈 봉과 오늘 봉은 뺀다. 거래량 null은 0."""
    bars = load_yahoo.to_bars(CHART["chart"]["result"][0], TODAY)
    assert bars == [
        Bar(datetime(2026, 9, 11, 9, 0, tzinfo=KST), Decimal("271000"), Decimal("271500"),
            Decimal("270500"), Decimal("271500"), 15234),
        Bar(datetime(2026, 9, 11, 9, 2, tzinfo=KST), Decimal("271500"), Decimal("272000"),
            Decimal("271500"), Decimal("271500"), 0),
    ]


def test_fetch_symbol_falls_back_to_kosdaq():
    """.KS가 Not Found면 .KQ로 다시 요청한다."""
    send = fake_yahoo({"035720.KQ"})
    ticker, result = load_yahoo.fetch_symbol("035720", send)
    assert ticker == "035720.KQ"
    assert send.calls == ["035720.KS", "035720.KQ"]
    assert result["timestamp"][0] == 1789084800


def test_main_saves_yahoo_source_and_continues_after_failure(conn, monkeypatch, capsys):
    """실패 종목은 출력만 하고 다음 종목을 저장하며, 실패가 있으면 1을 반환한다."""
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    code = load_yahoo.main(["999999", "005930"], send=fake_yahoo({"005930.KS"}), today=TODAY)
    assert code == 1
    assert conn.execute("SELECT source, symbol, count(*) FROM minute_bars GROUP BY 1, 2").fetchall() == [
        ("yahoo", "005930", 2)]
    out = capsys.readouterr().out
    assert "999999 실패: YahooError" in out
    assert "005930 yahoo(.KS) 2봉" in out


def test_main_returns_0_when_all_succeed(conn, monkeypatch):
    """전 종목 성공이면 0을 반환한다."""
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    assert load_yahoo.main(["005930"], send=fake_yahoo({"005930.KS"}), today=TODAY) == 0


def test_main_hides_connection_string_on_db_failure(monkeypatch, capsys):
    """DB 접속 실패 시 예외 종류만 출력하고 접속 문자열(비밀번호)은 출력하지 않는다."""
    monkeypatch.setenv("DATABASE_URL", "SECRETPW")
    assert load_yahoo.main(["005930"], send=fake_yahoo({"005930.KS"}), today=TODAY) == 1
    out = capsys.readouterr().out
    assert "실행 실패: ProgrammingError" in out
    assert "SECRETPW" not in out
