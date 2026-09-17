import os
from pathlib import Path

import psycopg
import pytest
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


@pytest.fixture
def conn():
    """테스트 DB에 스키마를 적용하고 테이블을 비운 연결을 제공한다."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.fail("trader/.env에 TEST_DATABASE_URL을 설정하세요")
    with psycopg.connect(url, autocommit=True) as c:
        c.execute((ROOT / "schema.sql").read_text(encoding="utf-8"))
        c.execute("TRUNCATE minute_bars, collect_runs, backtest_runs, paper_status, paper_trades, "
                  "selection_candidates CASCADE")
        yield c
