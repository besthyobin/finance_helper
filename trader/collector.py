"""KIS 당일 1분봉 수집기 진입점. 작업 스케줄러가 평일 16:00~23:00 매시 실행한다."""
import logging
import os
import sys
from datetime import datetime, time
from pathlib import Path

import psycopg
from dotenv import load_dotenv

import notify
import store
from kis import KST, KisClient

ROOT = Path(__file__).resolve().parent
START_AFTER = time(15, 35)
FINAL_RUN_FROM = time(23, 0)
REQUIRED_ENV = ("KIS_APP_KEY", "KIS_APP_SECRET", "DATABASE_URL")
MAX_ALERT_LINES = 20

log = logging.getLogger("collector")


def should_run(now):
    """평일 15:35 이후에만 수집한다. 자정 이후 밀린 실행이 장 전 빈 응답을 기록하는 것을 막는다."""
    return now.weekday() < 5 and now.time() >= START_AFTER


def read_symbols(path):
    """종목코드 파일을 읽는다. 빈 줄과 # 주석은 무시한다."""
    codes = []
    for line in path.read_text(encoding="utf-8").splitlines():
        code = line.split("#", 1)[0].strip()
        if code:
            codes.append(code)
    return codes


def collect(conn, client, symbols, today):
    """오늘 완료되지 않은 종목만 수집·기록하고 상태별 개수를 반환한다."""
    done = store.done_symbols(conn, today)
    counts = {"ok": 0, "empty": 0, "error": 0, "skipped": 0}
    for symbol in symbols:
        if symbol in done:
            counts["skipped"] += 1
            continue
        try:
            bars = client.fetch_minute_bars(symbol, today)
            if bars:
                store.save_bars(conn, symbol, bars)
                status = "ok"
            else:
                status = "empty"
            store.record_run(conn, symbol, today, len(bars), status)
            log.info("%s %s %d", symbol, status, len(bars))
        except Exception as e:
            store.record_run(conn, symbol, today, 0, "error", str(e))
            status = "error"
            log.error("%s error %s", symbol, e)
        counts[status] += 1
    return counts


def format_alert(today, failed):
    """실패 종목 알림 문구를 만든다. 최대 20줄, 나머지는 개수만 적는다."""
    lines = [f"[수집기] {today} 실패 {len(failed)}종목"]
    lines += [f"{symbol}: {error}" for symbol, error in failed[:MAX_ALERT_LINES]]
    if len(failed) > MAX_ALERT_LINES:
        lines.append(f"외 {len(failed) - MAX_ALERT_LINES}종목")
    return "\n".join(lines)


def alert_failures(conn, now, send=notify.send_telegram):
    """23:00 이후 실행에서 오늘 실패 종목이 남아 있으면 알림을 보내고 True를 반환한다."""
    if now.time() < FINAL_RUN_FROM:
        return False
    failed = store.failed_runs(conn, now.date())
    if not failed:
        return False
    send(format_alert(now.date(), failed))
    return True


def setup_logging(today):
    """콘솔과 logs/collector-YYYY-MM-DD.log에 로그를 남기도록 설정한다."""
    (ROOT / "logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler(ROOT / "logs" / f"collector-{today}.log", encoding="utf-8")],
    )


def main(now=None):
    """수집 1회 실행. 종목 단위 오류만 있으면 0, 설정·DB·토큰 등 실행 전체 실패면 1을 반환한다."""
    now = now or datetime.now(KST)
    load_dotenv(ROOT / ".env")
    setup_logging(now.date())
    if not should_run(now):
        log.info("수집 시간이 아님: %s", now.isoformat())
        return 0
    try:
        missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
        if missing:
            raise RuntimeError(f".env 누락: {', '.join(missing)}")
        client = KisClient(
            os.environ["KIS_APP_KEY"], os.environ["KIS_APP_SECRET"],
            os.environ.get("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443"),
            float(os.environ.get("KIS_RPS", "15")), ROOT / ".token.json",
        )
        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
            client.get_token()
            counts = collect(conn, client, read_symbols(ROOT / "symbols.txt"), now.date())
            log.info("요약 ok %(ok)d / empty %(empty)d / error %(error)d / skipped %(skipped)d", counts)
            alert_failures(conn, now)
    except Exception as e:
        log.error("실행 실패: %s %s", type(e).__name__, e)
        if now.time() >= FINAL_RUN_FROM:
            notify.send_telegram(f"[수집기] {now.date()} 실행 실패: {type(e).__name__}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
