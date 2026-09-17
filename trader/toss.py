"""토스증권 Open API 클라이언트: 토큰 캐시, 호출 간격 제한, 재시도, 1분봉·장 운영 시간 조회."""
import json
import time as _time
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import requests

from bars import KST, Bar

NETWORK_DELAYS = (1, 2, 4)
RATE_LIMIT_RETRIES = 3
REISSUE_CODES = {"expired-token", "token-revoked", "invalid-token"}
CANDLES_PATH = "/api/v1/candles"
MARKET_CALENDAR_PATH = "/api/v1/market-calendar/KR"
PAGE_SIZE = 200
MAX_PAGES = 10


class TossError(Exception):
    """토스 호출 실패. code에 토스 error.code 또는 NETWORK·HTTP<status>·PAGINATION·BAD_RESPONSE를 담는다."""

    def __init__(self, code, detail=""):
        """오류 코드와 부가 정보로 예외를 만든다."""
        super().__init__(f"{code} {detail}".strip())
        self.code = code


class TossAuthError(TossError):
    """인증·권한 오류. 날짜 단위로 기록하지 않고 실행 전체를 중단한다."""


def _json(resp):
    """응답 본문을 dict로 파싱하고, JSON 객체가 아니면 빈 dict를 반환한다."""
    try:
        body = resp.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _retry_after(resp):
    """429 응답의 Retry-After 초를 읽는다. 없거나 숫자가 아니면 1초."""
    try:
        return float(resp.headers.get("Retry-After", 1))
    except ValueError:
        return 1


def _to_bar(candle):
    """캔들 한 개를 봉 시작 시각(끝나는 시각 − 1분)·Decimal 가격·int 거래량의 Bar로 바꾼다."""
    end = datetime.fromisoformat(candle["timestamp"]).astimezone(KST)
    return Bar(end - timedelta(minutes=1), Decimal(candle["openPrice"]), Decimal(candle["highPrice"]),
               Decimal(candle["lowPrice"]), Decimal(candle["closePrice"]), int(Decimal(candle["volume"])))


