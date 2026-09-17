// 모의투자 화면: 5초마다 API를 조회해 표 3개, 차트 2개와 경고를 갱신한다.
const REFRESH_MS = 5000;
const STALE_MS = 2 * 60 * 1000;
const REASONS = { signal: "신호", close_time: "15:15 청산", day_end: "장 마감" };

/** 숫자 문자열을 반올림한 원화 표기로 바꾼다. 값이 없으면 "-". */
function won(value) {
  return value == null ? "-" : Math.round(Number(value)).toLocaleString("ko-KR");
}

/** ISO 시각 문자열에서 HH:MM만 뽑는다. 값이 없으면 "-". */
function hm(value) {
  return value ? value.slice(11, 16) : "-";
}

/** 손익 부호에 맞는 셀 클래스 이름을 고른다. */
function sign(value) {
  if (value == null || Number(value) === 0) return "";
  return Number(value) > 0 ? "up" : "down";
}

/** tbody를 행 목록으로 다시 채운다. 각 행은 [글자, 클래스] 쌍의 배열이다. */
function fill(id, rows) {
  const trs = rows.map((cells) => {
    const tr = document.createElement("tr");
    for (const [text, cls] of cells) {
      const td = document.createElement("td");
      td.textContent = text;
      if (cls) td.className = cls;
      tr.append(td);
    }
    return tr;
  });
  document.getElementById(id).replaceChildren(...trs);
}

/** 평일 09:03~15:31이면 true. 이 PC 시계(KST) 기준이며 휴장일은 구분하지 않는다. */
function marketOpen(now) {
  const minutes = now.getHours() * 60 + now.getMinutes();
  const weekday = now.getDay() >= 1 && now.getDay() <= 5;
  return weekday && minutes >= 9 * 60 + 3 && minutes < 15 * 60 + 31;
}

/** API 하나를 JSON으로 조회하고, 오류 응답이면 예외를 던진다. */
async function load(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} ${res.status}`);
  return res.json();
}

const SOURCES = { toss_live: "장중 수신 봉", toss: "수집기 확정 봉" };
let latest = { status: [], trades: [] };

/** 종목 선택 목록을 상태의 종목으로 맞춘다. 고른 종목은 유지한다. */
function fillSymbols(status) {
  const select = document.getElementById("symbol");
  const symbols = status.map((s) => s.symbol);
  if ([...select.options].map((o) => o.value).join() === symbols.join()) return;
  const chosen = select.value;
  select.replaceChildren(...symbols.map((sym) => new Option(sym, sym)));
  if (symbols.includes(chosen)) select.value = chosen;
}

/** 고른 종목의 오늘 1분봉을 받아 그날 체결·보유 진입과 함께 캔들 차트를 그린다. */
async function refreshCandles() {
  const symbol = document.getElementById("symbol").value;
  if (!symbol) return;
  const { source, bars } = await load(`/api/bars?symbol=${encodeURIComponent(symbol)}`);
  const holding = latest.status.find((s) => s.symbol === symbol && s.qty);
  drawCandles(document.getElementById("candles"), bars,
    latest.trades.filter((t) => t.symbol === symbol), holding);
  document.getElementById("bars-source").textContent = source ? SOURCES[source] : "오늘 봉 없음";
}

/** API를 조회해 표·차트·경고·갱신 시각을 바꾼다. 실패하면 연결 끊김을 표시한다. */
async function refresh() {
  const banner = document.getElementById("banner");
  try {
    const [status, trades, daily] = await Promise.all([
      load("/api/status"), load("/api/trades"), load("/api/daily"),
    ]);
    latest = { status, trades };
    fillSymbols(status);
    await refreshCandles();
    drawEquity(document.getElementById("equity"), daily);
    fill("status", status.map((s) => [
      [s.symbol], [s.qty ? `${s.qty}주` : "미보유"], [won(s.entry_price)],
      [won(s.last_close)], [won(s.eval_krw), sign(s.eval_krw)], [hm(s.last_bar_ts)],
    ]));
    fill("trades", trades.map((t) => [
      [t.symbol], [`${t.qty}주`], [hm(t.entry_ts)], [won(t.entry_price)], [hm(t.exit_ts)],
      [won(t.exit_price)], [won(t.pnl_krw), sign(t.pnl_krw)],
      [`${Number(t.return_pct).toFixed(2)}%`, sign(t.return_pct)], [REASONS[t.exit_reason] || t.exit_reason],
    ]));
    fill("daily", daily.map((d) => [
      [d.day], [`${d.trades}건`], [`${d.wins}건`], [won(d.pnl_krw), sign(d.pnl_krw)],
    ]));
    const lastUpdate = Math.max(0, ...status.map((s) => Date.parse(s.updated_at)));
    const stale = marketOpen(new Date()) && Date.now() - lastUpdate > STALE_MS;
    banner.textContent = stale ? "모의투자 프로세스 멈춤 (2분 넘게 갱신 없음)" : "";
    banner.hidden = !stale;
    document.getElementById("updated").textContent = new Date().toLocaleTimeString("ko-KR");
  } catch (err) {
    console.error(err);
    banner.textContent = "연결 끊김: web.py가 실행 중인지 확인하세요";
    banner.hidden = false;
  } finally {
    setTimeout(refresh, REFRESH_MS);
  }
}

// 종목을 바꾸면 다음 갱신을 기다리지 않고 캔들 차트만 다시 그린다
document.getElementById("symbol").addEventListener("change", () => refreshCandles().catch(() => {}));
refresh();
