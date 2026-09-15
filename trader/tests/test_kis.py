import itertools
import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import requests

from kis import KST, Bar, KisClient, KisError

NOW = datetime(2026, 9, 14, 16, 0, tzinfo=KST)


class FakeResponse:
    """requests.Response 대역: status_code와 json()만 흉내 낸다."""

    def __init__(self, body, status=200):
        self.status_code = status
        self._body = body

    def json(self):
        """본문이 None이면 JSON이 아닌 응답처럼 ValueError를 낸다."""
        if self._body is None:
            raise ValueError("not json")
        return self._body


class FakeKis:
    """KIS 서버 대역. 토큰 요청과 GET 요청에 준비된 응답을 순서대로 돌려준다."""

    def __init__(self, get_responses=(), token_responses=None, on_get=None):
        self.get_responses = list(get_responses)
        self.token_responses = list(
            token_responses if token_responses is not None else [token_ok("tok1")]
        )
        self.on_get = on_get
        self.calls = []

    def __call__(self, method, url, **kwargs):
        """요청을 기록하고 준비된 응답(또는 예외)을 반환한다."""
        self.calls.append((method, url, kwargs))
        if url.endswith("/oauth2/tokenP"):
            item = self.token_responses.pop(0)
        elif self.on_get:
            item = self.on_get(kwargs["params"])
        else:
            item = self.get_responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def gets(self):
        """GET 요청 기록만 반환한다."""
        return [c for c in self.calls if c[0] == "GET"]


def token_ok(token):
    """정상 토큰 발급 응답을 만든다."""
    return FakeResponse({"access_token": token, "token_type": "Bearer", "expires_in": 86400})


def ok(rows=()):
    """정상 시세 응답을 만든다."""
    return FakeResponse({"rt_cd": "0", "msg_cd": "MCA00000", "msg1": "정상처리 되었습니다.",
                         "output1": {}, "output2": list(rows)})


def err(code):
    """KIS 오류 응답을 만든다."""
    return FakeResponse({"rt_cd": "1", "msg_cd": code, "msg1": "오류"}, status=500)


def make_client(tmp_path, fake, sleeps=None, clock=None):
    """테스트용 KisClient를 만든다. sleep 인자는 sleeps에 기록되고 기본 clock은 매번 10초씩 흐른다."""
    return KisClient(
        "key", "secret", "https://kis.test", 1000, tmp_path / ".token.json",
        send=fake,
        sleep=(sleeps if sleeps is not None else []).append,
        clock=clock or itertools.count(0.0, 10.0).__next__,
        now=lambda: NOW,
    )


def get(client):
    """임의 경로로 _get을 호출한다."""
    return client._get("/uapi/test", "TR0001", {"A": "1"})


def test_token_is_cached_to_file(tmp_path):
    """발급한 토큰을 파일에 저장하고 다른 클라이언트가 재사용한다."""
    assert make_client(tmp_path, FakeKis()).get_token() == "tok1"
    reuse = FakeKis(token_responses=[])
    assert make_client(tmp_path, reuse).get_token() == "tok1"
    assert reuse.calls == []


def test_expired_cached_token_is_reissued(tmp_path):
    """만료가 5분 이내로 남은 캐시 토큰은 쓰지 않고 새로 발급한다."""
    (tmp_path / ".token.json").write_text(json.dumps(
        {"access_token": "old", "expires_at": (NOW + timedelta(minutes=4)).isoformat()}
    ), encoding="utf-8")
    fake = FakeKis(token_responses=[token_ok("new")])
    assert make_client(tmp_path, fake).get_token() == "new"


def test_token_issue_limit_waits_60s(tmp_path):
    """토큰 발급 1분 제한(EGW00133)이면 60초 기다렸다가 한 번 더 발급한다."""
    limited = FakeResponse({"error_code": "EGW00133", "error_description": "1분당 1회"}, status=403)
    sleeps = []
    client = make_client(tmp_path, FakeKis(token_responses=[limited, token_ok("tok1")]), sleeps)
    assert client.get_token() == "tok1"
    assert sleeps == [60]


