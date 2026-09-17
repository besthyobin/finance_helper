// 모의투자 차트: ECharts(window.echarts)로 1분봉 캔들·체결 표시와 누적 손익 곡선을 그린다.
const UP = "#d70015";
const DOWN = "#0a5fd6";
let candleChart = null;
let equityChart = null;

/** 1분봉 차트를 한 번만 만든다. 확대 상태를 유지하려고 이후에는 데이터만 바꾼다. */
function initCandleChart(el) {
  candleChart = echarts.init(el);
  candleChart.setOption({
    animation: false,
    tooltip: { trigger: "axis", axisPointer: { type: "cross" } },
    grid: { left: 64, right: 24, top: 24, bottom: 64 },
    xAxis: { type: "category", data: [], boundaryGap: true },
    yAxis: { type: "value", scale: true },
    dataZoom: [{ type: "inside" }, { type: "slider", bottom: 16 }],
    series: [{ type: "candlestick", name: "가격", data: [] }],
  });
  return candleChart;
}

/** 누적 손익 차트를 한 번만 만든다. */
function initEquityChart(el) {
  equityChart = echarts.init(el);
  equityChart.setOption({
    animation: false,
    tooltip: { trigger: "axis" },
    grid: { left: 80, right: 24, top: 24, bottom: 32 },
    xAxis: { type: "category", data: [] },
    yAxis: { type: "value", scale: true },
    series: [{ type: "line", name: "누적 손익(원)", data: [], areaStyle: { opacity: 0.1 } }],
  });
  return equityChart;
}

/** 체결 하나를 캔들 차트 위 표시(▲ 매수 / ▼ 매도)로 바꾼다. */
function marker(kind, ts, price) {
  const buy = kind === "buy";
  return {
    name: buy ? "매수" : "매도",
    coord: [ts.slice(11, 16), Number(price)],
    value: buy ? "매수" : "매도",
    symbol: "triangle",
    symbolSize: 14,
    symbolRotate: buy ? 0 : 180,
    symbolOffset: [0, buy ? 14 : -14],
    itemStyle: { color: buy ? UP : DOWN },
    label: { show: false },
  };
}

/** 한 종목의 하루 봉과 그날 체결·보유 진입으로 캔들 차트를 갱신한다. */
function drawCandles(el, bars, trades, holding) {
  const chart = candleChart || initCandleChart(el);
  const marks = [];
  for (const t of trades) {
    marks.push(marker("buy", t.entry_ts, t.entry_price), marker("sell", t.exit_ts, t.exit_price));
  }
  if (holding) marks.push(marker("buy", holding.entry_ts, holding.entry_price));
  chart.setOption({
    xAxis: { data: bars.map((b) => b.ts.slice(11, 16)) },
    series: [{
      data: bars.map((b) => [Number(b.open), Number(b.close), Number(b.low), Number(b.high)]),
      itemStyle: { color: UP, color0: DOWN, borderColor: UP, borderColor0: DOWN },
      markPoint: { data: marks },
    }],
  });
}

/** 최신순 일별 손익 목록을 날짜 오름차순 누적 합으로 바꿔 선 차트를 갱신한다. */
function drawEquity(el, daily) {
  const chart = equityChart || initEquityChart(el);
  const days = [...daily].reverse();
  let sum = 0;
  const values = days.map((d) => Math.round((sum += Number(d.pnl_krw))));
  chart.setOption({
    xAxis: { data: days.map((d) => d.day) },
    series: [{ data: values, itemStyle: { color: sum >= 0 ? UP : DOWN } }],
  });
}

/** 창 크기가 바뀌면 두 차트 크기를 맞춘다. */
function resizeCharts() {
  if (candleChart) candleChart.resize();
  if (equityChart) equityChart.resize();
}

window.addEventListener("resize", resizeCharts);
