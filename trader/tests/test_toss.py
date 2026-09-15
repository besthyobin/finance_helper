import itertools
import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import requests

from bars import KST, Bar
from toss import TossAuthError, TossClient, TossError

NOW = datetime(2026, 9, 15, 20, 30, tzinfo=KST)
SECRETS = ("CID-X", "SECRET-X", "TOK-1", "TOK-2")


class FakeResponse:
    """requests.Response 대역: status_code, headers, json()만 흉내 낸다."""

    def __init__(self, body, status=200, headers=None):
        self.status_code = status
        self.headers = headers or {}
        self._body = body

    def json(self):
        """본문이 None이면 JSON이 아닌 응답처럼 ValueError를 낸다."""
        if self._body is None:
            raise ValueError("not json")
        return self._body


class FakeToss:
    """토스 서버 대역. 토큰 요청과 GET 요청에 준비된 응답(또는 예외)을 순서대로 돌려준다."""

    def __init__(self, get_responses=(), token_responses=None, on_get=None):
        self.get_responses = list(get_responses)
        self.token_responses = list(
            token_responses if token_responses is not None else [token_ok("TOK-1")]
        )
        self.on_get = on_get
        self.calls = []

    def __call__(self, method, url, **kwargs):
        """요청을 기록하고 준비된 응답을 반환하거나 예외를 던진다."""
        self.calls.append((method, url, kwargs))
        if url.endswith("/oauth2/token"):
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

    def token_calls(self):
        """토큰 발급 요청 기록만 반환한다."""
        return [c for c in self.calls if c[0] == "POST"]


def token_ok(token):
    """정상 토큰 발급 응답을 만든다."""
    return FakeResponse({"access_token": token, "token_type": "Bearer", "expires_in": 86399})


def ok(candles=(), next_before=None):
    """정상 캔들 응답을 만든다."""
    return FakeResponse({"result": {"candles": list(candles), "nextBefore": next_before}})


def err(status, code, headers=None):
    """토스 공통 오류 응답을 만든다."""
    return FakeResponse({"error": {"requestId": "r1", "code": code, "message": "오류"}}, status, headers)


def make_client(tmp_path, fake, sleeps=None, clock=None):
    """테스트용 TossClient를 만든다. sleep 인자는 sleeps에 기록되고 기본 clock은 매번 10초씩 흐른다."""
    return TossClient(
        "CID-X", "SECRET-X", "https://toss.test", 1000, tmp_path / ".token.json",
        send=fake,
        sleep=(sleeps if sleeps is not None else []).append,
        clock=clock or itertools.count(0.0, 10.0).__next__,
        now=lambda: NOW,
    )


def get(client):
    """임의 경로로 _get을 호출한다."""
    return client._get("/api/v1/test", {"A": "1"})


def assert_no_secrets(exc):
    """예외 메시지에 client_id·secret·토큰이 없는지 확인한다."""
    assert not any(s in str(exc) for s in SECRETS)


def test_token_is_issued_with_form_and_cached_to_file(tmp_path):
    """form 본문으로 토큰을 발급해 만료 시각과 함께 파일에 저장하고, 다른 클라이언트가 재사용한다."""
    fake = FakeToss()
    assert make_client(tmp_path, fake).get_token() == "TOK-1"
    method, url, kwargs = fake.calls[0]
    assert (method, url) == ("POST", "https://toss.test/oauth2/token")
    assert kwargs["data"] == {"grant_type": "client_credentials",
                              "client_id": "CID-X", "client_secret": "SECRET-X"}
    assert kwargs["timeout"] == 10
    cached = json.loads((tmp_path / ".token.json").read_text(encoding="utf-8"))
    assert cached == {"access_token": "TOK-1",
                      "expires_at": (NOW + timedelta(seconds=86399)).isoformat()}

    reuse = FakeToss(token_responses=[])
    assert make_client(tmp_path, reuse).get_token() == "TOK-1"
    assert reuse.calls == []


def test_token_expiring_within_5_minutes_is_reissued(tmp_path):
    """만료가 5분 이내로 남은 캐시 토큰은 쓰지 않고 새로 발급한다."""
    (tmp_path / ".token.json").write_text(json.dumps(
        {"access_token": "OLD", "expires_at": (NOW + timedelta(minutes=4)).isoformat()}
    ), encoding="utf-8")
    fake = FakeToss(token_responses=[token_ok("TOK-2")])
    assert make_client(tmp_path, fake).get_token() == "TOK-2"


@pytest.mark.parametrize("status, code", [(401, "invalid_client"), (403, "access_denied")])
def test_token_auth_failure_raises_auth_error(tmp_path, status, code):
    """토큰 발급 401·403은 OAuth2 error 값을 code로 TossAuthError를 낸다."""
    fake = FakeToss(token_responses=[FakeResponse({"error": code, "error_description": "x"}, status)])
    with pytest.raises(TossAuthError) as e:
        make_client(tmp_path, fake).get_token()
    assert e.value.code == code
    assert_no_secrets(e.value)


