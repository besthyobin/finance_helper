import json
import os
import threading
from datetime import date, datetime
from decimal import Decimal
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

import engine
import store
import web
from bars import KST, Bar


@pytest.fixture
def base(conn):
    """테스트 DB를 읽는 화면 서버를 빈 포트에 띄우고 기본 URL을 준다."""
    server = web.make_server(os.environ["TEST_DATABASE_URL"], 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def get(url):
    """GET 요청의 (상태 코드, Content-Type, 본문 bytes)를 반환한다. 오류 응답도 그대로 돌려준다."""
    try:
        with urlopen(url) as r:
            return r.status, r.headers["Content-Type"], r.read()
    except HTTPError as e:
        return e.code, e.headers["Content-Type"], e.read()


def kst(day, hh, mm):
    """2026-09-day HH:MM KST."""
    return datetime(2026, 9, day, hh, mm, tzinfo=KST)


def save_trade(conn, symbol, day, qty, pnl):
    """day 09:41 진입, 10:01 청산한 모의 거래를 저장한다."""
    trade = engine.Trade(symbol, kst(day, 9, 41), Decimal("101.0505"), kst(day, 10, 1),
                         Decimal("103.948"), Decimal("2.63"), "signal")
    store.save_paper_trade(conn, "orb", qty, trade, Decimal(pnl))


@pytest.mark.parametrize("path, content_type, text", [
    ("/", "text/html", "모의투자"),
    ("/app.js", "text/javascript", "refresh"),
    ("/charts.js", "text/javascript", "echarts"),
    ("/style.css", "text/css", "body"),
])
def test_static_files_are_served(base, path, content_type, text):
    """화면 파일 4개를 알맞은 Content-Type으로 준다."""
    status, ctype, body = get(base + path)
    assert status == 200 and ctype.startswith(content_type) and text in body.decode("utf-8")


@pytest.mark.parametrize("path", ["/.env", "/../.env", "/web/index.html", "/paper.py", "/api/nope"])
def test_other_paths_are_404(base, path):
    """정해진 화면 파일·API 외 경로는 404."""
    assert get(base + path)[0] == 404


def test_status_adds_eval_krw_for_holdings(base, conn):
    """보유 종목은 (현재가 × (1 − 슬리피지) − 매수가) × 수량 평가손익을, 미보유는 null을 준다."""
    store.save_paper_status(conn, {"symbol": "A", "trade_date": date(2026, 9, 17), "last_bar_ts": kst(17, 10, 0),
                                   "last_close": Decimal("104"), "qty": 10, "entry_ts": kst(17, 9, 41),
                                   "entry_price": Decimal("101.0505")})
    store.save_paper_status(conn, {"symbol": "B", "trade_date": date(2026, 9, 17), "last_bar_ts": kst(17, 10, 0),
                                   "last_close": Decimal("100"), "qty": 0, "entry_ts": None, "entry_price": None})
    status, ctype, body = get(base + "/api/status")
    assert status == 200 and ctype.startswith("application/json")
    a, b = json.loads(body)
    assert (a["symbol"], a["eval_krw"], a["entry_ts"], a["trade_date"]) == (
        "A", "28.9750", "2026-09-17T09:41:00+09:00", "2026-09-17")
    assert (b["symbol"], b["qty"], b["eval_krw"], b["entry_price"]) == ("B", 0, None, None)


def test_trades_by_date_and_bad_date(base, conn):
    """date 파라미터의 KST 날짜 거래만 주고, 날짜 형식이 틀리면 400."""
    save_trade(conn, "A", 17, 10, "100")
    save_trade(conn, "B", 16, 5, "-50")
    rows = json.loads(get(base + "/api/trades?date=2026-09-16")[2])
    assert [(r["symbol"], r["qty"], r["pnl_krw"], r["exit_ts"]) for r in rows] == [
        ("B", 5, "-50", "2026-09-16T10:01:00+09:00")]
    assert get(base + "/api/trades?date=2026-13-01")[0] == 400


def test_daily_sums_by_day(base, conn):
    """일별 거래 수·수익 거래 수·손익 합계를 최신 날짜부터 준다."""
    save_trade(conn, "A", 17, 10, "100")
    save_trade(conn, "B", 17, 10, "-30")
    save_trade(conn, "A", 16, 5, "-50")
    assert json.loads(get(base + "/api/daily")[2]) == [
        {"day": "2026-09-17", "trades": 2, "wins": 1, "pnl_krw": "70"},
        {"day": "2026-09-16", "trades": 1, "wins": 0, "pnl_krw": "-50"},
    ]


def bar_at(day, hh, mm, close):
    """2026-09-day HH:MM 시작, 시가=고가=저가=종가=close인 봉."""
    p = Decimal(close)
    return Bar(kst(day, hh, mm), p, p, p, p, 100)


def test_bars_prefer_live_then_collected(base, conn):
    """그날 toss_live 봉이 있으면 그것을, 없으면 수집기 toss 봉을 시각 오름차순으로 준다."""
    store.save_bars(conn, "A", [bar_at(17, 9, 1, "101"), bar_at(17, 9, 0, "100")], source="toss_live")
    store.save_bars(conn, "A", [bar_at(17, 9, 0, "999")], source="toss")
    store.save_bars(conn, "A", [bar_at(16, 9, 0, "90")], source="toss")
    today = json.loads(get(base + "/api/bars?symbol=A&date=2026-09-17")[2])
    assert today == {"source": "toss_live", "bars": [
        {"ts": "2026-09-17T09:00:00+09:00", "open": "100", "high": "100", "low": "100", "close": "100", "volume": 100},
        {"ts": "2026-09-17T09:01:00+09:00", "open": "101", "high": "101", "low": "101", "close": "101", "volume": 100},
    ]}
    before = json.loads(get(base + "/api/bars?symbol=A&date=2026-09-16")[2])
    assert before["source"] == "toss" and [b["close"] for b in before["bars"]] == ["90"]
    assert json.loads(get(base + "/api/bars?symbol=B&date=2026-09-16")[2]) == {"source": None, "bars": []}


@pytest.mark.parametrize("query", ["date=2026-09-17", "symbol=&date=2026-09-17", "symbol=A&date=2026-13-01"])
def test_bars_bad_request(base, query):
    """종목이 없거나 날짜 형식이 틀리면 400."""
    assert get(base + "/api/bars?" + query)[0] == 400


def test_db_failure_returns_500_without_connection_string(conn):
    """DB 접속이 실패하면 500과 예외 종류만 주고 접속 문자열은 노출하지 않는다."""
    server = web.make_server("postgresql://nobody:SECRETPW@127.0.0.1:1/none", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        status, _, body = get(f"http://127.0.0.1:{server.server_address[1]}/api/status")
    finally:
        server.shutdown()
        server.server_close()
    assert status == 500 and b"SECRETPW" not in body


def test_status_and_trades_include_stock_names(base, conn):
    """status와 trades 응답에 종목명(name)이 포함된다."""
    conn.execute(
        "INSERT INTO selection_candidates (run_date, symbol, name, rank, status, metrics) "
        "VALUES ('2026-09-17', '005930', '삼성전자', 1, 'selected', '{}') "
        "ON CONFLICT (run_date, symbol) DO UPDATE SET name = EXCLUDED.name"
    )
    store.save_paper_status(conn, {
        "symbol": "005930", "trade_date": date(2026, 9, 17), "last_bar_ts": kst(17, 10, 0),
        "last_close": Decimal("260000"), "qty": 0, "entry_ts": None, "entry_price": None
    })
    status, _, body = get(base + "/api/status")
    assert status == 200
    rows = json.loads(body)
    assert any(r["symbol"] == "005930" and r.get("name") == "삼성전자" for r in rows)

    save_trade(conn, "005930", 17, 10, "50000")
    status, _, body = get(base + "/api/trades?date=2026-09-17")
    assert status == 200
    trades = json.loads(body)
    assert any(t["symbol"] == "005930" and t.get("name") == "삼성전자" for t in trades)


def post(url, data_dict):
    """POST 요청 (JSON). (상태코드, headers, 본문 bytes) 반환."""
    from urllib.request import Request
    req_body = json.dumps(data_dict).encode("utf-8")
    req = Request(url, data=req_body, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read()


def test_get_and_post_policy(base, tmp_path, monkeypatch):
    """GET /api/policy로 정책과 비용 조회가 가능하고, POST /api/policy로 수정이 반영된다."""
    import policy
    policy_file = tmp_path / "policy.json"
    monkeypatch.setattr(policy, "POLICY_PATH", policy_file)

    # 1. 초기 정책 GET
    status, ctype, body = get(base + "/api/policy")
    assert status == 200 and ctype.startswith("application/json")
    res = json.loads(body)
    assert "policy" in res and "costs" in res
    assert res["policy"]["stop_pct"] == -1.0
    assert res["costs"]["slippage"] == "0.0005"

    # 2. 정책 수정 POST
    status, ctype, body = post(base + "/api/policy", {"stop_pct": -1.8, "target_pct": 2.5})
    assert status == 200
    res_post = json.loads(body)
    assert res_post["ok"] is True
    assert res_post["policy"]["stop_pct"] == -1.8

    # 3. GET 재확인
    status, _, body = get(base + "/api/policy")
    res_get = json.loads(body)
    assert res_get["policy"]["stop_pct"] == -1.8
    assert res_get["policy"]["target_pct"] == 2.5

    # 4. 잘못된 값 POST 시 400 반환
    status, _, body = post(base + "/api/policy", {"stop_pct": "invalid"})
    assert status == 400
    res_err = json.loads(body)
    assert res_err["ok"] is False


def test_api_account_summary(base, conn, monkeypatch):
    """GET /api/account로 자본금, 총 평가 자산, 현금 예수금, 주식 평가액, 손익 등이 정확히 계산되어 반환된다."""
    monkeypatch.setenv("PAPER_CAPITAL", "10000000")

    # 1. 초기 미보유 상태
    status, ctype, body = get(base + "/api/account")
    assert status == 200 and ctype.startswith("application/json")
    acc = json.loads(body)
    assert acc["capital"] == "10000000"
    assert acc["total_assets"] == "10000000"
    assert acc["cash"] == "10000000"
    assert acc["stock_eval"] == "0"
    assert acc["realized_pnl"] == "0"
    assert acc["unrealized_pnl"] == "0"
    assert acc["total_pnl"] == "0"
    assert acc["return_pct"] == "0.00"

    # 2. 보유 종목 및 당일 체결 발생 시
    today = datetime.now(KST).date()
    store.save_paper_status(conn, {
        "symbol": "A", "trade_date": today, "last_bar_ts": datetime.now(KST),
        "last_close": Decimal("104"), "qty": 10, "entry_ts": datetime.now(KST),
        "entry_price": Decimal("101.0505")
    })
    trade = engine.Trade("B", datetime.now(KST), Decimal("100"), datetime.now(KST), Decimal("105"), Decimal("5.0"), "signal")
    store.save_paper_trade(conn, "orb", 100, trade, Decimal("50000"))

    status, _, body = get(base + "/api/account")
    assert status == 200
    acc = json.loads(body)
    assert acc["capital"] == "10000000"
    assert acc["realized_pnl"] == "50000"
    assert float(acc["unrealized_pnl"]) > 28.0
    assert float(acc["stock_eval"]) == 1039.48
    assert float(acc["total_pnl"]) == 50000 + float(acc["unrealized_pnl"])
    assert float(acc["total_assets"]) == 10000000 + float(acc["total_pnl"])

