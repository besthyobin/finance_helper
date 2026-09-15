"""한국투자증권 Open API 클라이언트: 토큰 캐시, 호출 간격 제한, 재시도."""
import json
import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

KST = timezone(timedelta(hours=9))
NETWORK_DELAYS = (1, 2, 4)
RATE_LIMIT_RETRIES = 3


class KisError(Exception):
    """KIS 호출 실패. code에 KIS 오류 코드나 NETWORK/HTTP 구분을 담는다."""

    def __init__(self, code, message=""):
        super().__init__(f"{code} {message}".strip())
        self.code = code


def _json(resp):
    """응답 본문을 dict로 파싱하고, JSON 객체가 아니면 빈 dict를 반환한다."""
    try:
        body = resp.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


class KisClient:
    """KIS REST 호출 담당. send/sleep/clock/now는 테스트에서 대역으로 바꾼다."""

    def __init__(self, app_key, app_secret, base_url, rps, token_path: Path,
                 send=requests.request, sleep=_time.sleep, clock=_time.monotonic,
                 now=lambda: datetime.now(KST)):
        """접속 정보와 대체 가능한 부수효과 함수를 받는다."""
        self.app_key = app_key
        self.app_secret = app_secret
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
        except (OSError, ValueError, KeyError):
            pass

        for attempt in range(2):
            self._throttle()
            resp = self._send(
                "POST", self.base_url + "/oauth2/tokenP",
                headers={"content-type": "application/json"},
                json={"grant_type": "client_credentials",
                      "appkey": self.app_key, "appsecret": self.app_secret},
                timeout=10,
            )
            body = _json(resp)
            if body.get("access_token"):
                expires_at = self._now() + timedelta(seconds=int(body.get("expires_in", 86400)))
                self.token_path.write_text(json.dumps(
                    {"access_token": body["access_token"], "expires_at": expires_at.isoformat()}
                ), encoding="utf-8")
                self._token = body["access_token"]
                return self._token
            if body.get("error_code") == "EGW00133" and attempt == 0:
                self._sleep(60)
                continue
            raise KisError(body.get("error_code") or f"HTTP{resp.status_code}",
                           body.get("error_description", ""))

    def _drop_token(self):
        """메모리와 파일의 토큰 캐시를 지운다."""
        self._token = None
        self.token_path.unlink(missing_ok=True)

    def _headers(self, tr_id):
        """시세 조회 공통 헤더를 만든다."""
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.get_token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    def _get(self, path, tr_id, params):
        """GET 호출. 토큰 만료·호출 한도·네트워크 오류를 재시도하고 정상 응답 본문을 반환한다."""
        network_tries = rate_tries = 0
        token_refreshed = False
        while True:
            headers = self._headers(tr_id)
            self._throttle()
            try:
                resp = self._send("GET", self.base_url + path, headers=headers,
                                  params=params, timeout=10)
            except requests.RequestException as e:
                if network_tries < len(NETWORK_DELAYS):
                    self._sleep(NETWORK_DELAYS[network_tries])
                    network_tries += 1
                    continue
                raise KisError("NETWORK", type(e).__name__) from None

            body = _json(resp)
            if body.get("rt_cd") == "0":
                return body
            code = body.get("msg_cd")
            if code == "EGW00123" and not token_refreshed:
                token_refreshed = True
                self._drop_token()
                continue
            if code == "EGW00201" and rate_tries < RATE_LIMIT_RETRIES:
                rate_tries += 1
                self._sleep(1)
                continue
            if code is None and resp.status_code >= 500 and network_tries < len(NETWORK_DELAYS):
                self._sleep(NETWORK_DELAYS[network_tries])
                network_tries += 1
                continue
            raise KisError(code or f"HTTP{resp.status_code}", body.get("msg1", ""))
