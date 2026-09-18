CREATE TABLE IF NOT EXISTS minute_bars (
    source  text        NOT NULL DEFAULT 'kis',
    symbol  text        NOT NULL,
    ts      timestamptz NOT NULL,
    open    numeric     NOT NULL,
    high    numeric     NOT NULL,
    low     numeric     NOT NULL,
    close   numeric     NOT NULL,
    volume  bigint      NOT NULL,
    PRIMARY KEY (source, symbol, ts)
);

-- 수집기 초기 버전 테이블(source 없음)을 데이터 보존하며 이전한다
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'minute_bars' AND column_name = 'source'
    ) THEN
        ALTER TABLE minute_bars ADD COLUMN source text NOT NULL DEFAULT 'kis';
        ALTER TABLE minute_bars DROP CONSTRAINT minute_bars_pkey;
        ALTER TABLE minute_bars ADD PRIMARY KEY (source, symbol, ts);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS collect_runs (
    symbol        text        NOT NULL,
    trade_date    date        NOT NULL,
    bar_count     int         NOT NULL,
    status        text        NOT NULL CHECK (status IN ('ok', 'empty', 'error')),
    error         text,
    collected_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, trade_date)
);

CREATE TABLE IF NOT EXISTS backtest_runs (
    id          bigserial   PRIMARY KEY,
    created_at  timestamptz NOT NULL DEFAULT now(),
    strategy    text        NOT NULL,
    params      jsonb       NOT NULL,   -- 기본값에 --param을 덮어쓴 최종값
    source      text        NOT NULL,
    symbols     text[]      NOT NULL,   -- 실제로 봉이 있어 백테스트한 종목
    date_from   date        NOT NULL,
    date_to     date        NOT NULL,
    costs       jsonb       NOT NULL    -- {"fee":"0.00015","tax":"0.002","slippage":"0.0005","exit_at":"15:15"}
);

CREATE TABLE IF NOT EXISTS backtest_trades (
    run_id       bigint      NOT NULL REFERENCES backtest_runs(id) ON DELETE CASCADE,
    symbol       text        NOT NULL,
    entry_ts     timestamptz NOT NULL,  -- 체결 봉 시각
    entry_price  numeric     NOT NULL,  -- 슬리피지 반영 매수가
    exit_ts      timestamptz NOT NULL,
    exit_price   numeric     NOT NULL,  -- 슬리피지 반영 매도가
    return_pct   numeric     NOT NULL,  -- 비용 반영 수익률(%)
    exit_reason  text        NOT NULL CHECK (exit_reason IN ('signal', 'close_time', 'day_end')),
    PRIMARY KEY (run_id, symbol, entry_ts)
);

-- 종목별 모의투자 현재 상태. paper.py가 새 봉을 처리할 때마다 덮어쓴다
CREATE TABLE IF NOT EXISTS paper_status (
    symbol       text        PRIMARY KEY,
    trade_date   date        NOT NULL,
    last_bar_ts  timestamptz,           -- 마지막으로 받은 봉 시작 시각
    last_close   numeric,
    qty          int         NOT NULL,  -- 0이면 미보유
    entry_ts     timestamptz,           -- 진입 체결 봉 시각
    entry_price  numeric,               -- 슬리피지 반영 매수가
    updated_at   timestamptz NOT NULL DEFAULT now()
);

-- 완결된 모의 거래. 재시작 따라잡기로 같은 거래가 다시 오면 덮어쓴다
CREATE TABLE IF NOT EXISTS paper_trades (
    symbol       text        NOT NULL,
    strategy     text        NOT NULL,
    entry_ts     timestamptz NOT NULL,
    qty          int         NOT NULL,
    entry_price  numeric     NOT NULL,  -- 슬리피지 반영 매수가
    exit_ts      timestamptz NOT NULL,
    exit_price   numeric     NOT NULL,  -- 슬리피지 반영 매도가
    pnl_krw      numeric     NOT NULL,  -- qty × (exit_price × (1 − fee − tax) − entry_price × (1 + fee))
    return_pct   numeric     NOT NULL,  -- 비용 반영 수익률(%)
    exit_reason  text        NOT NULL CHECK (exit_reason IN ('signal', 'close_time', 'day_end')),
    PRIMARY KEY (symbol, entry_ts)
);

-- 매일 종목 선정 결과. 같은 날 재실행하면 덮어쓴다
CREATE TABLE IF NOT EXISTS selection_candidates (
    run_date   date    NOT NULL,
    symbol     text    NOT NULL,
    name       text    NOT NULL,
    rank       int     NOT NULL,          -- 거래대금 1년 순위
    status     text    NOT NULL CHECK (status IN ('selected', 'passed', 'rejected')),
    reason     text,                      -- rejected일 때 제외 사유
    metrics    jsonb   NOT NULL,          -- 위험 지표, 기술지표, 두 구간 백테스트 수치
    PRIMARY KEY (run_date, symbol)
);

-- 수정주가 일봉. 수집할 때마다 전체 기간을 다시 받아 덮어쓴다(분할·배당으로 과거 값이 바뀜)
CREATE TABLE IF NOT EXISTS daily_bars (
    symbol  text    NOT NULL,
    day     date    NOT NULL,
    open    numeric NOT NULL,
    high    numeric NOT NULL,
    low     numeric NOT NULL,
    close   numeric NOT NULL,
    volume  bigint  NOT NULL,
    PRIMARY KEY (symbol, day)
);
