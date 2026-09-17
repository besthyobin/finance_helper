import logging
import os
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest

import engine
import selection
import selector
import store
from bars import KST, Bar, DailyBar
from toss import TossAuthError, TossError

TODAY = date(2026, 9, 18)  # 금요일
START = datetime(2026, 9, 18, 7, 30, tzinfo=KST)


class Clock:
    """가짜 시계."""

    def __init__(self, start):
        """시작 시각을 받는다."""
        self.t = start

    def now(self):
        """현재 가짜 시각."""
        return self.t


def daily(n=80):
    """TODAY 포함 n개 일봉. 어제까지는 50,000/50,500 번갈아, 오늘 봉은 999,999(제외 확인용)."""
    first = TODAY - timedelta(days=n - 1)
    out = []
    for i in range(n):
        day = first + timedelta(days=i)
        p = Decimal("999999") if day == TODAY else Decimal("50000" if i % 2 == 0 else "50500")
        out.append(DailyBar(day, p, p, p, p, 1_000_000))
    return out


class FakeClient:
    """TossClient 대역. specs[symbol]로 순위·종목·위험 데이터를 만들고, 호출마다 시계를 step만큼 흘린다."""

    def __init__(self, clock, specs, holiday=False, fail=None, step=timedelta(0)):
        """시계, 종목별 설정, 휴장 여부, 실패 규칙 fail(method, symbol), 호출당 흐를 시간을 받는다."""
        self.clock, self.specs, self.holiday, self.step = clock, specs, holiday, step
        self.fail = fail or (lambda method, symbol: None)
        self.calls = []

    def _call(self, method, symbol=None):
        """호출을 기록하고 시간을 흘리며, 실패 규칙이 예외를 주면 던진다."""
        self.calls.append((method, symbol))
        self.clock.t += self.step
        error = self.fail(method, symbol)
        if error:
            raise error

    def market_hours(self, day):
        """휴장이면 None, 아니면 정규장 시간."""
        self._call("market_hours")
        return None if self.holiday else (datetime.combine(day, time(9), KST), datetime.combine(day, time(15, 30), KST))

    def rankings(self):
        """specs 순서가 순위다."""
        self._call("rankings")
        return [{"rank": i + 1, "symbol": s, "last_price": Decimal(spec.get("price", "50000"))}
                for i, (s, spec) in enumerate(self.specs.items())]

    def stocks(self, symbols):
        """specs의 security_type(기본 STOCK)으로 종목 정보를 만든다."""
        self._call("stocks")
        return {s: {"name": f"종목{s}", "security_type": self.specs[s].get("type", "STOCK"), "common": True,
                    "active": True, "suspended": False} for s in symbols}

    def warnings(self, symbol):
        """경고 없음."""
        self._call("warnings", symbol)
        return []

    def short_selling(self, symbol, count):
        """specs의 short(기본 0.05) 비중 5일."""
        self._call("short_selling", symbol)
        return [{"date": TODAY - timedelta(days=i + 1), "amount_rate": Decimal(self.specs[symbol].get("short", "0.05"))}
                for i in range(count)]

    def credit_trades(self, symbol, count):
        """신용 잔고율 1%."""
        self._call("credit_trades", symbol)
        return [{"date": TODAY - timedelta(days=1), "margin_balance_rate": Decimal("0.01")}]

    def investor_trading(self, symbol, count):
        """외국인 +100주, 기관 -10주씩 count일."""
        self._call("investor_trading", symbol)
        return [{"date": TODAY - timedelta(days=i + 1), "foreigner": 100, "institution": -10} for i in range(count)]

    def fetch_daily(self, symbol, count):
        """오늘 봉까지 포함한 일봉."""
        self._call("fetch_daily", symbol)
        return daily()[-count:]


