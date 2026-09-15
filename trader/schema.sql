CREATE TABLE IF NOT EXISTS minute_bars (
    symbol  text        NOT NULL,
    ts      timestamptz NOT NULL,
    open    numeric     NOT NULL,
    high    numeric     NOT NULL,
    low     numeric     NOT NULL,
    close   numeric     NOT NULL,
    volume  bigint      NOT NULL,
    PRIMARY KEY (symbol, ts)
);

CREATE TABLE IF NOT EXISTS collect_runs (
    symbol        text        NOT NULL,
    trade_date    date        NOT NULL,
    bar_count     int         NOT NULL,
    status        text        NOT NULL CHECK (status IN ('ok', 'empty', 'error')),
    error         text,
    collected_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, trade_date)
);