def test_get_returns_body_with_auth_headers(tmp_path):
    """정상 응답 본문을 반환하고 인증 헤더와 TR ID를 보낸다."""
    fake = FakeKis(get_responses=[ok()])
    assert get(make_client(tmp_path, fake))["rt_cd"] == "0"
    _, url, kwargs = fake.gets()[0]
    assert url == "https://kis.test/uapi/test"
    assert kwargs["params"] == {"A": "1"}
    assert kwargs["headers"]["authorization"] == "Bearer tok1"
    assert kwargs["headers"]["tr_id"] == "TR0001"
    assert kwargs["timeout"] == 10


def test_get_retries_rate_limit(tmp_path):
    """호출 한도 초과(EGW00201)면 1초 쉬고 재시도한다."""
    sleeps = []
    client = make_client(tmp_path, FakeKis(get_responses=[err("EGW00201"), ok()]), sleeps)
    assert get(client)["rt_cd"] == "0"
    assert sleeps == [1]


def test_get_raises_after_three_rate_limit_retries(tmp_path):
    """호출 한도 초과가 재시도 3회 후에도 계속되면 예외를 낸다."""
    sleeps = []
    client = make_client(tmp_path, FakeKis(get_responses=[err("EGW00201")] * 4), sleeps)
    with pytest.raises(KisError) as e:
        get(client)
    assert e.value.code == "EGW00201"
    assert sleeps == [1, 1, 1]


def test_get_refreshes_expired_token_once(tmp_path):
    """토큰 만료(EGW00123)면 토큰을 재발급해 같은 호출을 다시 한다."""
    fake = FakeKis(get_responses=[err("EGW00123"), ok()],
                   token_responses=[token_ok("tok1"), token_ok("tok2")])
    assert get(make_client(tmp_path, fake))["rt_cd"] == "0"
    assert fake.gets()[1][2]["headers"]["authorization"] == "Bearer tok2"


def test_get_retries_network_errors_then_raises(tmp_path):
    """네트워크 오류는 1·2·4초 간격으로 3회 재시도한 뒤 NETWORK 예외를 낸다."""
    sleeps = []
    fake = FakeKis(get_responses=[requests.ConnectionError("down")] * 4)
    with pytest.raises(KisError) as e:
        get(make_client(tmp_path, fake, sleeps))
    assert e.value.code == "NETWORK"
    assert sleeps == [1, 2, 4]


def test_get_retries_5xx_without_kis_code(tmp_path):
    """KIS 오류 코드 없는 5xx는 네트워크 오류처럼 재시도한다."""
    sleeps = []
    client = make_client(tmp_path, FakeKis(get_responses=[FakeResponse(None, 502), ok()]), sleeps)
    assert get(client)["rt_cd"] == "0"
    assert sleeps == [1]


def test_get_raises_unknown_error_without_secrets(tmp_path):
    """알 수 없는 오류 코드는 바로 예외를 내고, 메시지에 앱키·시크릿·토큰이 없다."""
    fake = FakeKis(get_responses=[err("EGW99999")])
    with pytest.raises(KisError) as e:
        get(make_client(tmp_path, fake))
    assert e.value.code == "EGW99999"
    assert not {"key", "secret", "tok1"} & set(str(e.value).split())


def test_throttle_waits_between_calls(tmp_path):
    """직전 호출로부터 1/rps초가 지나지 않았으면 남은 시간만큼 기다린다."""
    sleeps = []
    client = make_client(tmp_path, FakeKis(get_responses=[ok()]), sleeps, clock=lambda: 0.0)
    client.rps = 10
    get(client)
    assert sleeps == [pytest.approx(0.1)]


TODAY = date(2026, 9, 14)
FIXTURES = Path(__file__).parent / "fixtures"


def row(hhmmss, ymd="20260914", price="70000"):
    """KIS output2 한 행을 만든다."""
    return {"stck_bsop_date": ymd, "stck_cntg_hour": hhmmss, "stck_prpr": price,
            "stck_oprc": price, "stck_hgpr": price, "stck_lwpr": price,
            "cntg_vol": "100", "acml_tr_pbmn": "0"}


def day_minutes():
    """정상 거래일 1분봉 시각(09:00~15:20 매분, 15:30)을 HHMMSS 목록으로 만든다."""
    out, t = [], datetime(2026, 9, 14, 9, 0)
    while t.time() <= time(15, 20):
        out.append(t.strftime("%H%M%S"))
        t += timedelta(minutes=1)
    return out + ["153000"]


