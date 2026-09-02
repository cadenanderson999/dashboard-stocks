"use strict";

const RATING_CLASS = {
  "Strong Buy": "pill-strong-buy",
  Buy: "pill-buy",
  Hold: "pill-hold",
  Sell: "pill-sell",
  "Strong Sell": "pill-strong-sell",
  "No Data": "pill-no-data",
};

const params = new URLSearchParams(location.search);
const SYMBOL = (params.get("symbol") || "").toUpperCase();
let LEAP = null; // this symbol's LEAP candidate record, if any

// --- formatting helpers --------------------------------------------------- //
function fmt(n, digits = 2) {
  return n === null || n === undefined ? "—" : Number(n).toFixed(digits);
}
function fmtPrice(n) { return n === null || n === undefined ? "—" : "$" + fmt(n); }
function fmtMarketCap(n) {
  if (n === null || n === undefined) return "—";
  const a = Math.abs(n);
  if (a >= 1e12) return "$" + (n / 1e12).toFixed(2) + "T";
  if (a >= 1e9) return "$" + (n / 1e9).toFixed(2) + "B";
  if (a >= 1e6) return "$" + (n / 1e6).toFixed(1) + "M";
  return "$" + Number(n).toFixed(0);
}
function fmtVolume(n) {
  if (n === null || n === undefined) return "—";
  const a = Math.abs(n);
  if (a >= 1e9) return (n / 1e9).toFixed(2) + "B";
  if (a >= 1e6) return (n / 1e6).toFixed(2) + "M";
  if (a >= 1e3) return (n / 1e3).toFixed(1) + "K";
  return String(n);
}
function pct(n, digits = 2) {
  return n === null || n === undefined ? "—" : `${n > 0 ? "+" : ""}${fmt(n, digits)}%`;
}
function fmtDate(str) {
  if (!str) return "—";
  const d = new Date(str + "T00:00:00");
  return Number.isNaN(d.getTime())
    ? str : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}
function titleCase(s) {
  return s ? s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()) : "—";
}
function fmtBig(n) {
  if (n === null || n === undefined) return "—";
  return (n < 0 ? "-" : "") + fmtMarketCap(Math.abs(n));
}

// --- load ----------------------------------------------------------------- //
async function fetchJson(path) {
  try {
    const res = await fetch(path, { cache: "no-store" });
    if (!res.ok) return null;
    return await res.json();
  } catch { return null; }
}

async function load() {
  const el = document.getElementById("stock-content");
  if (!SYMBOL) {
    el.innerHTML = `<p class="empty">No ticker specified.</p>`;
    return;
  }

  const [signals, scan, details, leaps] = await Promise.all([
    fetchJson("data/stocks.json"),
    fetchJson("data/rvol_scan.json"),
    fetchJson("data/details.json"),
    fetchJson("data/leaps.json"),
  ]);

  const find = (d) => d && d.stocks && d.stocks.find((s) => s.symbol === SYMBOL);
  const signal = find(signals) || find(scan);
  const detail = details && details.stocks ? details.stocks[SYMBOL] : null;
  LEAP = leaps && leaps.candidates ? leaps.candidates.find((c) => c.symbol === SYMBOL) : null;

  const isSample =
    (signals && signals.is_sample) || (details && details.is_sample);
  if (isSample) document.getElementById("sample-banner").classList.remove("hidden");
  const gen = (signals && signals.generated_at) || (details && details.generated_at);
  if (gen) document.getElementById("updated-at").textContent =
    "updated " + new Date(gen).toLocaleString();

  if (!signal && !detail) {
    el.innerHTML =
      `<p class="empty">No data for <strong>${SYMBOL}</strong>.` +
      ` It may not be in the tracked universe.</p>`;
    return;
  }

  document.title = `${SYMBOL} — Buy Side Signals`;
  render(el, signal || { symbol: SYMBOL }, detail || {});
}

