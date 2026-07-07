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

  const [signals, scan, details] = await Promise.all([
    fetchJson("data/stocks.json"),
    fetchJson("data/rvol_scan.json"),
    fetchJson("data/details.json"),
  ]);

  const find = (d) => d && d.stocks && d.stocks.find((s) => s.symbol === SYMBOL);
  const signal = find(signals) || find(scan);
  const detail = details && details.stocks ? details.stocks[SYMBOL] : null;

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

// --- price chart (inline SVG) --------------------------------------------- //
function priceChart(closes) {
  if (!closes || closes.length < 2) return "";
  const w = 900, h = 240, pad = 10;
  const min = Math.min(...closes), max = Math.max(...closes);
  const range = (max - min) || 1;
  const n = closes.length;
  const x = (i) => pad + (i * (w - 2 * pad)) / (n - 1);
  const y = (v) => pad + (1 - (v - min) / range) * (h - 2 * pad);
  const line = closes.map((c, i) => `${x(i).toFixed(1)},${y(c).toFixed(1)}`).join(" ");
  const up = closes[n - 1] >= closes[0];
  const stroke = up ? "var(--green)" : "var(--red)";
  const fill = up ? "rgba(79,157,86,0.12)" : "rgba(207,90,68,0.12)";
  const area = `${pad},${h - pad} ${line} ${w - pad},${h - pad}`;
  return `
    <div class="chart-wrap">
      <div class="chart-caption">Last ${n} trading days</div>
      <svg class="price-chart" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"
           role="img" aria-label="Price history for ${SYMBOL}">
        <polyline points="${area}" fill="${fill}" stroke="none"/>
        <polyline points="${line}" fill="none" stroke="${stroke}"
                  stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
      </svg>
    </div>`;
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
        <div class="stock-symbol">${s.symbol}</div>
        <div class="stock-name">${s.name || ""}</div>
        <div class="stock-tags">
          ${s.sector ? `<span class="tag">${s.sector}</span>` : ""}
          ${d.industry ? `<span class="tag">${d.industry}</span>` : ""}
          ${(s.lists || []).map((l) => `<span class="tag">${l}</span>`).join("")}
        </div>
      </div>
      <div class="stock-price-box">
        <div class="stock-price">${fmtPrice(s.price)}</div>
        <div class="stock-change ${chgCls}">${pct(chg)}</div>
        <div><span class="pill ${ratingCls}">${s.rating || "No Data"}</span></div>
      </div>
    </div>

    ${priceChart(d.closes)}

    <div class="stat-grid">
      ${statCard("Signal", [
        ["Rating", `<span class="pill ${ratingCls}">${s.rating || "No Data"}</span>`],
        ["Trend (EMA 50 / 200)", `<span class="${trendCls}">${s.trend || "—"}</span>`],
        ["RSI (14)", fmt(s.rsi, 1)],
        ["Momentum", s.momentum || "—"],
        ["EMA 50", fmtPrice(s.ema50)],
        ["EMA 200", fmtPrice(s.ema200)],
      ])}
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
        ["RVOL today", s.rvol_today != null ? fmt(s.rvol_today) + "×" : "—"],
        ["RVOL (30-day avg)", s.rvol_mean != null ? fmt(s.rvol_mean) + "×" : "—"],
        ["Surge days (30d)", s.rvol_high_days != null ? s.rvol_high_days : "—"],
        ["Avg volume", fmtVolume(d.avg_volume)],
        ["Avg volume (10d)", fmtVolume(d.avg_volume_10d)],
        ["Shares outstanding", fmtVolume(d.shares_outstanding)],
      ])}
    </div>

    ${s.reason ? `<p class="reason-note"><strong>Why this rating:</strong> ${s.reason}</p>` : ""}
    ${d.website ? `<p class="reason-note"><a href="${d.website}" target="_blank" rel="noopener">${d.website}</a>${d.country ? ` · ${d.country}` : ""}</p>` : ""}
  `;
}

load();
