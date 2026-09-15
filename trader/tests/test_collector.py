import logging
from datetime import date, datetime
from decimal import Decimal

import requests

import collector
import notify
import store
from kis import KST, Bar, KisError

TODAY = date(2026, 9, 14)  # 월요일


def bars(n):
    """09:00부터 n개의 1분봉을 만든다."""
    p = Decimal("70000")
    return [Bar(datetime(2026, 9, 14, 9, m, tzinfo=KST), p, p, p, p, 100) for m in range(n)]


class FakeClient:
    """KisClient 대역. results[symbol]이 예외면 던지고, 아니면 봉 목록을 반환한다."""

    def __init__(self, results):
        self.results = results
        self.fetched = []

    def fetch_minute_bars(self, symbol, today):
        """요청 종목을 기록하고 준비된 결과를 돌려준다."""
        self.fetched.append(symbol)
        result = self.results[symbol]
        if isinstance(result, Exception):
            raise result
        return result


def statuses(conn):
    """collect_runs를 {symbol: (status, bar_count)}로 읽는다."""
    rows = conn.execute("SELECT symbol, status, bar_count FROM collect_runs").fetchall()
    return {s: (st, n) for s, st, n in rows}


def test_should_run_only_weekdays_after_1535():
    """평일 15:35 이후에만 수집하고, 주말·장 마감 직후·자정 이후 새벽은 건너뛴다."""
    assert not collector.should_run(datetime(2026, 9, 12, 16, 0, tzinfo=KST))  # 토요일
    assert not collector.should_run(datetime(2026, 9, 14, 15, 34, tzinfo=KST))
    assert not collector.should_run(datetime(2026, 9, 15, 0, 10, tzinfo=KST))
    assert collector.should_run(datetime(2026, 9, 14, 15, 35, tzinfo=KST))


def test_read_symbols_skips_blank_and_comments(tmp_path):
    """빈 줄과 # 주석을 무시하고 종목코드만 읽는다."""
    path = tmp_path / "symbols.txt"
    path.write_text("# 대형주\n005930\n\n000660  # 하이닉스\n", encoding="utf-8")
    assert collector.read_symbols(path) == ["005930", "000660"]


def test_collect_records_ok_empty_error(conn):
    """성공은 저장·ok, 빈 응답은 empty, 실패는 봉 없이 error로 기록하고 다음 종목을 계속한다."""
    client = FakeClient({"A": KisError("EGW00201", "초과"), "B": bars(3), "C": []})
    counts = collector.collect(conn, client, ["A", "B", "C"], TODAY)
    assert counts == {"ok": 1, "empty": 1, "error": 1, "skipped": 0}
    assert statuses(conn) == {"A": ("error", 0), "B": ("ok", 3), "C": ("empty", 0)}
    assert conn.execute("SELECT symbol, count(*) FROM minute_bars GROUP BY symbol").fetchall() == [("B", 3)]
    assert store.failed_runs(conn, TODAY) == [("A", "EGW00201 초과")]


def test_collect_skips_done_and_retries_errors(conn):
    """ok·empty 종목은 건너뛰고 error 종목은 다시 수집한다."""
    store.record_run(conn, "A", TODAY, 3, "ok")
    store.record_run(conn, "B", TODAY, 0, "empty")
    store.record_run(conn, "C", TODAY, 0, "error", "x")
    client = FakeClient({"C": bars(2)})
    counts = collector.collect(conn, client, ["A", "B", "C"], TODAY)
    assert client.fetched == ["C"]
    assert counts == {"ok": 1, "empty": 0, "error": 0, "skipped": 2}
    assert statuses(conn)["C"] == ("ok", 2)


def test_alert_only_after_23_with_failures(conn):
    """23:00 이후 실행이고 실패 종목이 있을 때만 알림을 보낸다."""
    sent = []
    store.record_run(conn, "A", TODAY, 3, "ok")
    at_23 = datetime(2026, 9, 14, 23, 0, tzinfo=KST)
    assert not collector.alert_failures(conn, at_23, sent.append)

    store.record_run(conn, "B", TODAY, 0, "error", "NETWORK ConnectionError")
    assert not collector.alert_failures(conn, datetime(2026, 9, 14, 22, 59, tzinfo=KST), sent.append)
    assert sent == []

    assert collector.alert_failures(conn, at_23, sent.append)
    assert sent == ["[수집기] 2026-09-14 실패 1종목\nB: NETWORK ConnectionError"]


def test_format_alert_truncates_after_20_lines():
    """실패 종목이 20개를 넘으면 20줄만 쓰고 나머지는 개수로 적는다."""
    failed = [(f"{i:06d}", "오류") for i in range(25)]
    lines = collector.format_alert(TODAY, failed).splitlines()
    assert lines[0] == "[수집기] 2026-09-14 실패 25종목"
    assert len(lines) == 22
    assert lines[-1] == "외 5종목"


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
