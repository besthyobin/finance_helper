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

/** 종목명과 종목코드를 함께 표기한다 (예: "삼성전자 (005930)" 또는 "005930"). */
function stockLabel(item) {
  if (!item) return "-";
  const name = item.name;
  const sym = item.symbol;
  return name && name !== sym ? `${name} (${sym})` : sym;
}

const SOURCES = { toss_live: "장중 수신 봉", toss: "수집기 확정 봉" };
let latest = { status: [], trades: [] };

/** 종목 선택 목록을 상태의 종목으로 맞춘다. 고른 종목은 유지한다. */
function fillSymbols(status) {
  const select = document.getElementById("symbol");
  const symbols = status.map((s) => s.symbol);
  if ([...select.options].map((o) => o.value).join() === symbols.join()) return;
  const chosen = select.value;
  select.replaceChildren(...status.map((s) => new Option(stockLabel(s), s.symbol)));
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

/** 계좌 총액 및 자산 요약 카드를 갱신한다. */
function updateAccount(account) {
  if (!account) return;
  document.getElementById("acc-total-assets").textContent = won(account.total_assets);

  const badge = document.getElementById("acc-total-pnl-badge");
  const pnl = Number(account.total_pnl);
  const ret = Number(account.return_pct);
  badge.textContent = `${pnl >= 0 ? "+" : ""}${won(pnl)}원 (${ret >= 0 ? "+" : ""}${ret.toFixed(2)}%)`;
  badge.className = `pnl-badge ${sign(pnl)}`;

  document.getElementById("acc-cash").textContent = `${won(account.cash)}원`;
  document.getElementById("acc-stock-eval").textContent = `${won(account.stock_eval)}원`;
  document.getElementById("acc-capital").textContent = `${won(account.capital)}원`;

  const realPnl = Number(account.realized_pnl);
  const unrealPnl = Number(account.unrealized_pnl);
  document.getElementById("acc-pnl-detail").textContent =
    `실현 ${realPnl >= 0 ? "+" : ""}${won(realPnl)}원 / 평가 ${unrealPnl >= 0 ? "+" : ""}${won(unrealPnl)}원`;
}

/** API를 조회해 표·차트·경고·갱신 시각을 바꾼다. 실패하면 연결 끊김을 표시한다. */
async function refresh() {
  const banner = document.getElementById("banner");
  try {
    const [status, trades, daily, account] = await Promise.all([
      load("/api/status"), load("/api/trades"), load("/api/daily"), load("/api/account"),
    ]);
    latest = { status, trades };
    updateAccount(account);
    fillSymbols(status);
    await refreshCandles();
    drawEquity(document.getElementById("equity"), daily);
    fill("status", status.map((s) => [
      [stockLabel(s)], [s.qty ? `${s.qty}주` : "미보유"], [won(s.entry_price)],
      [won(s.last_close)], [won(s.eval_krw), sign(s.eval_krw)], [hm(s.last_bar_ts)],
    ]));
    fill("trades", trades.map((t) => [
      [stockLabel(t)], [`${t.qty}주`], [hm(t.entry_ts)], [won(t.entry_price)], [hm(t.exit_ts)],
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

/** 매매 정책 정보를 조회하여 화면 요약 및 폼 초기값을 채운다. */
let policyInitialized = false;
async function loadPolicy() {
  try {
    const data = await load("/api/policy");
    const { policy, costs } = data;
    if (policy) {
      document.getElementById("disp-strategy").textContent =
        policy.strategy === "orb" ? "ORB (시초가 돌파)" : policy.strategy;
      document.getElementById("disp-range").textContent = `09:00 ~ ${policy.range_end || "09:30"}`;
      document.getElementById("disp-exit").textContent = `${costs?.exit_at || policy.exit_at || "15:15"} 강제 청산`;
      if (costs) {
        const fee = (Number(costs.fee) * 100).toFixed(3);
        const tax = (Number(costs.tax) * 100).toFixed(2);
        const slip = (Number(costs.slippage) * 100).toFixed(2);
        document.getElementById("disp-costs").textContent = `수수료 ${fee}% · 세금 ${tax}% · 슬리피지 ${slip}%`;
      }
      if (!policyInitialized) {
        document.getElementById("input-stop").value = policy.stop_pct;
        document.getElementById("input-target").value = policy.target_pct;
        policyInitialized = true;
      }
    }
  } catch (err) {
    console.error("매매 정책 로드 실패:", err);
  }
}

/** 정책 설정 폼 제출 처리 */
const policyForm = document.getElementById("policy-form");
let msgTimer = null;
function showPolicyMsg(text, isSuccess) {
  const msg = document.getElementById("policy-msg");
  msg.textContent = text;
  msg.className = `form-msg ${isSuccess ? "success" : "error"}`;
  msg.hidden = false;
  if (msgTimer) clearTimeout(msgTimer);
  msgTimer = setTimeout(() => { msg.hidden = true; }, 4000);
}

policyForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = document.getElementById("btn-save-policy");
  const stopVal = parseFloat(document.getElementById("input-stop").value);
  const targetVal = parseFloat(document.getElementById("input-target").value);

  if (isNaN(stopVal) || isNaN(targetVal)) {
    showPolicyMsg("유효한 숫자를 입력하세요.", false);
    return;
  }

  // 양수로 입력 시 음수로 자동 보정
  const normalizedStop = stopVal > 0 ? -stopVal : stopVal;
  document.getElementById("input-stop").value = normalizedStop;

  btn.disabled = true;
  btn.textContent = "저장 중...";
  try {
    const res = await fetch("/api/policy", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ stop_pct: normalizedStop, target_pct: targetVal }),
    });
    const result = await res.json();
    if (!res.ok || !result.ok) {
      throw new Error(result.error || "정책 저장 실패");
    }
    showPolicyMsg(`설정 완료: 손절 ${result.policy.stop_pct}%, 익절 ${result.policy.target_pct}% (실시간 반영)`, true);
    await loadPolicy();
  } catch (err) {
    showPolicyMsg(`저장 실패: ${err.message}`, false);
  } finally {
    btn.disabled = false;
    btn.textContent = "설정 저장";
  }
});

loadPolicy();
refresh();