def test_token_rate_limit_waits_retry_after(tmp_path):
    """토큰 발급 429면 Retry-After초 기다렸다가 다시 발급한다."""
    sleeps = []
    fake = FakeToss(token_responses=[err(429, "rate-limit-exceeded", {"Retry-After": "2"}),
                                     token_ok("TOK-1")])
    assert make_client(tmp_path, fake, sleeps).get_token() == "TOK-1"
    assert sleeps == [2]


def test_get_returns_body_with_bearer_header(tmp_path):
    """정상 응답 본문을 반환하고 Bearer 헤더·파라미터·timeout을 보낸다."""
    fake = FakeToss(get_responses=[ok()])
    assert get(make_client(tmp_path, fake)) == {"result": {"candles": [], "nextBefore": None}}
    _, url, kwargs = fake.gets()[0]
    assert url == "https://toss.test/api/v1/test"
    assert kwargs["params"] == {"A": "1"}
    assert kwargs["headers"] == {"Authorization": "Bearer TOK-1"}
    assert kwargs["timeout"] == 10


def test_get_reissues_token_once_on_expired_token(tmp_path):
    """401 expired-token이면 캐시를 지우고 한 번 재발급해 같은 호출을 다시 한다."""
    fake = FakeToss(get_responses=[err(401, "expired-token"), ok()],
                    token_responses=[token_ok("TOK-1"), token_ok("TOK-2")])
    get(make_client(tmp_path, fake))
    assert fake.gets()[1][2]["headers"] == {"Authorization": "Bearer TOK-2"}
    cached = json.loads((tmp_path / ".token.json").read_text(encoding="utf-8"))
    assert cached["access_token"] == "TOK-2"


def test_get_raises_auth_error_when_401_repeats_after_reissue(tmp_path):
    """재발급 후에도 401이면 더 재발급하지 않고 TossAuthError를 낸다."""
    fake = FakeToss(get_responses=[err(401, "expired-token"), err(401, "token-revoked")],
                    token_responses=[token_ok("TOK-1"), token_ok("TOK-2")])
    with pytest.raises(TossAuthError) as e:
        get(make_client(tmp_path, fake))
    assert e.value.code == "token-revoked"
    assert len(fake.token_calls()) == 2


def test_get_forbidden_raises_auth_error(tmp_path):
    """403은 재시도 없이 TossAuthError를 낸다."""
    with pytest.raises(TossAuthError) as e:
        get(make_client(tmp_path, FakeToss(get_responses=[err(403, "forbidden")])))
    assert e.value.code == "forbidden"


def test_get_waits_retry_after_on_rate_limit(tmp_path):
    """429면 Retry-After초(없으면 1초) 기다렸다가 재시도한다."""
    sleeps = []
    fake = FakeToss(get_responses=[err(429, "rate-limit-exceeded"),
                                   err(429, "rate-limit-exceeded", {"Retry-After": "3"}), ok()])
    get(make_client(tmp_path, fake, sleeps))
    assert sleeps == [1, 3]


def test_get_raises_after_three_rate_limit_retries(tmp_path):
    """429가 재시도 3회 후에도 계속되면 TossError를 낸다."""
    sleeps = []
    fake = FakeToss(get_responses=[err(429, "rate-limit-exceeded")] * 4)
    with pytest.raises(TossError) as e:
        get(make_client(tmp_path, fake, sleeps))
    assert e.value.code == "rate-limit-exceeded"
    assert sleeps == [1, 1, 1]


def test_get_retries_network_errors_then_raises(tmp_path):
    """네트워크 오류는 1·2·4초 간격으로 3회 재시도한 뒤 예외 종류만 담은 NETWORK 예외를 낸다."""
    sleeps = []
    fake = FakeToss(get_responses=[requests.ConnectionError("https://toss.test/api/v1/test CID-X")] * 4)
    with pytest.raises(TossError) as e:
        get(make_client(tmp_path, fake, sleeps))
    assert e.value.code == "NETWORK"
    assert str(e.value) == "NETWORK ConnectionError"
    assert sleeps == [1, 2, 4]


def test_get_retries_5xx_without_error_code(tmp_path):
    """error.code 없는 5xx는 네트워크 오류처럼 재시도한다."""
    sleeps = []
    get(make_client(tmp_path, FakeToss(get_responses=[FakeResponse(None, 502), ok()]), sleeps))
    assert sleeps == [1]


def test_get_raises_other_errors_with_code_and_no_secrets(tmp_path):
    """그 밖 4xx는 재시도 없이 응답 error.code로 TossError를 내고, 메시지에 비밀값이 없다."""
    fake = FakeToss(get_responses=[err(404, "not-found")])
    with pytest.raises(TossError) as e:
        get(make_client(tmp_path, fake))
    assert type(e.value) is TossError
    assert e.value.code == "not-found"
    assert len(fake.gets()) == 1
    assert_no_secrets(e.value)