// --- TradingView chart ---------------------------------------------------- //
// Default chart preferences (used when logged out or before prefs load).
const DEFAULT_CHART = {
  interval: "240",
  emas: [9, 20, 200],
  volume: true, vrvp: true, vwap: false, bbands: false, rsi: false, macd: false,
};
const EMA_CHOICES = [9, 20, 50, 100, 200];

function currentPrefs() {
  const saved = (window.Account && Account.ready && Account.getChartPrefs)
    ? Account.getChartPrefs() : null;
  return Object.assign({}, DEFAULT_CHART, saved || {});
}

function buildStudies(p) {
  const s = [];
  (p.emas || []).forEach((n) =>
    s.push({ id: "MAExp@tv-basicstudies", inputs: { length: n } }));
  if (p.volume) s.push("Volume@tv-basicstudies");
  if (p.vwap) s.push("VWAP@tv-basicstudies");
  if (p.bbands) s.push("BB@tv-basicstudies");
  if (p.rsi) s.push("RSI@tv-basicstudies");
  if (p.macd) s.push("MACD@tv-basicstudies");
  if (p.vrvp) s.push("VbPVisible@tv-volumebyprice");
  return s;
}

function tvChartMarkup() {
  return `
    <div class="tv-chart">
      <div class="chart-toolbar">
        <button id="chart-settings-btn" class="chip hidden">⚙ Chart settings</button>
      </div>
      <div class="tradingview-widget-container" id="tv-container">
        <div class="tradingview-widget-container__widget"></div>
        <div class="tradingview-widget-copyright">
          <a href="https://www.tradingview.com/" rel="noopener nofollow" target="_blank">
            Track all markets on TradingView</a>
        </div>
      </div>
    </div>`;
}

function mountTradingView(symbol) {
  const container = document.getElementById("tv-container");
  if (!container) return;
  const p = currentPrefs();
  // Rebuild the container so re-mounting (after a settings change) is clean.
  container.innerHTML =
    `<div class="tradingview-widget-container__widget"></div>` +
    `<div class="tradingview-widget-copyright">` +
    `<a href="https://www.tradingview.com/" rel="noopener nofollow" target="_blank">` +
    `Track all markets on TradingView</a></div>`;
  const config = {
    width: "100%",
    height: 720,
    symbol: symbol.replace(/-/g, "."), // BRK-B -> BRK.B
    interval: p.interval || "240",
    range: "1M",
    timezone: "America/New_York",
    theme: "light",
    style: "1",
    locale: "en",
    hide_side_toolbar: true,
    allow_symbol_change: false,
    save_image: false,
    calendar: false,
    studies: buildStudies(p),
    support_host: "https://www.tradingview.com",
  };
  const script = document.createElement("script");
  script.type = "text/javascript";
  script.async = true;
  script.src =
    "https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js";
  script.text = JSON.stringify(config);
  container.appendChild(script);
}

