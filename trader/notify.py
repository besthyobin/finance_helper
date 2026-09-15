"""텔레그램 알림 전송."""
import logging
import os

import requests

log = logging.getLogger(__name__)


def send_telegram(text):
    """TELEGRAM_BOT_TOKEN·TELEGRAM_CHAT_ID로 메시지 1통을 보낸다. 실패해도 예외를 올리지 않는다."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.warning("텔레그램 설정이 없어 알림을 보내지 않음")
        return
    try:
        resp = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                             json={"chat_id": chat_id, "text": text}, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        # 예외 문자열에 토큰이 든 URL이 포함되므로 종류만 남긴다
        log.error("텔레그램 전송 실패: %s", type(e).__name__)