def test_throttle_waits_between_calls(tmp_path):
    """직전 호출로부터 1/rps초가 지나지 않았으면 남은 시간만큼 기다린다."""
    sleeps = []
    client = make_client(tmp_path, FakeToss(get_responses=[ok()]), sleeps, clock=lambda: 0.0)
    client.rps = 10
    get(client)
    assert sleeps == [pytest.approx(0.1)]


DAY = date(2026, 9, 14)       # 월요일
PREV_DAY = date(2026, 9, 11)  # 금요일
FIXTURES = Path(__file__).parent / "fixtures"


def candle(end, price="70000"):
    """끝나는 시각 end의 캔들 한 개를 응답 형식으로 만든다."""
    return {"timestamp": end.isoformat(), "openPrice": price, "highPrice": price,
            "lowPrice": price, "closePrice": price, "volume": "100", "currency": "KRW"}


def session_ends(day):
    """08:01~20:00 매분, 봉이 끝나는 시각 720개를 만든다."""
    first = datetime.combine(day, time(8, 1), KST)
    return [first + timedelta(minutes=i) for i in range(720)]


def market(ends, next_offset):
    """before 이하 봉을 최신순 200개씩 주고 nextBefore를 마지막 봉 시각 + next_offset으로 주는 on_get을 만든다."""
    def on_get(params):
        before = datetime.fromisoformat(params["before"])
        page = sorted((e for e in ends if e <= before), reverse=True)[:200]
        return ok([candle(e) for e in page], (page[-1] + next_offset).isoformat() if page else None)
    return on_get


def test_fetch_day_parses_candle_fields(tmp_path):
    """캔들을 봉 시작 시각(−1분)·Decimal 가격·int 거래량의 Bar로 바꿔 오름차순으로 반환한다."""
    body = json.loads((FIXTURES / "toss_candles.json").read_text(encoding="utf-8"))
    fake = FakeToss(on_get=lambda params: FakeResponse(body))
    bars = make_client(tmp_path, fake).fetch_day("005930", DAY)
    assert [b.ts for b in bars] == [datetime(2026, 9, 14, 9, m, tzinfo=KST) for m in (0, 1, 2)]
    assert bars[-1] == Bar(datetime(2026, 9, 14, 9, 2, tzinfo=KST), Decimal("70100"),
                           Decimal("70300"), Decimal("70000"), Decimal("70200"), 15234)
    assert len(fake.gets()) == 1


@pytest.mark.parametrize("next_offset", [timedelta(minutes=-1), timedelta(0)])
def test_fetch_day_walks_pages_back_to_previous_day(tmp_path, next_offset):
    """200개씩 거꾸로 받아 전날 봉에서 멈추고, nextBefore 겹침과 무관하게 중복 없는 720봉을 반환한다."""
    fake = FakeToss(on_get=market(session_ends(PREV_DAY) + session_ends(DAY), next_offset))
    bars = make_client(tmp_path, fake).fetch_day("005930", DAY)
    assert len(bars) == 720
    assert bars[0].ts == datetime(2026, 9, 14, 8, 0, tzinfo=KST)
    assert bars[-1].ts == datetime(2026, 9, 14, 19, 59, tzinfo=KST)
    assert all(a.ts < b.ts for a, b in zip(bars, bars[1:]))
    assert len(fake.gets()) == 4
    _, url, kwargs = fake.gets()[0]
    assert url == "https://toss.test/api/v1/candles"
    assert kwargs["params"] == {"symbol": "005930", "interval": "1m", "count": 200,
                                "before": "2026-09-14T23:59:59+09:00", "adjusted": "false"}


@pytest.mark.parametrize("page", [
    ok([candle(e) for e in reversed(session_ends(PREV_DAY)[-200:])], "2026-09-11T16:40:00+09:00"),
    ok([], None),
])
def test_fetch_day_returns_empty_for_day_without_bars(tmp_path, page):
    """첫 페이지가 전날 봉뿐이거나 비어 있으면 요청 1번으로 빈 목록을 반환한다."""
    fake = FakeToss(on_get=lambda params: page)
    assert make_client(tmp_path, fake).fetch_day("005930", DAY) == []
    assert len(fake.gets()) == 1


def test_fetch_day_raises_after_10_pages(tmp_path):
    """10페이지를 받아도 멈춤 조건에 닿지 않으면 PAGINATION 예외를 낸다."""
    end = datetime(2026, 9, 14, 12, 0, tzinfo=KST)
    fake = FakeToss(on_get=lambda params: ok([candle(end)], end.isoformat()))
    with pytest.raises(TossError) as e:
        make_client(tmp_path, fake).fetch_day("005930", DAY)
    assert e.value.code == "PAGINATION"
    assert len(fake.gets()) == 10


def test_fetch_day_rejects_body_without_candles(tmp_path):
    """200 응답에 result.candles가 없으면 빈 날로 기록되지 않도록 BAD_RESPONSE 예외를 낸다."""
    fake = FakeToss(on_get=lambda params: FakeResponse(None))
    with pytest.raises(TossError) as e:
        make_client(tmp_path, fake).fetch_day("005930", DAY)
    assert e.value.code == "BAD_RESPONSE"