def trades_for(select_avg, confirm_avg, select_n=20, confirm_n=5):
    """선정 구간(청산 TODAY−200일) select_n건, 확인 구간(청산 TODAY−10일) confirm_n건의 일정 수익률 거래."""
    def make(day, ret, n):
        """같은 날 n건."""
        ts = datetime.combine(day, time(10, 1), KST)
        return [engine.Trade("X", ts, Decimal("100"), ts, Decimal("100"), Decimal(ret), "signal") for _ in range(n)]
    return make(TODAY - timedelta(days=200), select_avg, select_n) + make(TODAY - timedelta(days=10), confirm_avg, confirm_n)


@pytest.fixture
def env(monkeypatch, tmp_path):
    """메일·백필·백테스트를 대역으로 바꾸고, 전날 종목 파일을 만든다."""
    sent, collects = [], []
    backtests = {}
    monkeypatch.setattr(selector.notify, "send_mail", sent.append)
    monkeypatch.setattr(selector.collector, "collect",
                        lambda conn, client, symbols, days, stop=None: collects.append((symbols, days, stop)) or ({}, []))
    monkeypatch.setattr(selector, "backtest_trades", lambda conn, symbol, today: backtests[symbol])
    path = tmp_path / "paper_symbols.txt"
    path.write_text("005930\n000660\n", encoding="utf-8")
    return {"sent": sent, "collects": collects, "backtests": backtests, "path": path}


SPECS = {"A": {}, "B": {"type": "ETF"}, "C": {}, "D": {"short": "0.2"}, "E": {"price": "150000"}}


def rows(conn):
    """오늘 선정 결과를 (종목, 상태, 사유)로."""
    return [(r["symbol"], r["status"], r["reason"]) for r in store.load_candidates(conn, TODAY)]


def test_holiday_does_nothing(conn, env):
    """휴장이면 장 시간만 조회하고 파일·DB·메일을 건드리지 않는다."""
    client = FakeClient(Clock(START), SPECS, holiday=True)
    assert selector.run(client, conn, env["path"], now=client.clock.now) == 0
    assert client.calls == [("market_hours", None)]
    assert env["sent"] == [] and rows(conn) == []
    assert env["path"].read_text(encoding="utf-8") == "005930\n000660\n"


def test_selects_writes_file_saves_candidates_and_mails(conn, env):
    """위험 필터·백테스트를 거쳐 선정한 종목으로 파일을 바꾸고 DB·메일에 근거를 남긴다."""
    env["backtests"].update(A=trades_for("0.2", "0.4"), C=trades_for("0.2", "-0.1"))
    client = FakeClient(Clock(START), SPECS)
    assert selector.run(client, conn, env["path"], now=client.clock.now,
                        deadline=datetime(2026, 9, 18, 8, 45, tzinfo=KST)) == 0
    assert env["path"].read_text(encoding="utf-8") == "# 2026-09-18 종목 선정기 자동 생성\nA  # 종목A\n"
    assert rows(conn) == [("A", "selected", None), ("C", "rejected", "확인 구간 손실"), ("D", "rejected", "공매도")]
    [a] = [r for r in store.load_candidates(conn, TODAY) if r["symbol"] == "A"]
    assert a["metrics"]["tech"]["close"] == "50000"
    assert a["metrics"]["backtest"]["confirm"] == {"trades": 5, "avg": "0.4"}
    [(symbols, days, stop)] = env["collects"]
    assert symbols == ["A", "C"]
    assert days[0] == date(2026, 9, 17) and len(days) == 261 and all(d.weekday() < 5 for d in days)
    assert stop() is False
    [mail] = env["sent"]
    lines = mail.splitlines()
    assert lines[0] == "[종목선정] 2026-09-18 선정 1종목"
    assert lines[1].startswith("1. A 종목A  확인구간 5건 평균 +0.40% | 선정구간 20건 평균 +0.20% | 종가 50,000 ")
    assert lines[1].endswith("외국인 5일 +500주 기관 5일 -50주")
    assert lines[2] == "후보 3 → 위험 제외 1 (공매도 1) → 백테스트 제외 1 → 통과 1"
    assert lines[3] == "순위 5 중 후보 탈락 2 (10만 원 초과 1, 보통주 아님 1)"