def market(minutes):
    """기준 시각 이하의 봉을 최신순으로 최대 30개 돌려주는 on_get 핸들러를 만든다."""
    def on_get(params):
        hour = params["FID_INPUT_HOUR_1"]
        picked = sorted((m for m in minutes if m <= hour), reverse=True)[:30]
        return ok([row(m) for m in picked])
    return on_get


def test_fetch_parses_kis_fields(tmp_path):
    """KIS 응답 필드를 Bar로 변환한다."""
    body = json.loads((FIXTURES / "minute_page.json").read_text(encoding="utf-8"))
    fake = FakeKis(on_get=lambda params: FakeResponse(body))
    bars = make_client(tmp_path, fake).fetch_minute_bars("005930", TODAY)
    assert [b.ts.time() for b in bars] == [time(9, 8), time(9, 9), time(9, 10)]
    assert bars[-1] == Bar(datetime(2026, 9, 14, 9, 10, tzinfo=KST), Decimal("70000"),
                           Decimal("70200"), Decimal("69900"), Decimal("70100"), 15234)


def test_fetch_sends_minute_chart_request(tmp_path):
    """당일분봉 경로·TR ID·파라미터로 15:30부터 요청한다."""
    fake = FakeKis(on_get=lambda params: ok())
    make_client(tmp_path, fake).fetch_minute_bars("005930", TODAY)
    _, url, kwargs = fake.gets()[0]
    assert url.endswith("/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice")
    assert kwargs["headers"]["tr_id"] == "FHKST03010200"
    assert kwargs["params"] == {"FID_ETC_CLS_CODE": "", "FID_COND_MRKT_DIV_CODE": "J",
                                "FID_INPUT_ISCD": "005930", "FID_INPUT_HOUR_1": "153000",
                                "FID_PW_DATA_INCU_YN": "Y"}


def test_fetch_walks_back_to_market_open(tmp_path):
    """15:30부터 거꾸로 받아 09:00에서 멈추고, 겹친 봉 없이 오름차순으로 반환한다."""
    minutes = day_minutes()
    fake = FakeKis(on_get=market(minutes))
    bars = make_client(tmp_path, fake).fetch_minute_bars("005930", TODAY)
    assert len(bars) == len(minutes)
    assert bars[0].ts == datetime(2026, 9, 14, 9, 0, tzinfo=KST)
    assert bars[-1].ts == datetime(2026, 9, 14, 15, 30, tzinfo=KST)
    assert all(a.ts < b.ts for a, b in zip(bars, bars[1:]))
    assert len(fake.gets()) <= 20


def test_fetch_stops_when_time_does_not_move_back(tmp_path):
    """다음 페이지의 가장 이른 시각이 기준 시각보다 이르지 않으면 멈춘다."""
    same_page = ok([row(m) for m in sorted(day_minutes()[360:390], reverse=True)])
    fake = FakeKis(on_get=lambda params: same_page)
    bars = make_client(tmp_path, fake).fetch_minute_bars("005930", TODAY)
    assert len(fake.gets()) == 2
    assert len(bars) == len(same_page.json()["output2"])


def test_fetch_raises_after_20_pages(tmp_path):
    """20페이지 안에 09:00에 닿지 못하면 PAGINATION 예외를 낸다."""
    def one_minute_back(params):
        t = datetime.strptime(params["FID_INPUT_HOUR_1"], "%H%M%S") - timedelta(minutes=1)
        return ok([row(t.strftime("%H%M%S"))])
    fake = FakeKis(on_get=one_minute_back)
    with pytest.raises(KisError) as e:
        make_client(tmp_path, fake).fetch_minute_bars("005930", TODAY)
    assert e.value.code == "PAGINATION"
    assert len(fake.gets()) == 20


def test_fetch_ignores_other_dates(tmp_path):
    """오늘이 아닌 날짜의 봉만 오면 빈 목록을 반환하고 더 요청하지 않는다."""
    fake = FakeKis(on_get=lambda params: ok([row("153000", ymd="20260911")]))
    assert make_client(tmp_path, fake).fetch_minute_bars("005930", TODAY) == []
    assert len(fake.gets()) == 1
