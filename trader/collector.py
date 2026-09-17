"""토스증권 1분봉 수집기 진입점. 작업 스케줄러가 평일 20:30에 실행하고, 첫 백필은 수동 실행한다."""
import logging
import os
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

import psycopg
from dotenv import load_dotenv

import notify
import store
from bars import KST
from toss import TossAuthError, TossClient, TossError

ROOT = Path(__file__).resolve().parent
HISTORY_DAYS = 1461
TODAY_FROM = time(20, 10)
MAX_CONSECUTIVE_ERRORS = 5
MAX_ALERT_LINES = 20
REQUIRED_ENV = ("TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET", "DATABASE_URL")

log = logging.getLogger("collector")


def read_symbols(path):
    """종목코드 파일을 읽는다. 빈 줄과 # 주석은 무시한다."""
    codes = []
    for line in path.read_text(encoding="utf-8").splitlines():
        code = line.split("#", 1)[0].strip()
        if code:
            codes.append(code)
    return codes


def target_days(now):
    """실행일 1461일 전부터 어제까지의 평일과, 20:10 이후면 오늘(평일일 때)을 최신순으로 반환한다."""
    today = now.date()
    first = today - timedelta(days=HISTORY_DAYS)
    last = today if now.time() >= TODAY_FROM else today - timedelta(days=1)
    days = (last - timedelta(days=i) for i in range((last - first).days + 1))
    return [d for d in days if d.weekday() < 5]


def collect(conn, client, symbols, days, stop=None):
    """종목마다 완료되지 않은 날짜를 최신순으로 수집·기록하고 (종목별 상태 개수, 실패 목록)을 반환한다.
    stop이 주어지면 날짜를 요청하기 전마다 확인해 참이면 남은 날짜·종목을 멈추고 바로 반환한다."""
    counts, failures = {}, []
    for symbol in symbols:
        done = store.done_days(conn, symbol)
        todo = [d for d in days if d not in done]
        c = counts[symbol] = {"ok": 0, "empty": 0, "error": 0, "skipped": 0}
        streak = 0
        for i, day in enumerate(todo):
            if stop and stop():
                c["skipped"] = len(todo) - i
                log.warning("%s 중단 요청, 남은 %d일 건너뜀", symbol, c["skipped"])
                return counts, failures
            if streak >= MAX_CONSECUTIVE_ERRORS:
                c["skipped"] = len(todo) - i
                log.error("%s 연속 오류 %d회, 남은 %d일 건너뜀", symbol, streak, c["skipped"])
                break
            try:
                bars = client.fetch_day(symbol, day)
            except TossAuthError:
                raise
            except Exception as e:
                store.record_run(conn, symbol, day, 0, "error", str(e))
                failures.append((symbol, day, str(e)))
                c["error"] += 1
                streak += 1
                log.error("%s %s error %s", symbol, day, e)
                continue
            if bars:
                store.save_bars(conn, symbol, bars, source="toss")
            status = "ok" if bars else "empty"
            store.record_run(conn, symbol, day, len(bars), status)
            c[status] += 1
            streak = 0
            log.info("%s %s %s %d", symbol, day, status, len(bars))
        log.info("%s ok %d / empty %d / error %d / skipped %d",
                 symbol, c["ok"], c["empty"], c["error"], c["skipped"])
    return counts, failures


def format_alert(run_date, failures):
    """실패 날짜 알림 문구를 만든다. 최대 20줄, 나머지는 개수만 적는다."""
    lines = [f"[수집기] {run_date} 실패 {len(failures)}건"]
    lines += [f"{symbol} {day}: {error}" for symbol, day, error in failures[:MAX_ALERT_LINES]]
    if len(failures) > MAX_ALERT_LINES:
        lines.append(f"외 {len(failures) - MAX_ALERT_LINES}건")
    return "\n".join(lines)


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
    """수집 1회 실행. 날짜 단위 오류만 있으면 0, 설정·DB·인증 등 실행 전체 실패면 1을 반환한다."""
    now = now or datetime.now(KST)
    load_dotenv(ROOT / ".env")
    setup_logging(now.date())
    try:
        missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
        if missing:
            log.error(".env 누락: %s", ", ".join(missing))
            raise RuntimeError("missing env")
        client = TossClient(
            os.environ["TOSS_CLIENT_ID"], os.environ["TOSS_CLIENT_SECRET"],
            os.environ.get("TOSS_BASE_URL") or "https://openapi.tossinvest.com",
            float(os.environ.get("TOSS_RPS") or "15"), ROOT / ".token.json",
        )
        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
            client.get_token()
            counts, failures = collect(conn, client, read_symbols(ROOT / "symbols.txt"), target_days(now))
    except Exception as e:
        # 예외 문자열에 접속 문자열 등이 섞일 수 있어 종류와 토스 오류 코드만 남긴다
        log.error("실행 실패: %s %s", type(e).__name__, e.code if isinstance(e, TossError) else "")
        notify.send_mail(f"[수집기] {now.date()} 실행 실패: {type(e).__name__}")
        return 1
    total = {k: sum(c[k] for c in counts.values()) for k in ("ok", "empty", "error", "skipped")}
    log.info("요약 ok %(ok)d / empty %(empty)d / error %(error)d / skipped %(skipped)d", total)
    if failures:
        notify.send_mail(format_alert(now.date(), failures))
    return 0


if __name__ == "__main__":
    sys.exit(main())
