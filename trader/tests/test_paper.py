import logging
import os
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest

import engine
import paper
from bars import KST, Bar
from toss import TossAuthError, TossError

TODAY = date(2026, 9, 17)


def at(hh_mm, second=0):
    """오늘 HH:MM:SS KST 시각."""
    h, m = map(int, hh_mm.split(":"))
    return datetime(2026, 9, 17, h, m, second, tzinfo=KST)


REGULAR = (at("09:00"), at("15:30"))


def day_bars(price_at):
    """08:50~15:40 시작 1분봉 411개. price_at(time)이 시가=고가=저가=종가."""
    out = []
    for i in range(411):
        ts = at("08:50") + timedelta(minutes=i)
        p = Decimal(price_at(ts.time()))
        out.append(Bar(ts, p, p, p, p, 100))
    return out


def breakout(t):
    """09:40에 범위 고가 100을 101로 돌파(09:41 체결)하고, 10:00에 104로 +2% 목표에 닿는다(10:01 청산)."""
    if t < time(9, 40):
        return "100"
    return "101" if t < time(10, 0) else "104"


def flat(t):
    """하루 종일 100."""
    return "100"


class Clock:
    """가짜 시계. sleep한 만큼 시간이 흐른다."""

    def __init__(self, start):
        """시작 시각을 받는다."""
        self.t = start

    def now(self):
        """현재 가짜 시각을 반환한다."""
        return self.t

    def sleep(self, seconds):
        """음수 대기가 없는지 확인하고 시간을 흘린다."""
        assert seconds >= 0
        self.t += timedelta(seconds=seconds)


class FakeClient:
    """TossClient 대역. fetch_recent는 끝난 봉만, fetch_day는 진행 중인 봉까지 준다."""

    def __init__(self, clock, days, hours=REGULAR, fail=None, final=None):
        """시계, {종목: 봉}, 장 시간(또는 예외), 실패 규칙 fail(kind, symbol, now), 15:31 이후 fetch_day용 봉을 받는다."""
        self.clock = clock
        self.days = days
        self.hours = hours
        self.fail = fail or (lambda kind, symbol, now: None)
        self.final = final
        self.calls = []

    def _call(self, kind, symbol):
        """호출을 기록하고, 실패 규칙이 예외를 주면 던진다. 현재 시각을 반환한다."""
        now = self.clock.now()
        self.calls.append((kind, symbol))
        error = self.fail(kind, symbol, now)
        if error:
            raise error
        return now

    def market_hours(self, day):
        """준비된 장 시간을 반환하거나 예외를 던진다."""
        if isinstance(self.hours, Exception):
            raise self.hours
        return self.hours

    def fetch_recent(self, symbol, count):
        """지금 이전에 끝난 봉 중 최근 count개를 반환한다."""
        now = self._call("recent", symbol)
        return [b for b in self.days[symbol] if b.ts + timedelta(minutes=1) <= now][-count:]

    def fetch_day(self, symbol, day):
        """지금까지 시작한 봉 전부(진행 중인 봉 포함)를 반환한다. 15:31 이후 final이 있으면 그 봉을 쓴다."""
        now = self._call("day", symbol)
        days = self.final if self.final and now.time() >= time(15, 31) else self.days
        return [b for b in days[symbol] if b.ts <= now]

    def count(self, kind, symbol):
        """종류·종목별 호출 수를 반환한다."""
        return self.calls.count((kind, symbol))


@pytest.fixture
def sent(monkeypatch):
    """텔레그램 전송 대신 메시지를 목록에 기록한다."""
    messages = []
    monkeypatch.setattr(paper.notify, "send_telegram", messages.append)
    return messages


def run_from(conn, start, days, capital="1000000", **client_kwargs):
    """start부터 가짜 시계로 paper.run을 돌려 (종료 코드, 클라이언트, 끝난 시각)을 반환한다."""
    clock = Clock(start)
    client = FakeClient(clock, days, **client_kwargs)
    code = paper.run(client, conn, list(days), Decimal(capital), now=clock.now, sleep=clock.sleep)
    return code, client, clock.now()


def saved_trades(conn):
    """paper_trades를 진입 시각 순으로 조회한다."""
    return conn.execute(
        "SELECT symbol, qty, entry_ts, entry_price, exit_ts, exit_price, pnl_krw, exit_reason "
        "FROM paper_trades ORDER BY entry_ts"
    ).fetchall()


SUMMARY_MATCH = ("[모의투자] 2026-09-17 요약\n거래 1건 (승 1 / 패 0) 손익 +13,156원\n"
                 "A 1건 +13,156원\n마감 비교: 일치")


def test_pnl_krw_applies_fee_and_tax():
    """원화 손익 = 수량 × (매도가 × (1 − 수수료 − 세금) − 매수가 × (1 + 수수료))."""
    costs = engine.Costs(Decimal("0.001"), Decimal("0.002"), Decimal("0"), time(15, 15))
    trade = engine.Trade("A", at("09:00"), Decimal("10000"), at("10:00"), Decimal("10100"), Decimal("0"), "signal")
    assert paper.pnl_krw(10, trade, costs) == Decimal("597.000")