def test_lookup_failure_rejects_symbol_and_no_selection_keeps_file(conn, env):
    """한 종목 조회 실패는 그 종목만 제외하고, 선정이 없으면 파일을 두고 전날 종목 유지 메일을 보낸다."""
    env["backtests"].update(C=trades_for("0.2", "-0.1"))
    fail = lambda method, symbol: TossError("HTTP500") if (method, symbol) == ("short_selling", "A") else None
    client = FakeClient(Clock(START), SPECS, fail=fail)
    assert selector.run(client, conn, env["path"], now=client.clock.now) == 0
    assert rows(conn) == [("A", "rejected", "조회 실패"), ("C", "rejected", "확인 구간 손실"), ("D", "rejected", "공매도")]
    assert env["path"].read_text(encoding="utf-8") == "005930\n000660\n"
    lines = env["sent"][0].splitlines()
    assert lines[0] == "[종목선정] 2026-09-18 선정 없음, 전날 종목 유지(005930, 000660)"
    assert lines[1] == "후보 3 → 위험 제외 2 (공매도 1, 조회 실패 1) → 백테스트 제외 1 → 통과 0"


def test_calculation_error_rejects_only_that_symbol(conn, env, monkeypatch):
    """기술지표 계산 중 예외가 나면 그 종목만 계산 실패로 제외하고 나머지는 그대로 진행한다."""
    real_technicals = selection.technicals
    state = {"raised": False}

    def flaky_technicals(daily):
        """A(가장 먼저 처리되는 후보)에서 딱 한 번만 예외를 던진다."""
        if not state["raised"]:
            state["raised"] = True
            raise ZeroDivisionError("boom")
        return real_technicals(daily)

    monkeypatch.setattr(selector.selection, "technicals", flaky_technicals)
    env["backtests"].update(C=trades_for("0.2", "-0.1"))
    client = FakeClient(Clock(START), SPECS)
    assert selector.run(client, conn, env["path"], now=client.clock.now) == 0
    assert rows(conn) == [("A", "rejected", "계산 실패"), ("C", "rejected", "확인 구간 손실"), ("D", "rejected", "공매도")]
    assert env["sent"][0].splitlines()[1] == "후보 3 → 위험 제외 2 (계산 실패 1, 공매도 1) → 백테스트 제외 1 → 통과 0"


def test_start_after_deadline_skips_toss_calls(conn, env):
    """이미 마감을 넘겨 실행하면 순위·종목 조회 없이 바로 전날 종목 유지 메일을 보낸다."""
    client = FakeClient(Clock(datetime(2026, 9, 18, 9, 0, tzinfo=KST)), SPECS)
    assert selector.run(client, conn, env["path"], now=client.clock.now,
                        deadline=datetime(2026, 9, 18, 8, 45, tzinfo=KST)) == 0
    assert client.calls == [("market_hours", None)]
    assert rows(conn) == []
    assert env["path"].read_text(encoding="utf-8") == "005930\n000660\n"
    assert env["sent"][0].splitlines()[0] == "[종목선정] 2026-09-18 시간 초과, 전날 종목 유지(005930, 000660)"


def test_deadline_stops_keeps_file_and_mails_timeout(conn, env):
    """마감을 넘기면 남은 후보·백필·백테스트를 멈추고, 파일은 두고, 평가 못 한 후보를 표시하고 시간 초과 메일을 보낸다."""
    client = FakeClient(Clock(START), SPECS, step=timedelta(minutes=1))
    assert selector.run(client, conn, env["path"], now=client.clock.now,
                        deadline=datetime(2026, 9, 18, 7, 35, tzinfo=KST)) == 0
    assert env["collects"] == []
    assert rows(conn) == [("A", "rejected", "미평가(시간 초과)")]
    assert env["path"].read_text(encoding="utf-8") == "005930\n000660\n"
    assert env["sent"][0].splitlines()[0] == "[종목선정] 2026-09-18 시간 초과, 전날 종목 유지(005930, 000660)"