// --- chart settings panel (per-user, saved to their account) -------------- //
let chartModal = null;
function openChartSettings() {
  const p = currentPrefs();
  if (!chartModal) {
    chartModal = document.createElement("div");
    chartModal.className = "modal-backdrop hidden";
    chartModal.innerHTML = `
      <div class="modal">
        <h3>Chart settings</h3>
        <label class="cs-row">Interval
          <select id="cs-interval">
            <option value="60">1 hour</option>
            <option value="240">4 hour</option>
            <option value="D">1 day</option>
            <option value="W">1 week</option>
          </select>
        </label>
        <div class="cs-group">EMAs
          <div id="cs-emas" class="cs-checks">
            ${EMA_CHOICES.map((n) =>
              `<label><input type="checkbox" value="${n}" data-ema> ${n}</label>`).join("")}
          </div>
        </div>
        <div class="cs-group">Indicators
          <div class="cs-checks">
            <label><input type="checkbox" data-ind="volume"> Volume</label>
            <label><input type="checkbox" data-ind="vwap"> VWAP</label>
            <label><input type="checkbox" data-ind="bbands"> Bollinger</label>
            <label><input type="checkbox" data-ind="rsi"> RSI</label>
            <label><input type="checkbox" data-ind="macd"> MACD</label>
            <label><input type="checkbox" data-ind="vrvp"> Volume Profile</label>
          </div>
        </div>
        <div class="modal-err" id="cs-err"></div>
        <div class="modal-actions">
          <button type="button" class="chip" id="cs-cancel">Cancel</button>
          <button type="button" class="chip active" id="cs-save">Save</button>
        </div>
      </div>`;
    document.body.appendChild(chartModal);
    chartModal.addEventListener("click", (e) => {
      if (e.target === chartModal) chartModal.classList.add("hidden");
    });
    chartModal.querySelector("#cs-cancel").onclick =
      () => chartModal.classList.add("hidden");
    chartModal.querySelector("#cs-save").onclick = saveChartSettings;
  }
  // Populate from current prefs.
  chartModal.querySelector("#cs-interval").value = p.interval || "240";
  chartModal.querySelectorAll("[data-ema]").forEach((c) => {
    c.checked = (p.emas || []).includes(Number(c.value));
  });
  chartModal.querySelectorAll("[data-ind]").forEach((c) => {
    c.checked = !!p[c.dataset.ind];
  });
  chartModal.querySelector("#cs-err").textContent = "";
  chartModal.classList.remove("hidden");
}

async function saveChartSettings() {
  const emas = [...chartModal.querySelectorAll("[data-ema]:checked")]
    .map((c) => Number(c.value));
  const prefs = { interval: chartModal.querySelector("#cs-interval").value, emas };
  chartModal.querySelectorAll("[data-ind]").forEach((c) => {
    prefs[c.dataset.ind] = c.checked;
  });
  const err = chartModal.querySelector("#cs-err");
  err.textContent = "Saving…";
  const { error } = await Account.saveChartPrefs(prefs);
  if (error) { err.textContent = error.message || "Could not save."; return; }
  chartModal.classList.add("hidden");
  mountTradingView(SYMBOL); // re-render with the new settings
}

// --- render --------------------------------------------------------------- //
function statCard(title, rows) {
  const body = rows
    .map(([label, value, cls]) =>
      `<div class="stat"><span class="stat-l">${label}</span>` +
      `<span class="stat-v ${cls || ""}">${value}</span></div>`)
    .join("");
  return `<section class="stat-card"><h3>${title}</h3>${body}</section>`;
}

function targetBar(a, price) {
  const { target_low: lo, target_high: hi, target_mean: mean } = a;
  if (lo == null || hi == null || hi <= lo) return "";
  const pos = (v) => Math.max(0, Math.min(100, ((v - lo) / (hi - lo)) * 100));
  const marks =
    (price != null ? `<div class="tb-mark price" style="left:${pos(price)}%" title="Price ${fmtPrice(price)}"></div>` : "") +
    (mean != null ? `<div class="tb-mark mean" style="left:${pos(mean)}%" title="Mean target ${fmtPrice(mean)}"></div>` : "");
  return `
    <div class="target-bar"><div class="tb-track"></div>${marks}</div>
    <div class="tb-labels"><span>${fmtPrice(lo)}</span><span>${fmtPrice(hi)}</span></div>
    <div class="tb-legend"><span class="dot price"></span>Price
      <span class="dot mean"></span>Mean target</div>`;
}

