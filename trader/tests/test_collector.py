import logging
import os
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
import requests

import collector
import notify
import store
from bars import KST, Bar
from toss import TossAuthError, TossError

NOW = datetime(2026, 9, 15, 21, 0, tzinfo=KST)  # 화요일
DAY = date(2026, 9, 14)                          # 월요일
PREV_DAY = date(2026, 9, 11)                     # 금요일


def bars(day, n):
    """day 09:00부터 n개의 1분봉을 만든다."""
    p = Decimal("70000")
    return [Bar(datetime(day.year, day.month, day.day, 9, m, tzinfo=KST), p, p, p, p, 100)
            for m in range(n)]


class FakeClient:
    """TossClient 대역. results[(symbol, day)]가 예외면 던지고, 없으면 빈 목록을 반환한다."""

    def __init__(self, results=None):
        self.results = results or {}
        self.fetched = []

    def get_token(self):
        """토큰 발급을 흉내 낸다."""
        return "TOK"

    def fetch_day(self, symbol, day):
        """요청을 기록하고 준비된 결과를 돌려준다."""
        self.fetched.append((symbol, day))
        result = self.results.get((symbol, day), [])
        if isinstance(result, Exception):
            raise result
        return result


def statuses(conn):
    """collect_runs를 {(symbol, trade_date): (status, bar_count)}로 읽는다."""
    rows = conn.execute("SELECT symbol, trade_date, status, bar_count FROM collect_runs").fetchall()
    return {(s, d): (st, n) for s, d, st, n in rows}


def test_read_symbols_skips_blank_and_comments(tmp_path):
    """빈 줄과 # 주석을 무시하고 종목코드만 읽는다."""
    path = tmp_path / "symbols.txt"
    path.write_text("# 대형주\n005930\n\n000660  # 하이닉스\n", encoding="utf-8")
    assert collector.read_symbols(path) == ["005930", "000660"]


def test_target_days_weekdays_newest_first_within_1461_days():
    """1461일 전부터 어제까지의 평일만 최신순으로 반환하고, 20:10 전이면 오늘은 뺀다."""
    days = collector.target_days(datetime(2026, 9, 15, 12, 0, tzinfo=KST))
    assert days[0] == DAY
    assert days[-1] == date(2022, 9, 15)
    assert date(2022, 9, 14) not in days
    assert all(d.weekday() < 5 for d in days)
    assert days == sorted(days, reverse=True)
    assert len(days) == 1043


def test_target_days_includes_today_from_2010():
    """평일 20:10부터 오늘을 맨 앞에 포함한다."""
    assert collector.target_days(datetime(2026, 9, 15, 20, 9, tzinfo=KST))[0] == DAY
    assert collector.target_days(datetime(2026, 9, 15, 20, 10, tzinfo=KST))[0] == date(2026, 9, 15)


def test_target_days_excludes_weekend_today():
    """주말에 실행하면 20:10 이후라도 오늘은 없고 직전 금요일부터 시작한다."""
    assert collector.target_days(datetime(2026, 9, 12, 21, 0, tzinfo=KST))[0] == PREV_DAY


def test_collect_records_ok_empty_error(conn):
    """봉 있음은 source='toss'로 저장·ok, 없음은 empty, 오류는 error로 기록하고 실패 목록을 돌려준다."""
    client = FakeClient({("A", DAY): bars(DAY, 3),
                         ("B", DAY): TossError("NETWORK", "ConnectionError")})
    counts, failures = collector.collect(conn, client, ["A", "B"], [DAY, PREV_DAY])
    assert counts == {"A": {"ok": 1, "empty": 1, "error": 0, "skipped": 0},
                      "B": {"ok": 0, "empty": 1, "error": 1, "skipped": 0}}
    assert failures == [("B", DAY, "NETWORK ConnectionError")]
    assert statuses(conn) == {("A", DAY): ("ok", 3), ("A", PREV_DAY): ("empty", 0),
                              ("B", DAY): ("error", 0), ("B", PREV_DAY): ("empty", 0)}
    assert conn.execute("SELECT source, symbol, count(*) FROM minute_bars GROUP BY 1, 2").fetchall() \
        == [("toss", "A", 3)]


def test_collect_skips_done_days_and_retries_errors(conn):
    """해당 종목의 ok·empty 날짜는 건너뛰고 error 날짜는 다시 받으며, 남은 날짜를 최신순으로 요청한다."""
    older = date(2026, 9, 10)
    store.record_run(conn, "A", DAY, 720, "ok")
    store.record_run(conn, "A", PREV_DAY, 0, "error", "x")
    store.record_run(conn, "B", older, 0, "empty")
    client = FakeClient({("A", PREV_DAY): bars(PREV_DAY, 2)})
    collector.collect(conn, client, ["A"], [DAY, PREV_DAY, older])
    assert client.fetched == [("A", PREV_DAY), ("A", older)]
    assert statuses(conn)[("A", PREV_DAY)] == ("ok", 2)


def test_collect_skips_symbol_after_5_consecutive_errors(conn):
    """한 종목에서 오류가 5번 연속이면 남은 날짜를 건너뛰고, 중간에 성공이 있으면 횟수를 다시 센다."""
    days = [DAY - timedelta(days=i) for i in range(8)]
    fail = TossError("HTTP500")
    results = {(s, d): fail for s in ("A", "B") for d in days}
    results[("B", days[4])] = bars(days[4], 1)
    client = FakeClient(results)
    counts, failures = collector.collect(conn, client, ["A", "B"], days)
    assert counts["A"] == {"ok": 0, "empty": 0, "error": 5, "skipped": 3}
    assert counts["B"] == {"ok": 1, "empty": 0, "error": 7, "skipped": 0}
    assert [d for s, d in client.fetched if s == "A"] == days[:5]
    assert len(failures) == 12