def test_regular_keeps_finished_regular_session_bars():
    """09:00~15:29 시작 봉 중 now 이전에 끝난 봉만 남긴다."""
    bars = [b for b in day_bars(flat) if b.ts.time() in (time(8, 59), time(9, 0), time(15, 29), time(15, 30))]
    assert [b.ts.time() for b in paper.regular(bars, at("15:30"))] == [time(9, 0), time(15, 29)]
    assert [b.ts.time() for b in paper.regular(bars, at("15:29", 59))] == [time(9, 0)]


@pytest.mark.parametrize("now, expected", [
    (at("09:00", 10), at("09:00", 15)),
    (at("09:00", 15), at("09:01", 15)),
    (at("09:00", 59), at("09:01", 15)),
])
def test_next_fetch_at_is_next_15_seconds(now, expected):
    """다음 조회 시각은 now보다 늦은 가장 가까운 매분 15초다."""
    assert paper.next_fetch_at(now) == expected


def test_holiday_returns_0_without_fetching(conn, sent):
    """휴장일이면 봉을 조회하지 않고 알림 없이 0을 반환한다."""
    code, client, _ = run_from(conn, at("08:55"), {"A": day_bars(breakout)}, hours=None)
    assert code == 0 and client.calls == [] and sent == []


def test_irregular_hours_alerts_and_skips(conn, sent):
    """정규장이 09:00~15:30이 아니면 알리고 거래 없이 0을 반환한다."""
    code, client, _ = run_from(conn, at("08:55"), {"A": day_bars(breakout)}, hours=(at("10:00"), at("16:30")))
    assert code == 0 and client.calls == []
    assert sent == ["[모의투자] 2026-09-17 정규장 시간 변경일(10:00~16:30), 모의투자 안 함"]


def test_full_day_saves_trade_status_and_matches_backtest(conn, sent):
    """08:55부터 돌면 백테스트와 같은 거래를 저장·알리고, 15:31에 비교 일치 요약을 보낸다."""
    code, client, end = run_from(conn, at("08:55"), {"A": day_bars(breakout), "B": day_bars(flat)})
    assert code == 0 and end == at("15:31")
    assert saved_trades(conn) == [("A", 4948, at("09:41"), Decimal("101.0505"), at("10:01"),
                                   Decimal("103.9480"), Decimal("13156.010705300"), "signal")]
    assert sent == [
        "[모의투자] 2026-09-17 시작(따라잡기 완료): 2종목, 보유 0종목, 거래 0건",
        "[모의투자] 매수 A 4948주 @ 101원 (09:41)",
        "[모의투자] 매도 A 4948주 @ 104원 (10:01) 손익 +13,156원 (+2.63%)",
        SUMMARY_MATCH,
    ]
    status = conn.execute("SELECT symbol, last_bar_ts, last_close, qty, entry_ts FROM paper_status ORDER BY symbol").fetchall()
    assert status == [("A", at("15:29"), Decimal("104"), 0, None), ("B", at("15:29"), Decimal("100"), 0, None)]
    assert client.count("day", "A") == 2  # 시작 따라잡기 + 마감 비교, 빈 분 없음


def test_status_shows_holding_during_trade(conn, sent):
    """보유 중에 끝나면(09:50 시작 → 따라잡기 직후) 상태에 수량·진입 정보가 남는다."""
    clock = Clock(at("09:50"))
    client = FakeClient(clock, {"A": day_bars(breakout)})
    p = paper.Paper(conn, ["A"], Decimal("500000"), TODAY)
    p.poll(client, "A", clock.now(), catch_up=True)
    [row] = conn.execute("SELECT qty, entry_ts, entry_price, last_bar_ts FROM paper_status").fetchall()
    assert row == (4948, at("09:41"), Decimal("101.0505"), at("09:49"))
    assert sent == []


def test_restart_catches_up_without_duplicates_or_trade_alerts(conn, sent):
    """10:30에 두 번 시작해도 거래는 1건이고, 따라잡기 중 체결은 알리지 않는다."""
    for _ in range(2):
        code, _, _ = run_from(conn, at("10:30"), {"A": day_bars(breakout), "B": day_bars(flat)})
        assert code == 0
    assert len(saved_trades(conn)) == 1
    assert not any("매수" in m or "매도" in m for m in sent)
    assert sent[0] == "[모의투자] 2026-09-17 시작(따라잡기 완료): 2종목, 보유 0종목, 거래 1건"
    assert sent[1] == SUMMARY_MATCH


def test_zero_quantity_does_not_save_trade(conn, sent):
    """종목당 금액으로 1주도 못 사면 거래를 저장·알리지 않고, 마감 비교는 일치한다."""
    code, _, _ = run_from(conn, at("08:55"), {"A": day_bars(breakout)}, capital="100")
    assert code == 0 and saved_trades(conn) == []
    assert sent[-1] == "[모의투자] 2026-09-17 요약\n거래 0건 (승 0 / 패 0) 손익 +0원\n마감 비교: 일치"


