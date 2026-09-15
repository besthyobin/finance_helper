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