function analystCard(a, price) {
  a = a || {};
  const hasAny = ["target_mean", "target_high", "target_low", "num_analysts"]
    .some((k) => a[k] != null);
  if (!hasAny) return statCard("Analyst targets", [["Coverage", "No data"]]);

  const upside = (a.target_mean != null && price)
    ? ((a.target_mean - price) / price) * 100 : null;
  const rows = [
    ["Mean target", a.target_mean != null
      ? `${fmtPrice(a.target_mean)}${upside != null ? ` <span class="${upside >= 0 ? "pos" : "neg"}">(${pct(upside, 1)})</span>` : ""}` : "—"],
    ["Median target", fmtPrice(a.target_median)],
    ["High / Low", (a.target_high != null && a.target_low != null)
      ? `${fmtPrice(a.target_high)} / ${fmtPrice(a.target_low)}` : "—"],
    ["Recommendation", a.recommendation
      ? `${titleCase(a.recommendation)}${a.recommendation_mean != null ? ` (${a.recommendation_mean})` : ""}` : "—"],
    ["Analysts", a.num_analysts != null ? a.num_analysts : "—"],
  ];
  const body = rows.map(([l, v]) =>
    `<div class="stat"><span class="stat-l">${l}</span><span class="stat-v">${v}</span></div>`).join("");
  return `<section class="stat-card"><h3>Analyst targets</h3>${body}${targetBar(a, price)}</section>`;
}

function earningsSection(d) {
  const next = d.next_earnings;
  const hist = d.earnings || [];
  if (!next && !hist.length) return "";
  const rows = hist.map((e) => `
    <tr>
      <td>${fmtDate(e.period)}</td>
      <td class="num">${fmtBig(e.revenue)}</td>
      <td class="num">${fmtBig(e.net_income)}</td>
      <td class="num">${e.eps != null ? "$" + fmt(e.eps) : "—"}</td>
    </tr>`).join("");
  return `
    <section class="earnings-section">
      <h3>Earnings${next ? ` · <span class="next-earn">Next report: ${fmtDate(next)}</span>` : ""}</h3>
      ${hist.length ? `<div class="table-scroll"><table class="mini-table">
        <thead><tr><th>Quarter</th><th class="num">Revenue</th>
          <th class="num">Net income</th><th class="num" title="Diluted EPS">EPS</th></tr></thead>
        <tbody>${rows}</tbody></table></div>`
        : `<p class="empty">No earnings history available.</p>`}
    </section>`;
}

// Signed sub-score bar (centre = 0). ``max`` is the bar's half-width.
function scoreBar(label, value, min, max, note) {
  if (value == null) return "";
  const span = Math.max(Math.abs(min), Math.abs(max));
  const w = Math.min(100, Math.abs(value) / span * 50);
  const cls = value > 0 ? "pos" : value < 0 ? "neg" : "";
  const left = value >= 0 ? 50 : 50 - w;
  return `
    <div class="sbar">
      <div class="sbar-l">${label}<span class="muted">${note ? " · " + note : ""}</span></div>
      <div class="sbar-track"><div class="sbar-mid"></div>
        <div class="sbar-fill ${cls}" style="left:${left}%;width:${w}%"></div></div>
      <div class="sbar-v ${cls}">${value > 0 ? "+" : ""}${value}<span class="muted">/${value >= 0 ? max : min}</span></div>
    </div>`;
}

function breakdownCard(s) {
  if (s.score == null) return "";
  const setups = (s.setups || []);
  return `
    <section class="stat-card">
      <h3>Strategy breakdown</h3>
      ${scoreBar("Trend", s.trend_score, -35, 35, "SMA stack, 200-day slope, 52-wk low")}
      ${scoreBar("Momentum", s.momentum_score, -35, 35, "RS rank, 12-1 momentum, highs")}
      ${scoreBar("Timing", s.timing_score, -8, 12, "pullbacks vs bear bounces")}
      ${scoreBar("Volume", s.volume_score, -8, 8, "up/down volume, OBV")}
      <div class="setups">${setups.length
        ? setups.map((x) => `<span class="setup-chip ${SETUP_CLASS[x] || ""}">${x}</span>`).join("")
        : `<span class="muted">No named setup active.</span>`}</div>
    </section>`;
}

