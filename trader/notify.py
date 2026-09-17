"""Gmail 메일 알림 전송."""
import logging
import os
import smtplib
from email.message import EmailMessage

log = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465


def send_mail(text):
    """GMAIL_ADDRESS·GMAIL_APP_PASSWORD로 나에게 메일 1통을 보낸다. 첫 줄이 제목이고, 실패해도 예외를 올리지 않는다."""
    address = os.environ.get("GMAIL_ADDRESS")
    password = os.environ.get("GMAIL_APP_PASSWORD")
    if not address or not password:
        log.warning("메일 설정이 없어 알림을 보내지 않음")
        return
    msg = EmailMessage()
    msg["From"] = msg["To"] = address
    msg["Subject"] = text.splitlines()[0] if text else "(알림)"
    msg.set_content(text)
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=10) as smtp:
            smtp.login(address, password)
            smtp.send_message(msg)
    except Exception as e:
        # 알림 실패가 수집·모의투자를 멈추면 안 되므로 모든 예외를 삼킨다(비ASCII 비밀번호는 UnicodeEncodeError).
        # 예외 문자열에 계정 정보가 섞일 수 있어 종류만 남긴다
        log.error("메일 전송 실패: %s", type(e).__name__)
