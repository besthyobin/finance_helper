"""모의투자 로컬 화면 서버. web/의 화면 파일과 DB 조회 JSON API를 127.0.0.1에서 제공한다."""
import json
import os
import sys
from datetime import date, datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import psycopg
from dotenv import load_dotenv

import store
from bars import KST
from paper import COSTS, LIVE_SOURCE

ROOT = Path(__file__).resolve().parent
PORT = 8765
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/charts.js": ("charts.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}


def to_json(value):
    """json.dumps 보조: Decimal은 문자열, 시각은 KST ISO 8601, 날짜는 YYYY-MM-DD로 바꾼다."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(KST).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def with_eval(row):
    """보유 중인 상태 행에 슬리피지 반영 평가손익 eval_krw를 더한다. 미보유면 None."""
    held = row["qty"] and row["last_close"] is not None
    row["eval_krw"] = ((row["last_close"] * (1 - COSTS.slippage) - row["entry_price"]) * row["qty"]
                       if held else None)
    return row


def load_chart_bars(conn, symbol, day):
    """차트용 하루 1분봉: 장중 봉(toss_live)이 있으면 그것을, 없으면 수집기 봉(toss)을 {source, bars}로 반환한다."""
    for source in (LIVE_SOURCE, "toss"):
        bars = store.load_bars(conn, source, day, day, [symbol]).get(symbol)
        if bars:
            return {"source": source, "bars": [vars(b) for b in bars]}
    return {"source": None, "bars": []}


def query_day(query):
    """쿼리의 date(YYYY-MM-DD)를 날짜로 바꾼다. 없으면 오늘, 형식이 틀리면 ValueError."""
    values = query.get("date")
    return date.fromisoformat(values[0]) if values else datetime.now(KST).date()


class Handler(BaseHTTPRequestHandler):
    """GET만 처리한다. 정해진 화면 파일 4개와 API 4개 외에는 404."""

    def do_GET(self):
        """경로에 따라 화면 파일이나 API 응답을 보낸다."""
        url = urlparse(self.path)
        if url.path in STATIC:
            name, content_type = STATIC[url.path]
            return self._send(200, content_type, (ROOT / "web" / name).read_bytes())
        try:
            if url.path == "/api/status":
                return self._json(200, [with_eval(r) for r in self._query(store.load_paper_status)])
            if url.path in ("/api/trades", "/api/bars"):
                query = parse_qs(url.query)
                try:
                    day = query_day(query)
                except ValueError:
                    return self._json(400, {"error": "date는 YYYY-MM-DD"})
                if url.path == "/api/trades":
                    return self._json(200, self._query(store.load_paper_trades, day))
                symbol = query.get("symbol", [""])[0]
                if not symbol:
                    return self._json(400, {"error": "symbol 필요"})
                return self._json(200, self._query(load_chart_bars, symbol, day))
            if url.path == "/api/daily":
                return self._json(200, self._query(store.load_paper_daily))
        except psycopg.Error as e:
            # 예외 문자열에 접속 문자열이 섞일 수 있어 종류만 보낸다
            return self._json(500, {"error": type(e).__name__})
        self._send(404, "text/plain; charset=utf-8", b"not found")

    def log_message(self, format, *args):
        """요청마다 콘솔에 찍는 기본 로그를 끈다(화면이 5초마다 조회한다)."""

    def _query(self, fn, *args):
        """요청마다 DB에 새로 연결해 store 조회 함수를 실행한다. 접속은 최대 3초까지만 기다린다."""
        with psycopg.connect(self.server.db_url, autocommit=True, connect_timeout=3) as conn:
            return fn(conn, *args)

    def _json(self, status, body):
        """body를 JSON으로 보낸다."""
        data = json.dumps(body, default=to_json, ensure_ascii=False).encode("utf-8")
        self._send(status, "application/json; charset=utf-8", data)

    def _send(self, status, content_type, data):
        """상태 코드·Content-Type·본문을 보낸다."""
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def make_server(db_url, port):
    """127.0.0.1:port에 화면 서버를 만든다. port가 0이면 빈 포트를 쓴다."""
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.db_url = db_url
    return server


def main():
    """.env의 DATABASE_URL로 화면 서버를 띄우고 Ctrl+C까지 실행한다."""
    load_dotenv(ROOT / ".env")
    server = make_server(os.environ["DATABASE_URL"], PORT)
    print(f"http://127.0.0.1:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