def test_deadline_passed_during_backfill_skips_backtest(conn, env, monkeypatch):
    """백필 중 마감이 지나면 백테스트를 하지 않고 시간 초과로 끝낸다."""
    clock = Clock(START)
    def slow_collect(conn, client, symbols, days, stop=None):
        """백필이 마감을 넘기는 상황."""
        clock.t = datetime(2026, 9, 18, 8, 46, tzinfo=KST)
        assert stop() is True
        return {}, []
    monkeypatch.setattr(selector.collector, "collect", slow_collect)
    monkeypatch.setattr(selector, "backtest_trades", lambda *args: pytest.fail("백테스트하면 안 됨"))
    client = FakeClient(clock, SPECS)
    assert selector.run(client, conn, env["path"], now=clock.now,
                        deadline=datetime(2026, 9, 18, 8, 45, tzinfo=KST)) == 0
    assert rows(conn) == [("A", "rejected", "미평가(시간 초과)"), ("C", "rejected", "미평가(시간 초과)"),
                          ("D", "rejected", "공매도")]
    assert env["sent"][0].splitlines()[0].startswith("[종목선정] 2026-09-18 시간 초과")


def test_auth_error_propagates(conn, env):
    """인증 오류는 run 밖으로 올리고 파일은 그대로 둔다."""
    fail = lambda method, symbol: TossAuthError("access_denied") if method == "warnings" else None
    client = FakeClient(Clock(START), SPECS, fail=fail)
    with pytest.raises(TossAuthError):
        selector.run(client, conn, env["path"], now=client.clock.now)
    assert env["path"].read_text(encoding="utf-8") == "005930\n000660\n"


def test_rerun_same_day_overwrites_candidates(conn, env):
    """같은 날 다시 실행하면 선정 결과가 중복되지 않는다."""
    env["backtests"].update(A=trades_for("0.2", "0.4"), C=trades_for("0.2", "-0.1"))
    for _ in range(2):
        client = FakeClient(Clock(START), SPECS)
        selector.run(client, conn, env["path"], now=client.clock.now)
    assert len(store.load_candidates(conn, TODAY)) == 3


def minute_day(day, breakout=True):
    """day 08:50~15:40 1분봉. 장전 봉은 200, 정규장은 100에서 09:40에 101, 10:00에 104(orb 매수·목표 매도)."""
    out = []
    start = datetime.combine(day, time(8, 50), KST)
    for i in range(411):
        ts = start + timedelta(minutes=i)
        t = ts.time()
        if t < time(9, 0):
            p = "200"
        elif not breakout or t < time(9, 40):
            p = "100"
        else:
            p = "101" if t < time(10, 0) else "104"
        out.append(Bar(ts, Decimal(p), Decimal(p), Decimal(p), Decimal(p), 100))
    return out


def test_backtest_trades_uses_regular_toss_bars_within_365_days(conn):
    """수집기 봉(toss)만, 365일 이내만, 정규장 봉만으로 모의투자 조건 orb 백테스트를 돌린다."""
    store.save_bars(conn, "A", minute_day(date(2026, 9, 17)), source="toss")
    store.save_bars(conn, "A", minute_day(date(2025, 9, 17)), source="toss")       # 366일 전
    store.save_bars(conn, "A", minute_day(date(2026, 9, 16)), source="toss_live")  # 장중 봉
    trades = selector.backtest_trades(conn, "A", TODAY)
    assert [(t.entry_ts, t.exit_ts, t.exit_reason) for t in trades] == [
        (datetime(2026, 9, 17, 9, 41, tzinfo=KST), datetime(2026, 9, 17, 10, 1, tzinfo=KST), "signal")]
    assert trades[0].entry_price == Decimal("101.0505")