const SETUP_CLASS = {
  "Trend Template": "good", "Golden Cross": "good", "Momentum Leader": "good",
  "Pullback Buy": "good", "Breakout": "good", "52-Week High": "good",
  "Accumulation": "good", "MACD Bull Cross": "good",
  "Death Cross": "bad", "Bear Bounce": "bad", "Distribution": "bad",
  "MACD Bear Cross": "bad", "Extended": "warn",
};

function leapCard(s, leap) {
  if (!leap) {
    const why = s.rating && ["Buy", "Strong Buy"].includes(s.rating)
      ? "screens below the LEAP threshold (needs a rising 200-day trend, RS ≥ 60, supportive fundamentals and moderate volatility)"
      : "needs an uptrend and a Buy-or-better rating to qualify";
    return `<section class="stat-card"><h3>LEAP calls</h3>
      <p class="muted small">Not a LEAP candidate — ${why}.
      <a href="leaps.html">See the LEAP Calls page</a>.</p></section>`;
  }
  const cls = leap.leap_rating === "LEAP Buy" ? "pill-strong-buy" : "pill-hold";
  const checks = Object.entries(leap.checks || {}).map(([k, c]) =>
    `<div class="stat"><span class="stat-l">${titleCase(k)} <span class="muted">${c.detail}</span></span>` +
    `<span class="stat-v">${c.points}/${c.max}</span></div>`).join("");
  const rows = (leap.contracts || []).map((c) => `
    <tr>
      <td><strong>${c.role}</strong></td>
      <td data-label="Expiry">${fmtDate(c.expiry)} <span class="muted">(${c.dte}d)</span></td>
      <td class="num" data-label="Strike">$${fmt(c.strike, c.strike % 1 ? 2 : 0)}</td>
      <td class="num" data-label="Mid (bid–ask)">$${fmt(c.mid)} <span class="muted">${fmt(c.bid)}–${fmt(c.ask)}</span></td>
      <td class="num" data-label="Delta">${fmt(c.delta)}</td>
      <td class="num" data-label="IV">${fmt(c.iv, 0)}%</td>
      <td class="num" data-label="Open int.">${c.oi ?? "—"}</td>
      <td class="num" data-label="Breakeven">${pct(c.breakeven_pct, 1)}</td>
      <td class="num" data-label="Leverage">${fmt(c.leverage, 1)}×</td>
    </tr>`).join("");
  return `
    <section class="stat-card leap-card">
      <h3>LEAP calls · <span class="pill ${cls}">${leap.leap_rating}</span>
        <span class="score-num">${leap.leap_score}/100</span></h3>
      ${checks}
      ${rows ? `<div class="table-scroll mini-scroll"><table class="mini-table contracts">
        <thead><tr><th>Style</th><th>Expiry</th><th class="num">Strike</th>
          <th class="num">Mid (bid–ask)</th><th class="num">Δ</th><th class="num">IV</th>
          <th class="num">OI</th><th class="num">Breakeven</th><th class="num">Lev.</th></tr></thead>
        <tbody>${rows}</tbody></table></div>`
        : `<p class="muted small">${leap.chain_status === "no_leaps"
          ? "No expiries ≥ 1 year out are listed for this ticker."
          : leap.chain_status === "not_fetched" ? "Option chain not fetched this run (outside the top candidates)."
          : "Option chain unavailable this run."}</p>`}
      <p class="muted small"><a href="leaps.html#${encodeURIComponent(s.symbol)}">Full LEAP write-up →</a></p>
    </section>`;
}