def test_collect_propagates_auth_error_without_record(conn):
    """TossAuthError는 기록하지 않고 그대로 올려 실행을 멈춘다."""
    client = FakeClient({("A", DAY): TossAuthError("invalid_client")})
    with pytest.raises(TossAuthError):
        collector.collect(conn, client, ["A", "B"], [DAY])
    assert statuses(conn) == {}
    assert client.fetched == [("A", DAY)]


def test_format_alert_lists_up_to_20_failures():
    """실패가 20건을 넘으면 20줄만 쓰고 나머지는 개수로 적는다."""
    failures = [(f"{i:06d}", DAY, "HTTP500") for i in range(25)]
    lines = collector.format_alert(date(2026, 9, 15), failures).splitlines()
    assert lines[0] == "[수집기] 2026-09-15 실패 25건"
    assert lines[1] == "000000 2026-09-14: HTTP500"
    assert len(lines) == 22
    assert lines[-1] == "외 5건"


@pytest.fixture
def main_env(monkeypatch):
    """main이 실제 .env·텔레그램을 쓰지 않도록 막고, 보낸 알림을 담을 목록을 돌려준다."""
    monkeypatch.setattr(collector, "load_dotenv", lambda *args, **kwargs: None)
    for key in ("TOSS_BASE_URL", "TOSS_RPS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TOSS_CLIENT_ID", "CID-X")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "SECRET-X")
    sent = []
    monkeypatch.setattr(collector.notify, "send_telegram", sent.append)
    return sent


def test_main_missing_env_fails_without_secrets(main_env, monkeypatch, caplog):
    """설정이 빠지면 1을 반환하고, 로그·알림에는 빠진 변수 이름과 예외 종류만 남긴다."""
    monkeypatch.delenv("TOSS_CLIENT_ID")
    monkeypatch.setenv("DATABASE_URL", "SECRETPW")
    with caplog.at_level(logging.INFO):
        assert collector.main(NOW) == 1
    assert ".env 누락: TOSS_CLIENT_ID" in caplog.text
    assert "실행 실패: RuntimeError" in caplog.text
    assert "SECRET-X" not in caplog.text and "SECRETPW" not in caplog.text
    assert main_env == ["[수집기] 2026-09-15 실행 실패: RuntimeError"]


def test_main_hides_connection_string_on_db_failure(main_env, monkeypatch, caplog):
    """DB 접속 실패 시 예외 종류만 로그·알림에 남기고 접속 문자열은 남기지 않는다."""
    monkeypatch.setenv("DATABASE_URL", "SECRETPW")
    with caplog.at_level(logging.INFO):
        assert collector.main(NOW) == 1
    assert "실행 실패: ProgrammingError" in caplog.text
    assert "SECRETPW" not in caplog.text
    assert main_env == ["[수집기] 2026-09-15 실행 실패: ProgrammingError"]


def test_main_alerts_only_when_dates_fail(conn, main_env, monkeypatch):
    """토스 기본 설정으로 클라이언트를 만들고, 실패 날짜가 있을 때만 실패 알림을 보낸다."""
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setattr(collector, "target_days", lambda now: [DAY])
    fake = FakeClient({("A", DAY): bars(DAY, 2), ("B", DAY): TossError("HTTP500")})
    created = []
    monkeypatch.setattr(collector, "TossClient", lambda *args: created.append(args[:4]) or fake)

    monkeypatch.setattr(collector, "read_symbols", lambda path: ["A"])
    assert collector.main(NOW) == 0
    assert main_env == []

    monkeypatch.setattr(collector, "read_symbols", lambda path: ["B"])
    assert collector.main(NOW) == 0
    assert main_env == ["[수집기] 2026-09-15 실패 1건\nB 2026-09-14: HTTP500"]
    assert created[0] == ("CID-X", "SECRET-X", "https://openapi.tossinvest.com", 15.0)


def test_send_telegram_swallows_errors_without_leaking_token(monkeypatch, caplog):
    """전송 실패는 예외 없이 로그만 남기고, 로그에 봇 토큰이 없다."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "BOTSECRET")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")

    def fail(url, **kwargs):
        raise requests.ConnectionError(f"failed {url}")

    monkeypatch.setattr(notify.requests, "post", fail)
    with caplog.at_level(logging.ERROR):
        notify.send_telegram("hi")
    assert "텔레그램 전송 실패" in caplog.text
    assert "BOTSECRET" not in caplog.text


def test_send_telegram_posts_message(monkeypatch):
    """설정이 있으면 봇 API로 chat_id와 본문을 보낸다."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "BOT")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    calls = []

    class Resp:
        def raise_for_status(self):
            """성공 응답이라 아무것도 하지 않는다."""

    def post(url, **kwargs):
        calls.append((url, kwargs["json"]))
        return Resp()

    monkeypatch.setattr(notify.requests, "post", post)
    notify.send_telegram("hi")
    assert calls == [("https://api.telegram.org/botBOT/sendMessage", {"chat_id": "42", "text": "hi"})]