def test_backfill_days_and_write_symbols(tmp_path):
    """백필 날짜는 어제부터 365일 전까지 평일 최신순이고, 종목 파일은 주석 헤더와 종목명 주석으로 쓴다."""
    days = selector.backfill_days(TODAY)
    assert (days[0], days[-1], len(days)) == (date(2026, 9, 17), date(2025, 9, 18), 261)
    path = tmp_path / "paper_symbols.txt"
    selector.write_symbols(path, TODAY, [{"symbol": "A", "name": "에이"}, {"symbol": "B", "name": "비"}])
    assert path.read_text(encoding="utf-8") == "# 2026-09-18 종목 선정기 자동 생성\nA  # 에이\nB  # 비\n"
    assert selector.read_symbols(path) == ["A", "B"]
    assert not (tmp_path / "paper_symbols.txt.tmp").exists()


@pytest.fixture
def main_env(monkeypatch):
    """main이 실제 .env·메일을 쓰지 않도록 막고 보낸 메일 목록을 돌려준다."""
    monkeypatch.setattr(selector, "load_dotenv", lambda *args, **kwargs: None)
    for key in selector.REQUIRED_ENV + ("TOSS_BASE_URL", "TOSS_RPS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TOSS_CLIENT_ID", "CID-X")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "SECRET-X")
    sent = []
    monkeypatch.setattr(selector.notify, "send_mail", sent.append)
    return sent


def test_main_missing_env_fails_without_secrets(main_env, monkeypatch, caplog):
    """설정이 빠지면 1을 반환하고 로그·메일에는 변수 이름과 예외 종류만 남긴다."""
    monkeypatch.setenv("DATABASE_URL", "SECRETPW")
    monkeypatch.delenv("TOSS_CLIENT_ID")
    with caplog.at_level(logging.INFO):
        assert selector.main([], now=START) == 1
    assert ".env 누락: TOSS_CLIENT_ID" in caplog.text
    assert "SECRET-X" not in caplog.text and "SECRETPW" not in caplog.text
    assert main_env == ["[종목선정] 2026-09-18 실행 실패: RuntimeError"]


@pytest.mark.parametrize("argv, deadline", [
    ([], datetime(2026, 9, 18, 8, 45, tzinfo=KST)),
    (["--no-deadline"], None),
])
def test_main_passes_deadline_and_default_client(conn, main_env, monkeypatch, argv, deadline):
    """토스 기본 설정으로 클라이언트를 만들고, 기본은 08:45 마감, --no-deadline이면 마감 없이 run을 부른다."""
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    created, calls = [], []
    monkeypatch.setattr(selector, "TossClient", lambda *args: created.append(args[:4]) or "CLIENT")
    monkeypatch.setattr(selector, "run", lambda client, conn, path, deadline=None: calls.append((client, path, deadline)) or 0)
    assert selector.main(argv, now=START) == 0
    assert created == [("CID-X", "SECRET-X", "https://openapi.tossinvest.com", 15.0)]
    assert calls == [("CLIENT", selector.ROOT / "paper_symbols.txt", deadline)]


def test_main_auth_error_returns_1_and_mails(conn, main_env, monkeypatch):
    """인증 오류면 실행 실패 메일을 보내고 1을 반환한다."""
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setattr(selector, "TossClient", lambda *args: "CLIENT")
    def auth_fail(*args, **kwargs):
        """인증 실패를 흉내 낸다."""
        raise TossAuthError("access_denied")
    monkeypatch.setattr(selector, "run", auth_fail)
    assert selector.main([], now=START) == 1
    assert main_env == ["[종목선정] 2026-09-18 실행 실패: TossAuthError"]
