"""매일 종목 선정 규칙(순수 계산): 후보 거르기, 위험 필터, 기술지표, 두 구간 백테스트 평가, 순위, 메일 문구."""
import statistics
from decimal import Decimal

MAX_PRICE = Decimal("100000")
MAX_SELECTED = 5
SHORT_RATE_MAX = Decimal("0.10")        # 최근 5일 평균 공매도 거래대금 비중
MARGIN_RATE_MAX = Decimal("0.08")       # 최신 신용융자 잔고율
VOLATILITY_MAX = Decimal("0.05")        # 최근 20일 일간 수익률 표준편차
TURNOVER_MIN = Decimal("10000000000")   # 최근 20일 일평균 거래대금 100억 원
BLOCKING_WARNINGS = {"LIQUIDATION_TRADING", "OVERHEATED", "INVESTMENT_WARNING", "INVESTMENT_RISK"}
MIN_SELECT_TRADES = 20


def candidates(rankings, stocks):
    """순위 종목 중 보통주·활성·10만 원 이하만 순위순으로 남기고, 나머지는 (종목, 사유)로 돌려준다."""
    kept, dropped = [], []
    for r in rankings:
        stock = stocks.get(r["symbol"])
        if stock is None:
            dropped.append((r["symbol"], "종목 정보 없음"))
        elif stock["security_type"] != "STOCK" or not stock["common"]:
            dropped.append((r["symbol"], "보통주 아님"))
        elif not stock["active"]:
            dropped.append((r["symbol"], "상장 상태 아님"))
        elif r["last_price"] > MAX_PRICE:
            dropped.append((r["symbol"], "10만 원 초과"))
        else:
            kept.append({**r, "name": stock["name"]})
    return kept, dropped


def _mean(values):
    """Decimal 목록의 평균."""
    return sum(values, Decimal(0)) / len(values)


def technicals(daily):
    """날짜 오름차순 일봉으로 종가·20·60일 이동평균·20일 수익률·변동성·일평균 거래대금을 계산한다. 모자라면 None."""
    closes = [d.close for d in daily]
    tech = {"close": closes[-1] if closes else None,
            "ma20": _mean(closes[-20:]) if len(closes) >= 20 else None,
            "ma60": _mean(closes[-60:]) if len(closes) >= 60 else None,
            "return_20d": None, "volatility_20d": None, "turnover_20d": None}
    if len(daily) >= 21:
        last = daily[-21:]
        returns = [b.close / a.close - 1 for a, b in zip(last, last[1:])]
        tech["return_20d"] = last[-1].close / last[0].close - 1
        tech["volatility_20d"] = statistics.stdev(returns)
        tech["turnover_20d"] = _mean([d.close * d.volume for d in last[1:]])
    return tech


def short_average(short):
    """최근 5개 공매도 기록 중 값이 있는 거래대금 비중의 평균. 없으면 None."""
    rates = [r["amount_rate"] for r in short[:5] if r["amount_rate"] is not None]
    return _mean(rates) if rates else None


def investor_sums(records):
    """외국인·기관 순매수 주 수를 값 있는 것만 더한다. 모두 없으면 None."""
    def total(key):
        """한 분류의 합."""
        values = [r[key] for r in records if r[key] is not None]
        return sum(values) if values else None
    return {"foreigner": total("foreigner"), "institution": total("institution")}


def _active(warning, today):
    """경고가 today에 적용 중이면 True."""
    return ((warning["start"] is None or warning["start"] <= today)
            and (warning["end"] is None or today <= warning["end"]))


def risk_reason(today, stock, warnings, short, credit, tech):
    """위험 필터를 거래정지·경고·공매도·신용·일봉 부족·변동성·유동성 순으로 검사해 첫 제외 사유를, 없으면 None을 반환한다."""
    if stock["suspended"]:
        return "거래정지"
    if any(w["type"] in BLOCKING_WARNINGS and _active(w, today) for w in warnings):
        return "경고"
    short_avg = short_average(short)
    if short_avg is not None and short_avg >= SHORT_RATE_MAX:
        return "공매도"
    margin = credit[0]["margin_balance_rate"] if credit else None
    if margin is not None and margin >= MARGIN_RATE_MAX:
        return "신용"
    if tech["volatility_20d"] is None or tech["turnover_20d"] is None:
        return "일봉 부족"
    if tech["volatility_20d"] >= VOLATILITY_MAX:
        return "변동성"
    if tech["turnover_20d"] < TURNOVER_MIN:
        return "유동성"
    return None