class TossClient:
    """토스 REST 호출 담당. send/sleep/clock/now는 테스트에서 대역으로 바꾼다."""

    def __init__(self, client_id, client_secret, base_url, rps, token_path: Path,
                 send=requests.request, sleep=_time.sleep, clock=_time.monotonic,
                 now=lambda: datetime.now(KST)):
        """접속 정보와 대체 가능한 부수효과 함수를 받는다."""
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = base_url
        self.rps = rps
        self.token_path = token_path
        self._send = send
        self._sleep = sleep
        self._clock = clock
        self._now = now
        self._token = None
        self._last_call = None

    def _throttle(self):
        """직전 호출로부터 1/rps초가 지나도록 대기한다."""
        if self._last_call is not None:
            wait = self._last_call + 1 / self.rps - self._clock()
            if wait > 0:
                self._sleep(wait)
        self._last_call = self._clock()

    def get_token(self):
        """캐시 토큰이 5분 이상 유효하면 재사용하고, 아니면 발급해 파일에 저장한다."""
        if self._token:
            return self._token
        try:
            cached = json.loads(self.token_path.read_text(encoding="utf-8"))
            if datetime.fromisoformat(cached["expires_at"]) > self._now() + timedelta(minutes=5):
                self._token = cached["access_token"]
                return self._token
        except (OSError, ValueError, KeyError, TypeError):
            pass

        rate_tries = 0
        while True:
            self._throttle()
            try:
                resp = self._send(
                    "POST", self.base_url + "/oauth2/token",
                    data={"grant_type": "client_credentials",
                          "client_id": self.client_id, "client_secret": self.client_secret},
                    timeout=10,
                )
            except requests.RequestException as e:
                raise TossError("NETWORK", type(e).__name__) from None
            body = _json(resp)
            if resp.status_code == 200 and body.get("access_token"):
                expires_at = self._now() + timedelta(seconds=int(body.get("expires_in", 86399)))
                self.token_path.write_text(json.dumps(
                    {"access_token": body["access_token"], "expires_at": expires_at.isoformat()}
                ), encoding="utf-8")
                self._token = body["access_token"]
                return self._token
            if resp.status_code == 429 and rate_tries < RATE_LIMIT_RETRIES:
                rate_tries += 1
                self._sleep(_retry_after(resp))
                continue
            error = body.get("error")
            code = error if isinstance(error, str) else f"HTTP{resp.status_code}"
            if resp.status_code in (401, 403):
                raise TossAuthError(code)
            raise TossError(code)

    def _drop_token(self):
        """메모리와 파일의 토큰 캐시를 지운다."""
        self._token = None
        self.token_path.unlink(missing_ok=True)

    def _get(self, path, params):
        """GET 호출. 토큰 만료·호출 한도·네트워크 오류를 재시도하고 정상 응답 본문을 반환한다."""
        network_tries = rate_tries = 0
        token_reissued = False
        while True:
            headers = {"Authorization": f"Bearer {self.get_token()}"}
            self._throttle()
            try:
                resp = self._send("GET", self.base_url + path, headers=headers,
                                  params=params, timeout=10)
            except requests.RequestException as e:
                if network_tries < len(NETWORK_DELAYS):
                    self._sleep(NETWORK_DELAYS[network_tries])
                    network_tries += 1
                    continue
                raise TossError("NETWORK", type(e).__name__) from None

            status = resp.status_code
            if status == 200:
                return _json(resp)
            error = _json(resp).get("error")
            code = error.get("code") if isinstance(error, dict) else None
            if status == 401:
                if code in REISSUE_CODES and not token_reissued:
                    token_reissued = True
                    self._drop_token()
                    continue
                raise TossAuthError(code or "HTTP401")
            if status == 403:
                raise TossAuthError(code or "HTTP403")
            if status == 429:
                if rate_tries < RATE_LIMIT_RETRIES:
                    rate_tries += 1
                    self._sleep(_retry_after(resp))
                    continue
                raise TossError(code or "HTTP429")
            if code is None and status >= 500 and network_tries < len(NETWORK_DELAYS):
                self._sleep(NETWORK_DELAYS[network_tries])
                network_tries += 1
                continue
            raise TossError(code or f"HTTP{status}")

    def fetch_day(self, symbol, day: date):
        """day 하루치 1분봉을 최신순 페이지로 거꾸로 받아 봉 시작 시각 오름차순으로 반환한다."""
        bars = {}
        before = f"{day.isoformat()}T23:59:59+09:00"
        for _ in range(MAX_PAGES):
            body = self._get(CANDLES_PATH, {"symbol": symbol, "interval": "1m", "count": PAGE_SIZE,
                                            "before": before, "adjusted": "false"})
            try:
                candles = body["result"]["candles"]
                next_before = body["result"].get("nextBefore")
            except (KeyError, TypeError, AttributeError):
                raise TossError("BAD_RESPONSE") from None
            reached_prev_day = False
            for c in candles:
                bar = _to_bar(c)
                end_date = (bar.ts + timedelta(minutes=1)).date()
                if end_date < day:
                    reached_prev_day = True
                elif end_date == day:
                    bars[bar.ts] = bar
            if reached_prev_day or not candles or not next_before:
                break
            before = next_before
        else:
            raise TossError("PAGINATION")
        return [bars[ts] for ts in sorted(bars)]

    def fetch_recent(self, symbol, count):
        """최근 1분봉 count개를 받아 클라이언트 시계 기준 끝난 봉만 시작 시각 오름차순으로 반환한다."""
        body = self._get(CANDLES_PATH, {"symbol": symbol, "interval": "1m", "count": count,
                                        "adjusted": "false"})
        try:
            bars = [_to_bar(c) for c in body["result"]["candles"]]
        except (KeyError, TypeError, AttributeError):
            raise TossError("BAD_RESPONSE") from None
        now = self._now()
        return sorted((b for b in bars if b.ts + timedelta(minutes=1) <= now), key=lambda b: b.ts)

    def market_hours(self, day: date):
        """day의 정규장 (시작, 종료) KST 시각을 반환한다. 휴장이면 None."""
        body = self._get(MARKET_CALENDAR_PATH, {"date": day.isoformat()})
        try:
            today = body["result"]["today"]
            regular = (today["integrated"] or {}).get("regularMarket")
            if today["date"] != day.isoformat() or not regular:
                return None
            return (datetime.fromisoformat(regular["startTime"]).astimezone(KST),
                    datetime.fromisoformat(regular["endTime"]).astimezone(KST))
        except (KeyError, TypeError, AttributeError, ValueError):
            raise TossError("BAD_RESPONSE") from None