function render(el, s, d) {
  const chg = s.change_pct;
  const chgCls = chg > 0 ? "pos" : chg < 0 ? "neg" : "";
  const ratingCls = RATING_CLASS[s.rating] || "pill-no-data";
  const trendCls = s.trend === "Bullish" ? "trend-bullish"
    : s.trend === "Bearish" ? "trend-bearish" : "";

  // 52-week position
  let weekPos = "—";
  if (d.week52_low != null && d.week52_high != null && s.price != null) {
    const span = d.week52_high - d.week52_low;
    if (span > 0) weekPos = fmt(((s.price - d.week52_low) / span) * 100, 0) + "% of range";
  }
  const dayRange = (d.day_low != null && d.day_high != null)
    ? `${fmtPrice(d.day_low)} – ${fmtPrice(d.day_high)}` : "—";
  const weekRange = (d.week52_low != null && d.week52_high != null)
    ? `${fmtPrice(d.week52_low)} – ${fmtPrice(d.week52_high)}` : "—";
  const divYield = (d.dividend_rate && s.price)
    ? fmt((d.dividend_rate / s.price) * 100, 2) + "%" : "—";

  el.innerHTML = `
    <div class="stock-head">
      <div>
        <div class="stock-symbol"><span id="detail-star"></span>${s.symbol}</div>
        <div class="stock-name">${s.name || ""}</div>
        <div class="stock-tags">
          ${s.sector ? `<span class="tag">${s.sector}</span>` : ""}
          ${d.industry ? `<span class="tag">${d.industry}</span>` : ""}
          ${(s.lists || []).map((l) => `<span class="tag">${l}</span>`).join("")}
        </div>
        <div class="stock-links">
          <a class="ext-link" target="_blank" rel="noopener"
             href="https://optioncharts.io/options/${encodeURIComponent(s.symbol)}">
            Options chain on OptionCharts ↗</a>
        </div>
      </div>
      <div class="stock-price-box">
        <div class="stock-price">${fmtPrice(s.price)}</div>
        <div class="stock-change ${chgCls}">${pct(chg)}</div>
        <div><span class="pill ${ratingCls}">${s.rating || "No Data"}</span></div>
      </div>
    </div>

    ${tvChartMarkup()}

    <div class="stat-grid">
      ${statCard("Signal", [
        ["Rating", `<span class="pill ${ratingCls}">${s.rating || "No Data"}</span>` +
          (s.score != null ? ` <span class="score-num ${s.score > 0 ? "pos" : s.score < 0 ? "neg" : ""}">${s.score > 0 ? "+" : ""}${s.score}</span>` : "")],
        ["Trend (50 / 200 SMA)", `<span class="${trendCls}">${s.trend || "—"}</span>`],
        ["Trend Template", s.tt_pass != null ? `${s.tt_pass} / 8 criteria` : "—"],
        ["RS rank (1–99)", s.rs_rank != null ? `<span class="${s.rs_rank >= 80 ? "pos" : s.rs_rank <= 20 ? "neg" : ""}">${s.rs_rank}</span>` : "—"],
        ["Timing", s.timing || "—"],
        ["RSI (14) / RSI (2)", `${fmt(s.rsi, 1)} / ${fmt(s.rsi2, 1)}`],
        ["MACD (12,26,9)", s.macd_label ? `${s.macd_label}${s.macd_hist != null ? ` · hist ${fmt(s.macd_hist, 2)}` : ""}` : "—"],
        ["ADX (14)", s.adx != null ? `${fmt(s.adx, 0)} ${s.adx >= 25 ? "· trending" : "· weak trend"}` : "—"],
        ["SMA 50 / 150 / 200", `${fmtPrice(s.sma50)} / ${fmtPrice(s.sma150)} / ${fmtPrice(s.sma200)}` +
          (s.sma200_rising != null ? ` <span class="${s.sma200_rising ? "pos" : "neg"}">(200 ${s.sma200_rising ? "rising" : "falling"})</span>` : "")],
        ["52-wk high / low", `${fmtPrice(s.high52)} / ${fmtPrice(s.low52)}` +
          (s.pct_off_high != null ? ` <span class="muted">(${pct(s.pct_off_high, 1)} from high)</span>` : "")],
        ["ATR (14) · 2×ATR stop", s.atr != null ? `$${fmt(s.atr)} (${fmt(s.atr_pct, 1)}%) · ${fmtPrice(s.stop_2atr)}` : "—"],
        ["Returns 1m / 3m / 6m", `${pct(s.ret_1m, 1)} / ${pct(s.ret_3m, 1)} / ${pct(s.ret_6m, 1)}`],
      ])}
      ${breakdownCard(s)}
      ${leapCard(s, LEAP)}
      ${statCard("Price & range", [
        ["Price", fmtPrice(s.price), chgCls],
        ["Day change", pct(chg), chgCls],
        ["Open", fmtPrice(d.open)],
        ["Previous close", fmtPrice(d.previous_close)],
        ["Day range", dayRange],
        ["52-week range", weekRange],
        ["52-week position", weekPos],
      ])}
      ${statCard("Valuation", [
        ["Market cap", fmtMarketCap(s.market_cap)],
        ["P/E (TTM)", fmt(s.pe, 1)],
        ["Forward P/E", fmt(d.forward_pe, 1)],
        ["Price / book", fmt(d.price_to_book, 2)],
        ["EPS (TTM)", d.eps != null ? "$" + fmt(d.eps) : "—"],
        ["Beta", fmt(d.beta, 2)],
        ["Dividend / yield", d.dividend_rate ? `$${fmt(d.dividend_rate)} · ${divYield}` : "—"],
      ])}
      ${statCard("Volume", [
        ["Up / down volume (50d)", s.udv_ratio != null ? `${fmt(s.udv_ratio)}× <span class="muted">${s.udv_ratio >= 1.3 ? "accumulation" : s.udv_ratio <= 0.77 ? "distribution" : "neutral"}</span>` : "—"],
        ["RVOL today", s.rvol_today != null ? fmt(s.rvol_today) + "×" : "—"],
        ["RVOL (30-day avg)", s.rvol_mean != null ? fmt(s.rvol_mean) + "×" : "—"],
        ["Surge days (30d)", s.rvol_high_days != null ? s.rvol_high_days : "—"],
        ["Avg volume", fmtVolume(d.avg_volume)],
        ["Avg volume (10d)", fmtVolume(d.avg_volume_10d)],
        ["Shares outstanding", fmtVolume(d.shares_outstanding)],
      ])}
      ${analystCard(d.analyst, s.price)}
    </div>

    ${earningsSection(d)}

    ${d.website ? `<p class="reason-note"><a href="${d.website}" target="_blank" rel="noopener">${d.website}</a>${d.country ? ` · ${d.country}` : ""}</p>` : ""}
  `;

  // Mount the TradingView widget into the container just rendered.
  mountTradingView(s.symbol);
  renderDetailStar();
  setupChartSettingsButton();
}

function renderDetailStar() {
  const host = document.getElementById("detail-star");
  if (!host) return;
  if (!(window.Account && Account.ready)) { host.innerHTML = ""; return; }
  const on = Account.isStarred(SYMBOL);
  host.innerHTML = `<button class="star${on ? " on" : ""}" title="Watchlist" ` +
    `aria-label="Toggle watchlist">${on ? "★" : "☆"}</button>`;
  host.querySelector("button").onclick = () => Account.toggleStar(SYMBOL);
}

function setupChartSettingsButton() {
  const btn = document.getElementById("chart-settings-btn");
  if (!btn) return;
  const show = window.Account && Account.ready && Account.isLoggedIn();
  btn.classList.toggle("hidden", !show);
  btn.onclick = openChartSettings;
}

if (window.Account && Account.ready) {
  // Auth changes: refresh the star + show/hide the chart-settings button.
  Account.onChange(() => { renderDetailStar(); setupChartSettingsButton(); });
  // Prefs load/change: re-render the chart with the user's saved indicators.
  Account.onPrefsChange(() => { mountTradingView(SYMBOL); setupChartSettingsButton(); });
}

load();