def split_trades(trades, confirm_from):
    """거래를 청산일 기준으로 (선정 구간, 확인 구간)으로 나눈다. confirm_from 당일부터 확인 구간이다."""
    select = [t for t in trades if t.exit_ts.date() < confirm_from]
    confirm = [t for t in trades if t.exit_ts.date() >= confirm_from]
    return select, confirm


def _stats(trades):
    """거래 수와 평균 수익률(%). 거래가 없으면 평균은 None."""
    return {"trades": len(trades), "avg": _mean([t.return_pct for t in trades]) if trades else None}


def evaluate(select_trades, confirm_trades):
    """두 구간 통계와 탈락 사유(선정 구간 거래 부족·손실, 확인 구간 거래 없음·손실, 통과면 None)를 반환한다."""
    result = {"select": _stats(select_trades), "confirm": _stats(confirm_trades)}
    s, c = result["select"], result["confirm"]
    if s["trades"] < MIN_SELECT_TRADES:
        reason = "선정 구간 거래 부족"
    elif s["avg"] <= 0:
        reason = "선정 구간 손실"
    elif c["trades"] == 0:
        reason = "확인 구간 거래 없음"
    elif c["avg"] <= 0:
        reason = "확인 구간 손실"
    else:
        reason = None
    return result, reason


def rank_selected(passed):
    """확인 구간 평균 내림차순, 거래 수 내림차순, 순위 오름차순으로 정렬해 최대 5개를 고른다."""
    key = lambda p: (-p["backtest"]["confirm"]["avg"], -p["backtest"]["confirm"]["trades"], p["rank"])
    return sorted(passed, key=key)[:MAX_SELECTED]


def _counts(reasons):
    """사유별 개수를 '사유 N, ...'로 쓴다. 개수 내림차순, 같으면 이름순. 비었으면 빈 문자열."""
    items = sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))
    return ", ".join(f"{k} {v}" for k, v in items)


def _with_counts(text, reasons):
    """집계가 있으면 괄호로 붙인다."""
    detail = _counts(reasons)
    return f"{text} ({detail})" if detail else text


def _num(value, fmt):
    """값이 있으면 fmt로, 없으면 '-'."""
    return "-" if value is None else format(value, fmt)


def _position(close, ma):
    """종가가 이동평균 이상이면 '위', 아래면 '아래', 계산 불가면 '-'."""
    if close is None or ma is None:
        return "-"
    return "위" if close >= ma else "아래"


def _stock_line(i, s):
    """선정 종목 한 줄: 두 구간 백테스트, 기술지표, 5일 순매수."""
    t, bt, inv = s["tech"], s["backtest"], s["investor"]
    pct = lambda v, fmt: "-" if v is None else f"{v * 100:{fmt}}%"
    shares = lambda v: "-" if v is None else f"{v:+,}주"
    return (f"{i}. {s['symbol']} {s['name']}  "
            f"확인구간 {bt['confirm']['trades']}건 평균 {_num(bt['confirm']['avg'], '+.2f')}% | "
            f"선정구간 {bt['select']['trades']}건 평균 {_num(bt['select']['avg'], '+.2f')}% | "
            f"종가 {_num(t['close'], ',.0f')} MA20 {_position(t['close'], t['ma20'])} "
            f"MA60 {_position(t['close'], t['ma60'])} 20일 {pct(t['return_20d'], '+.1f')} "
            f"변동성 {pct(t['volatility_20d'], '.1f')} 외국인 5일 {shares(inv['foreigner'])} "
            f"기관 5일 {shares(inv['institution'])}")


def format_mail(run_date, selected, summary, kept=None, reason=None):
    """선정 메일 문구. 첫 줄이 제목이다. reason(선정 없음·시간 초과)이 있으면 전날 종목 유지 제목을 쓴다."""
    if reason:
        title = f"[종목선정] {run_date} {reason}, 전날 종목 유지({', '.join(kept) if kept else '없음'})"
    else:
        title = f"[종목선정] {run_date} 선정 {len(selected)}종목"
    lines = [title] + [_stock_line(i, s) for i, s in enumerate(selected, 1)]
    risk_total = sum(summary["risk"].values())
    lines.append(f"후보 {summary['candidates']} → {_with_counts(f'위험 제외 {risk_total}', summary['risk'])} "
                 f"→ 백테스트 제외 {summary['backtest_rejected']} → 통과 {summary['passed']}")
    lines.append(_with_counts(f"순위 {summary['ranked']} 중 후보 탈락 {sum(summary['dropped'].values())}",
                              summary["dropped"]))
    return "\n".join(lines)