def test_fetch_failures_alert_once_and_gap_is_filled(conn, sent):
    """09:38~09:44 조회가 7분 실패하면 5번째에 한 번 알리고, 복구 후 빈 분을 하루치 조회로 채워 같은 거래를 낸다."""
    def fail(kind, symbol, now):
        """A의 최근 봉 조회만 09:38~09:44에 실패시킨다."""
        if kind == "recent" and symbol == "A" and time(9, 38) <= now.time() < time(9, 45):
            return TossError("HTTP500")
        return None

    code, client, _ = run_from(conn, at("08:55"), {"A": day_bars(breakout)}, capital="500000", fail=fail)
    assert code == 0
    assert [m for m in sent if "실패" in m] == ["[모의투자] A 연속 5분 실패: TossError"]
    assert client.count("day", "A") == 3  # 시작 따라잡기 + 빈 분 + 마감 비교
    assert len(saved_trades(conn)) == 1
    assert sent[-1] == SUMMARY_MATCH


def test_close_reports_mismatch_when_final_bars_differ(conn, sent):
    """마감 때 받은 봉이 실시간과 달라 거래가 다르면 요약에 불일치를 적는다."""
    code, _, _ = run_from(conn, at("08:55"), {"A": day_bars(breakout)}, final={"A": day_bars(flat)})
    assert code == 0
    assert sent[-1].endswith("마감 비교: A 불일치: 실시간 1건 / 마감 0건")


def test_auth_error_propagates(conn, sent):
    """인증 오류는 run 밖으로 올린다."""
    with pytest.raises(TossAuthError):
        run_from(conn, at("08:55"), {"A": day_bars(breakout)}, hours=TossAuthError("access_denied"))


def test_db_failure_while_processing_alerts_immediately(conn, sent, monkeypatch):
    """DB 저장처럼 토스 오류가 아닌 예외는 연속 실패를 세지 않고 그 자리에서 바로 알린다."""
    def fail_save(conn, strategy, qty, trade, pnl_krw):
        """paper_trades 저장을 항상 실패시킨다."""
        raise RuntimeError("db down")

    monkeypatch.setattr(paper.store, "save_paper_trade", fail_save)
    code, _, _ = run_from(conn, at("08:55"), {"A": day_bars(breakout)}, capital="500000")
    assert code == 0
    assert sent.count("[모의투자] A 처리 실패: RuntimeError, DB·코드 확인 후 재시작 필요") == 1
    assert not any("연속 5분 실패" in m for m in sent)


def test_db_failure_alerts_once_until_recovery(conn, sent, monkeypatch):
    """상태 저장이 매분 계속 실패해도 알림은 실패가 시작될 때 1번만 보낸다."""
    def fail_save(conn, row):
        """paper_status 저장을 항상 실패시킨다."""
        raise RuntimeError("db down")

    monkeypatch.setattr(paper.store, "save_paper_status", fail_save)
    code, _, _ = run_from(conn, at("08:55"), {"A": day_bars(breakout)}, capital="500000")
    assert code == 0
    assert sent.count("[모의투자] A 처리 실패: RuntimeError, DB·코드 확인 후 재시작 필요") == 1


@pytest.fixture
def main_env(monkeypatch, sent):
    """main 실행 환경: .env 읽기를 막고 필수 값과 종목을 채운 뒤 알림 목록을 반환한다."""
    monkeypatch.setattr(paper, "load_dotenv", lambda *args, **kwargs: None)
    for key in paper.REQUIRED_ENV:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TOSS_CLIENT_ID", "CID-X")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "SECRET-X")
    monkeypatch.setenv("PAPER_CAPITAL", "2000000")
    monkeypatch.setattr(paper, "read_symbols", lambda path: ["A"])
    return sent


def test_main_missing_env_fails_without_secrets(main_env, monkeypatch, caplog):
    """설정이 빠지면 1을 반환하고 로그·알림에는 변수 이름과 예외 종류만 남긴다."""
    monkeypatch.delenv("PAPER_CAPITAL")
    monkeypatch.setenv("DATABASE_URL", "SECRETPW")
    with caplog.at_level(logging.INFO):
        assert paper.main(at("08:55")) == 1
    assert ".env 누락: PAPER_CAPITAL" in caplog.text
    assert "SECRET-X" not in caplog.text and "SECRETPW" not in caplog.text
    assert main_env == ["[모의투자] 2026-09-17 실행 실패: RuntimeError"]


def test_main_auth_error_returns_1(conn, main_env, monkeypatch):
    """인증 오류면 실행 실패 알림을 보내고 1을 반환하며, 토스 기본 설정으로 클라이언트를 만든다."""
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    created = []
    fake = FakeClient(Clock(at("08:55")), {"A": []}, hours=TossAuthError("access_denied"))
    monkeypatch.setattr(paper, "TossClient", lambda *args: created.append(args[:4]) or fake)
    assert paper.main(at("08:55")) == 1
    assert main_env == ["[모의투자] 2026-09-17 실행 실패: TossAuthError"]
    assert created == [("CID-X", "SECRET-X", "https://openapi.tossinvest.com", 15.0)]
